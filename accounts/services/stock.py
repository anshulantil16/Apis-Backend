"""The APIS India share price, for the ticker across the top of the dashboard.

The banner used to carry a price typed into the source by hand. APIS is
listed, so that number was wrong every day after the one it was written on,
and wrong in a way that looks authoritative.

Cached rather than fetched per visitor: ~660 people load this dashboard, and
an intranet banner does not need a live tick. It needs to be right today.

A stale price is better than a blank banner, so the last good reading is kept
for a day and served if the upstream call fails - labelled as of when it was
taken, never presented as current.
"""
import logging

import requests
from django.core.cache import cache

log = logging.getLogger(__name__)

# APIS India Limited on the BSE. Confirmed via Yahoo's own symbol search:
# "APISINDIA.BO" - the obvious guess - is not a symbol and 404s.
SYMBOL = 'APIS.BO'
QUOTE_URL = f'https://query1.finance.yahoo.com/v8/finance/chart/{SYMBOL}'

FRESH_KEY = 'bse_ticker:fresh'
LAST_GOOD_KEY = 'bse_ticker:last_good'
FRESH_TTL = 15 * 60          # a quarter hour; the banner is not a trading screen
LAST_GOOD_TTL = 7 * 24 * 60 * 60

# Yahoo answers a default python-requests User-Agent with a challenge page
# rather than JSON, so this is required, not decoration.
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; APIS-Intranet/1.0)'}

TAGLINE = ('We here at AIL keep quality on top preference as we believe your '
           'trust is our presence..')


def _fetch():
    """One reading from upstream, or None if anything at all went wrong.

    Deliberately total: this feeds a decorative banner, and no failure here is
    worth a 500 on the dashboard everyone lands on.
    """
    # Catches everything on purpose. A narrower list would still be a list of
    # the failures we thought of, and the one we did not think of would take
    # down the page all 660 people land on - to save a share price nobody
    # opened the dashboard for. The caller degrades cleanly either way.
    try:
        r = requests.get(QUOTE_URL, params={'interval': '1d', 'range': '5d'},
                         headers=HEADERS, timeout=10)
        if r.status_code != 200:
            log.warning('BSE ticker: HTTP %s for %s', r.status_code, SYMBOL)
            return None
        meta = r.json()['chart']['result'][0]['meta']
        price = float(meta['regularMarketPrice'])
        # chartPreviousClose is the previous session's close, which is what the
        # percentage on every finance site is measured against.
        prev = float(meta.get('chartPreviousClose') or meta.get('previousClose') or 0)
    except Exception as e:
        log.warning('BSE ticker: could not read a price (%s)', e)
        return None

    change = price - prev if prev else 0.0
    pct = (change / prev * 100) if prev else 0.0
    return {
        'quote': f'Apis India Ltd BSE Price: ₹{price:,.2f}',
        'price': round(price, 2),
        'previous_close': round(prev, 2),
        'change': round(change, 2),
        # Always signed: "+1.03%" and "-0.91%" both read as a movement,
        # where a bare "0.91%" reads as a fact about the price.
        'change_pct': f'{pct:+.2f}%',
        'trend_up': change >= 0,
        'tagline': TAGLINE,
        'stale': False,
    }


def ticker():
    """What the banner should show right now.

    Three outcomes, and the caller can tell them apart: a fresh reading, a
    stale one flagged as such, or nothing at all - in which case the banner
    shows only the tagline rather than an invented price.
    """
    hit = cache.get(FRESH_KEY)
    if hit:
        return hit

    fresh = _fetch()
    if fresh:
        cache.set(FRESH_KEY, fresh, FRESH_TTL)
        cache.set(LAST_GOOD_KEY, fresh, LAST_GOOD_TTL)
        return fresh

    last = cache.get(LAST_GOOD_KEY)
    if last:
        # Not re-cached under FRESH_KEY: the next request should try upstream
        # again rather than settle into serving an old number for 15 minutes.
        return {**last, 'stale': True}

    return {'quote': '', 'price': None, 'change_pct': '', 'trend_up': True,
            'tagline': TAGLINE, 'stale': False, 'unavailable': True}
