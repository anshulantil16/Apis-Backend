"""Where Market Watch's numbers come from - live, every time.

Prices: Flipkart's search page. It embeds the listing data it renders
(brand, title, pack size, MRP, selling price, image, product link) as
JSON, so the figures are the ones a shopper sees at that moment.

The other stores the brief named were tried and do not answer an automated
request: Amazon (a bot check), Blinkit (403), Zepto (an empty page), BigBasket
(Access Denied) and JioMart (a page that only fills in with JavaScript). They are listed as unavailable
rather than guessed at. Their official routes - the Amazon Product
Advertising API, Flipkart's Affiliate API - need a registered account and
are what this should move to before it is used beyond testing.

News: Google News RSS, which links every item to its original article.

Nothing here invents a value. A field the listing does not carry is left
empty, and a product that cannot be found is reported as not found.
"""
import json
import re
import time
from datetime import datetime, timedelta, timezone as dt_tz
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus
from xml.etree import ElementTree

import requests

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
TIMEOUT = 20

# The products compared. For each: the brands (APIS first) and the search
# that finds each one's product; the pack sizes compared; what a listing's
# title must say to count (`must`), and what makes it some other product -
# a combo, a snack made with it - (`skip`).
PRODUCTS = {
    'honey': {
        'label': 'Honey', 'noun': 'honey', 'sizes': (250, 500, 1000),
        'brands': [('APIS', 'apis himalaya honey'), ('Dabur', 'dabur honey'),
                   ('Patanjali', 'patanjali honey'), ('Saffola', 'saffola honey'),
                   ('Zandu', 'zandu honey')],
        'must': r'honey',
        'skip': r'\bcombo\b|\+|churna|\bwith\b.*\b(oil|ghee|tea|chyawan)',
    },
    'dates': {
        'label': 'Dates', 'noun': 'dates', 'sizes': (250, 500, 1000),
        'brands': [('APIS', 'apis dates'), ('Aladdin', 'aladdin dates'), ('Nutraj', 'nutraj dates')],
        'must': r'\bdates?\b|khaj',
        'skip': r'\bcombo\b|\+|raisin|kishmish|munakka|choco|\bbites?\b|almond|cashew|walnut|\bseeds?\b',
    },
}
DEFAULT_PRODUCT = 'honey'
# Honey's brands, for older callers that knew only honey.
BRANDS = PRODUCTS['honey']['brands']


def brands(product):
    return PRODUCTS[product]['brands']

# Every store the comparison is meant to cover, and whether it is read today.
# Only stores that answer a plain request are read; the rest need their
# official partner access first and are shown as "not connected" - never
# filled in with a guess and never scraped past their bot protection.
STORES = [
    {'store': 'Flipkart', 'live': True, 'reason': 'Live - read from Flipkart search'},
    {'store': 'Amazon', 'live': False, 'reason': 'Not connected - needs the Amazon Product Advertising API'},
    {'store': 'Blinkit', 'live': False, 'reason': 'Not connected - blocks automated requests, no public API'},
    {'store': 'Zepto', 'live': False, 'reason': 'Not connected - blocks automated requests, no public API'},
    {'store': 'BigBasket', 'live': False, 'reason': 'Not connected - blocks automated requests (Access Denied)'},
    {'store': 'JioMart', 'live': False, 'reason': 'Not connected - prices load only inside the browser'},
]
UNAVAILABLE_STORES = [s for s in STORES if not s['live']]

# (topic, search, how many days back is still news). Company and competitor
# stories are rarer than market ones, so they are looked for further back.
NEWS_QUERIES = [
    ('APIS', '"Apis India"', 365),
    ('Competitors', 'Dabur honey', 180),
    ('Competitors', 'Patanjali honey', 180),
    ('Competitors', 'Saffola honey', 180),
    ('Competitors', 'Zandu honey', 180),
    ('Honey market', 'honey market India', 60),
    ('FMCG', 'FMCG India', 7),
]
# Most per topic, so a busy week of FMCG headlines cannot push the company's
# and competitors' own news off the list.
PER_TOPIC = {'APIS': 4, 'Competitors': 6, 'Honey market': 3, 'FMCG': 5}
# Topics whose headline has to actually be about honey to count.
HONEY_TOPICS = {'Competitors', 'Honey market'}
# "Honey" is also a crypto token, a grade of brass scrap and a singer.
OFF_TOPIC = re.compile(r'binance|crypto|\btoken\b|\bcoin\b|hivemapper|live price|price chart|'
                       r'\bscrap\b|brass|share price|stock price|honey singh|honeymoon', re.I)


def _get(url, tries=2):
    """One retry: a store that is slow once is usually fine a moment later."""
    for attempt in range(tries):
        try:
            r = requests.get(url, headers={'User-Agent': UA, 'Accept-Language': 'en-IN,en;q=0.9'},
                             timeout=TIMEOUT)
            r.raise_for_status()
            return r.text
        except requests.RequestException:
            if attempt == tries - 1:
                raise
            time.sleep(2)


# ── Flipkart ───────────────────────────────────────────────────────────────

def _state(html):
    m = re.search(r'window\.__INITIAL_STATE__\s*=\s*', html)
    if not m:
        raise ValueError('the page carried no listing data')
    state, _ = json.JSONDecoder().raw_decode(html[m.end():])
    return state


def _products(node, out):
    """Every listing object in Flipkart's page state."""
    if isinstance(node, dict):
        if isinstance(node.get('pricing'), dict) and isinstance(node.get('titles'), dict):
            out.append(node)
            return
        for v in node.values():
            _products(v, out)
    elif isinstance(node, list):
        for v in node:
            _products(v, out)


_UNIT = r'(\d+(?:\.\d+)?)\s*(kg|gms|gm|grams|gram|g)\b'


def _grams(text):
    """'500 g', '1 kg', '1kg' -> grams. None for anything else."""
    m = re.search(_UNIT, (text or '').lower())
    if not m:
        return None
    n = float(m.group(1))
    return int(round(n * 1000 if m.group(2) == 'kg' else n))


def _size(g):
    return f'{g // 1000} kg' if g % 1000 == 0 else f'{g} g'


def _pack(title, subtitle):
    """-> (total grams, label) for what one listing actually sells.

    A "Pack of 2 x 225g" is 450 g for the one price, and comparing it as
    225 g would make it look half the price it is.
    """
    t = f'{title} {subtitle or ""}'.lower()
    m = re.search(r'(\d+)\s*x\s*' + _UNIT, t)
    if m:
        unit = _grams(f'{m.group(2)} {m.group(3)}')
        n = int(m.group(1))
        if not unit:
            return None, ''
        return n * unit, (f'{n} × {_size(unit)}' if n > 1 else _size(unit))
    unit = _grams(subtitle) or _grams(title)
    if not unit:
        return None, ''
    m = re.search(r'pack of\s*(\d+)', t)
    n = int(m.group(1)) if m else 1
    return n * unit, (f'{n} × {_size(unit)}' if n > 1 else _size(unit))


def _same_maker(maker, brand):
    """Flipkart's maker name is the brand, sometimes with more after it:
    "Aladdin Dates" is Aladdin. "APICURE" is not APIS, so a whole word."""
    m, b = maker.strip().lower(), brand.lower()
    return m == b or m.startswith(b + ' ')


def flipkart(brand, query, product=DEFAULT_PRODUCT):
    """-> the brand's listings of `product`, in Flipkart's own order."""
    spec = PRODUCTS[product]
    html = _get(f'https://www.flipkart.com/search?q={quote_plus(query)}')
    raw = []
    _products(_state(html), raw)
    out, seen = [], set()
    for p in raw:
        t = p.get('titles') or {}
        maker = (t.get('superTitle') or '').strip()
        title = (t.get('title') or t.get('newTitle') or '').strip()
        # Exact maker: "APICURE" and "APERA" also come back for "apis".
        if not _same_maker(maker, brand) or not re.search(spec['must'], title.lower()):
            continue
        if re.search(spec['skip'], title.lower()):
            continue    # a bundle, or something else made with it, is not its price
        grams, label = _pack(title, t.get('subtitle'))
        prices = (p.get('pricing') or {}).get('prices') or []
        sell = next((x.get('value') for x in prices if not x.get('strikeOff')), None)
        mrp = next((x.get('value') for x in prices if x.get('strikeOff')), None) or sell
        pid = p.get('id') or ''
        if not grams or not sell or pid in seen:
            continue
        seen.add(pid)
        images = ((p.get('media') or {}).get('images') or [])
        image = ''
        if images:
            image = (images[0].get('url') or '').replace('{@width}', '312').replace('{@height}', '312') \
                .replace('{@quality}', '70').replace('http://', 'https://')
        avail = json.dumps(p.get('availability') or {}).upper()
        out.append({
            'source': 'Flipkart', 'brand': brand, 'title': title, 'product_id': pid,
            'pack_grams': grams, 'pack_label': label, 'mrp': float(mrp), 'price': float(sell),
            'url': 'https://www.flipkart.com' + (p.get('baseUrl') or ''),
            'image': image,
            'in_stock': 'OUT_OF_STOCK' not in avail and 'SOLD_OUT' not in avail,
        })
    return out


def _find(node, test):
    """The first dict anywhere in `node` that passes `test`, or None."""
    if isinstance(node, dict):
        if test(node):
            return node
        node = node.values()
    if isinstance(node, (list, type({}.values()))):
        for v in node:
            hit = _find(v, test)
            if hit is not None:
                return hit
    return None


def product_page(known):
    """Today's price for one product, read off its own Flipkart page.

    `known` is what an earlier day's search recorded about it - its link, and
    the pack it sells, which does not change. The price, MRP and stock are
    read now, from the page, exactly as a shopper opening it would see them.
    None if the page no longer shows a price.
    """
    state = _state(_get(known['url']))
    ppd = _find(state, lambda d: 'finalPrice' in d and 'mrp' in d)
    if not ppd or not ppd.get('finalPrice'):
        return None
    pls = _find(state, lambda d: 'isAvailable' in d) or {}
    offers = _find(state, lambda d: isinstance(d.get('availability'), str) and 'schema.org' in d['availability'])
    in_stock = bool(pls.get('isAvailable', True))
    if offers:
        in_stock = in_stock and offers['availability'].endswith('InStock')
    return {**{k: known[k] for k in ('brand', 'title', 'product_id', 'pack_grams', 'pack_label', 'url', 'image')},
            'source': 'Flipkart', 'mrp': float(ppd.get('mrp') or ppd['finalPrice']),
            'price': float(ppd['finalPrice']), 'in_stock': in_stock}


def all_prices(product=DEFAULT_PRODUCT, known=None):
    """-> (listings, per-brand status). One brand failing does not stop the rest.

    Flipkart's search does not always show every brand: some days a brand's
    honey is simply not in the results for its own name, although the
    products are still on sale. When the search finds nothing for a brand,
    `known` - {brand: [products found on earlier days]} - is used to open
    those products' own pages and read today's price there instead. A price
    is still only ever one read today; nothing is carried over.
    """
    listings, status = [], []
    for i, (brand, query) in enumerate(brands(product)):
        if i:
            time.sleep(1.2)     # one shopper's pace, not a burst
        try:
            found = flipkart(brand, query, product)
            via = 'search'
            if not found and (known or {}).get(brand):
                via = 'product pages'
                for k in known[brand]:
                    time.sleep(1.2)
                    try:
                        hit = product_page(k)
                    except Exception:      # one page failing does not stop the others
                        hit = None
                    if hit:
                        found.append(hit)
            listings += found
            status.append({'brand': brand, 'ok': True, 'count': len(found), 'via': via})
        except Exception as e:     # a timeout, a block, a changed page
            status.append({'brand': brand, 'ok': False, 'count': 0, 'error': str(e)[:160]})
    return listings, status


# ── News ───────────────────────────────────────────────────────────────────

def news(limit=18):
    """-> the latest items across NEWS_QUERIES, newest first, each with its link."""
    items, seen, errors = [], set(), []
    for topic, q, days in NEWS_QUERIES:
        cutoff = datetime.now(dt_tz.utc) - timedelta(days=days)
        try:
            root = ElementTree.fromstring(
                _get(f'https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en'))
        except Exception as e:
            errors.append(f'{topic}: {str(e)[:120]}')
            continue
        for it in root.iter('item'):
            title = (it.findtext('title') or '').strip()
            src = it.find('source')
            publisher = (src.text or '').strip() if src is not None else ''
            if publisher and title.endswith(f' - {publisher}'):
                title = title[: -len(publisher) - 3]
            key = re.sub(r'\W+', ' ', title.lower()).strip()
            if not title or key in seen or OFF_TOPIC.search(f'{title} {publisher}'):
                continue
            if topic in HONEY_TOPICS and 'honey' not in title.lower():
                continue
            try:
                when = parsedate_to_datetime(it.findtext('pubDate') or '')
            except (TypeError, ValueError):
                continue
            if when < cutoff:
                continue
            seen.add(key)
            items.append({'topic': topic, 'title': title, 'publisher': publisher,
                          'publisher_url': src.get('url', '') if src is not None else '',
                          'url': (it.findtext('link') or '').strip(),
                          'published': when.isoformat()})
    items.sort(key=lambda x: x['published'], reverse=True)
    kept, per = [], {}
    for it in items:
        if per.get(it['topic'], 0) < PER_TOPIC.get(it['topic'], 4):
            per[it['topic']] = per.get(it['topic'], 0) + 1
            kept.append(it)
    return kept[:limit], errors
