"""GET /api/marketwatch/dashboard/ - the Market Watch panel's data.

  ?product=dates     which product: honey (default) or dates - sources.PRODUCTS
  ?size=500          the pack size compared, in grams (that product's sizes)
  ?day=2026-11-03    a past day, from the price history (default: today, live)
  ?refresh=1         fetch now instead of using the last 30 minutes' fetch

Read-only, and for signed-in intranet users only. Every number in the
answer is either a price read off a live listing or arithmetic on those
prices; when a brand could not be found, its row says so instead.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from statistics import mean

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from accounts.auth import SessionRequiredAPIView

from . import sources
from .models import PriceSnapshot

LIVE_TTL = 30 * 60          # a fetch is reused for half an hour
MIN_REFRESH_GAP = 2 * 60    # and Refresh cannot hammer the stores faster than this
HOME = 'APIS'
DEFAULT_PRODUCT = sources.DEFAULT_PRODUCT
# Honey's sizes, for older callers; each product has its own (sizes()).
SIZES = sources.PRODUCTS[DEFAULT_PRODUCT]['sizes']


def live_key(product=DEFAULT_PRODUCT):
    return f'marketwatch:live:{product}'


# Kept for anything that still reads honey's cache under the old name.
LIVE_KEY = live_key()


def product_of(value):
    """A known product key, or the default for anything else."""
    return value if value in sources.PRODUCTS else DEFAULT_PRODUCT


def sizes(product=DEFAULT_PRODUCT):
    return sources.PRODUCTS[product]['sizes']


def brand_names(product=DEFAULT_PRODUCT):
    return [b for b, _ in sources.brands(product)]


def _round(x, n=2):
    return None if x is None else round(x, n)


def _known_products(product=DEFAULT_PRODUCT, days=60):
    """{brand: [each product found on an earlier day]}, newest record of each:
    the links to fall back on when today's search misses a brand."""
    since = timezone.localdate() - timedelta(days=days)
    out, seen = defaultdict(list), set()
    for s in PriceSnapshot.objects.filter(product=product, day__gte=since).order_by('-fetched_at'):
        if not s.url or s.product_id in seen:
            continue
        seen.add(s.product_id)
        out[s.brand].append({'brand': s.brand, 'title': s.title, 'product_id': s.product_id,
                             'pack_grams': s.pack_grams, 'pack_label': s.pack_label,
                             'url': s.url, 'image': s.image})
    return out


def _fetch_live(product=DEFAULT_PRODUCT):
    """One product's prices, fetched now, with the prices written to the history."""
    listings, status = sources.all_prices(product, known=_known_products(product))
    now = timezone.now()
    day = timezone.localdate(now)
    with transaction.atomic():
        PriceSnapshot.objects.bulk_create([PriceSnapshot(
            fetched_at=now, day=day, source=l['source'], product=product, brand=l['brand'],
            title=l['title'][:400], product_id=l['product_id'], pack_grams=l['pack_grams'],
            pack_label=l['pack_label'], mrp=l['mrp'], price=l['price'], url=l['url'], image=l['image'],
            in_stock=l['in_stock']) for l in listings])
    live = {'fetched_at': now.isoformat(), 'listings': listings, 'status': status}
    cache.set(live_key(product), live, timeout=LIVE_TTL)
    return live


# How far a pack may be from the size compared and still count as that size:
# a 400 g jar stands in for 500 g, a 1 kg jar does not stand in for 250 g.
SIZE_BAND = (0.5, 1.6)


def _in_band(grams, size):
    return SIZE_BAND[0] * size <= grams <= SIZE_BAND[1] * size


def _pick(listings, brand, size):
    """The brand's listing closest to `size` grams - in stock first, then the
    lowest price a shopper can pay for that pack, so a brand sold by several
    sellers is shown at its best real offer. None if it sells nothing near
    that size (see SIZE_BAND)."""
    mine = [l for l in listings if l['brand'] == brand and _in_band(l['pack_grams'], size)]
    if not mine:
        return None
    return min(mine, key=lambda l: (not l['in_stock'], abs(l['pack_grams'] - size), l['price']))


def _row(l):
    per100 = l['price'] * 100 / l['pack_grams']
    return {**l, 'per_100g': _round(per100),
            'discount_pct': _round((l['mrp'] - l['price']) * 100 / l['mrp'], 1) if l['mrp'] else None}


def _compare(listings, size, product=DEFAULT_PRODUCT):
    rows = []
    for brand in brand_names(product):
        l = _pick(listings, brand, size)
        listed = any(x['brand'] == brand for x in listings)
        rows.append(_row(l) if l else {'brand': brand, 'missing': True,
                                       'reason': 'size' if listed else 'unlisted'})
    home = next((r for r in rows if r['brand'] == HOME and not r.get('missing')), None)
    for r in rows:
        if r.get('missing') or not home or r is home:
            continue
        r['diff_vs_apis'] = _round(r['per_100g'] - home['per_100g'])
        r['diff_vs_apis_pct'] = _round((r['per_100g'] - home['per_100g']) * 100 / home['per_100g'], 1)
    return rows, home


def _nearest(listings, brand, size):
    """Same pick as _pick: one rule for every table and the report."""
    return _pick(listings, brand, size)


def _size_label(size):
    return f'{size // 1000} kg' if size % 1000 == 0 else f'{size} g'


def _matrix(listings, product=DEFAULT_PRODUCT):
    """The standard sizes down the side, every listed brand across. Where a
    brand does not sell exactly that size, its cell is its real pack nearest
    to it, marked as such - but only a pack near enough to be the same kind
    of buy (SIZE_BAND); otherwise the cell is empty and says so."""
    rows = []
    for size in sizes(product):
        cells = {}
        for b in brand_names(product):
            l = _nearest(listings, b, size)
            if l:
                cells[b] = {**_row(l), 'exact': l['pack_grams'] == size}
        cheapest = min(cells.values(), key=lambda c: c['per_100g'])['brand'] if cells else None
        rows.append({'grams': size, 'label': _size_label(size), 'cells': cells, 'cheapest': cheapest})
    return rows


def _all_listings(listings, product=DEFAULT_PRODUCT):
    """Every listing found, per brand, smallest pack first."""
    out = {}
    for b in brand_names(product):
        mine = sorted((_row(l) for l in listings if l['brand'] == b), key=lambda r: (r['pack_grams'], r['price']))
        if mine:
            out[b] = mine
    return out


def _spotlight(rows, home):
    """APIS against each competitor, per 100 g - where it is cheaper, and by
    how much; and, as plainly, where it is not."""
    if not home:
        return None
    beats, behind = [], []
    for r in rows:
        if r.get('missing') or r['brand'] == HOME:
            continue
        cheaper = r['per_100g'] > home['per_100g']
        gap = (r['per_100g'] - home['per_100g']) * 100 / (r['per_100g'] if cheaper else home['per_100g'])
        entry = {'brand': r['brand'], 'per_100g': r['per_100g'], 'pct': round(abs(gap), 1)}
        (beats if cheaper else behind).append(entry)
    beats.sort(key=lambda e: -e['pct'])
    return {'product': home, 'beats': beats, 'behind': behind,
            'rank': 1 + len(behind), 'of': 1 + len(beats) + len(behind)}


def _insights(rows, home, size, product=DEFAULT_PRODUCT):
    noun = sources.PRODUCTS[product]['noun']
    found = [r for r in rows if not r.get('missing')]
    rivals = [r for r in found if r['brand'] != HOME]
    out = []
    if found:
        best = min(found, key=lambda r: r['per_100g'])
        out.append({'kind': 'cheapest', 'title': f"Lowest price: {best['brand']}",
                    'value': f"₹{best['per_100g']:.2f} / 100g",
                    'detail': f"{best['pack_label']} for ₹{best['price']:.0f} on {best['source']}."})
    if rivals:
        avg = mean(r['per_100g'] for r in rivals)
        out.append({'kind': 'average', 'title': 'Competitor average', 'value': f'₹{avg:.2f} / 100g',
                    'detail': f"Across {len(rivals)} competitor brand{'s' if len(rivals) > 1 else ''} listed today."})
        if home:
            gap = (home['per_100g'] - avg) * 100 / avg
            out.append({'kind': 'apis', 'title': 'APIS vs the market',
                        'value': f"{abs(gap):.1f}% {'below' if gap < 0 else 'above'} average",
                        'detail': f"APIS is ₹{home['per_100g']:.2f} / 100g ({home['pack_label']}) "
                                  f"against a competitor average of ₹{avg:.2f}."})
    if found:
        deal = max(found, key=lambda r: r['discount_pct'] or 0)
        if deal['discount_pct']:
            out.append({'kind': 'discount', 'title': f"Biggest discount: {deal['brand']}",
                        'value': f"{deal['discount_pct']:.0f}% off MRP",
                        'detail': f"₹{deal['price']:.0f} against an MRP of ₹{deal['mrp']:.0f}."})
    unlisted = [r['brand'] for r in rows if r.get('missing') and r.get('reason') == 'unlisted']
    if unlisted:
        out.append({'kind': 'missing', 'title': 'Not listed today', 'value': ', '.join(unlisted),
                    'detail': f'No {noun} from these brands was found on Flipkart, so they are left out of every figure.'})
    nosize = [r['brand'] for r in rows if r.get('missing') and r.get('reason') == 'size']
    if nosize:
        out.append({'kind': 'nosize', 'title': f'No pack near {_size_label(size)}', 'value': ', '.join(nosize),
                    'detail': 'These brands do not sell a pack close to this size, so they are left out of this comparison.'})
    if home and home['pack_grams'] != size:
        out.append({'kind': 'note', 'title': 'APIS pack size', 'value': home['pack_label'],
                    'detail': f"APIS's nearest Flipkart listing to {_size_label(size)} is {home['pack_label']}, "
                              f'so packs are compared on price per 100 g.'})
    return out


def _history(size, product=DEFAULT_PRODUCT, days=30):
    """Per brand, per day: price per 100 g of the pack nearest `size`, from
    the latest fetch that day. Only days something was actually fetched."""
    since = timezone.localdate() - timedelta(days=days - 1)
    snaps = PriceSnapshot.objects.filter(product=product, day__gte=since).order_by('day', '-fetched_at')
    latest = {}
    for s in snaps:                       # newest fetch per day wins
        latest.setdefault(s.day, s.fetched_at)
    by_day = defaultdict(list)
    for s in snaps:
        if s.fetched_at == latest[s.day]:
            by_day[s.day].append({'brand': s.brand, 'pack_grams': s.pack_grams, 'price': float(s.price),
                                  'in_stock': s.in_stock})
    series = {b: [] for b in brand_names(product)}
    for d in sorted(by_day):
        for b in series:
            l = _pick(by_day[d], b, size)
            if l:
                series[b].append({'day': d.isoformat(), 'per_100g': _round(l['price'] * 100 / l['pack_grams'])})
    return {'days': [d.isoformat() for d in sorted(by_day)],
            'series': [{'brand': b, 'points': p} for b, p in series.items() if p]}


def _snapshot_day(day, product=DEFAULT_PRODUCT):
    """A past day's listings of one product, from the last fetch made that day."""
    last = (PriceSnapshot.objects.filter(product=product, day=day).order_by('-fetched_at')
            .values_list('fetched_at', flat=True).first())
    if not last:
        return None, []
    rows = PriceSnapshot.objects.filter(product=product, day=day, fetched_at=last)
    return last, [{'source': s.source, 'brand': s.brand, 'title': s.title, 'product_id': s.product_id,
                   'pack_grams': s.pack_grams, 'pack_label': s.pack_label, 'mrp': float(s.mrp),
                   'price': float(s.price), 'url': s.url, 'image': s.image, 'in_stock': s.in_stock}
                  for s in rows]


class ReportView(SessionRequiredAPIView):
    """GET ?day=YYYY-MM-DD[&product=dates][&type=pdf] -> that day's comparison
    of one product as an Excel file, or a PDF.

    Today's report is fetched first if nothing has been fetched yet today;
    a past day with no saved prices answers 404 rather than an empty file."""

    def get(self, request):
        from django.http import HttpResponse
        from . import report

        q = request.query_params
        product = product_of(q.get('product'))
        label = sources.PRODUCTS[product]['label'].lower()
        today = timezone.localdate()
        try:
            day = date.fromisoformat(q.get('day') or today.isoformat())
        except ValueError:
            return Response({'error': 'Use a date like 2026-11-12.'}, status=400)
        if day > today:
            return Response({'error': 'There is no report for a day still to come.'}, status=400)
        if day == today and not PriceSnapshot.objects.filter(product=product, day=today).exists():
            _fetch_live(product)
        # 'type', not 'format': DRF claims ?format= for itself and answers 404
        # for any value it has no renderer for, before this view runs.
        as_pdf = (q.get('type') or '').lower() == 'pdf'
        if as_pdf:
            from . import report_pdf
            built = report_pdf.build(day, product)
        else:
            built = report.build(day, product)
        if not built:
            return Response({'error': f'No {label} prices were saved on {day:%d %b %Y}, so there is no '
                                      f'report for it.'}, status=404)
        name, data = built
        resp = HttpResponse(data, content_type='application/pdf' if as_pdf else
                            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{name}"'
        return resp


class DashboardView(SessionRequiredAPIView):
    def get(self, request):
        q = request.query_params
        product = product_of(q.get('product'))
        spec = sources.PRODUCTS[product]
        try:
            size = int(q.get('size') or 500)
        except ValueError:
            size = 500
        if size not in spec['sizes']:
            size = 500 if 500 in spec['sizes'] else spec['sizes'][0]

        live = cache.get(live_key(product))
        note = ''
        if q.get('refresh') == '1' and live:
            age = (timezone.now() - datetime.fromisoformat(live['fetched_at'])).total_seconds()
            if age < MIN_REFRESH_GAP:
                note = f'Fetched {int(age)} seconds ago - showing that rather than asking the stores again so soon.'
            else:
                live = None
        if not live:
            live = _fetch_live(product)

        today = timezone.localdate()
        day = today
        try:
            if q.get('day'):
                day = date.fromisoformat(q['day'])
        except ValueError:
            pass
        fetched_at, listings = live['fetched_at'], live['listings']
        if day != today:
            past_at, past = _snapshot_day(day, product)
            if past_at:
                fetched_at, listings = past_at.isoformat(), past
            else:
                day = today

        rows, home = _compare(listings, size, product)
        # The days with saved prices, per product: the Download report list
        # offers every product, not only the one on screen.
        days_by_product = {p: [] for p in sources.PRODUCTS}
        for p, d in PriceSnapshot.objects.values_list('product', 'day').distinct():
            if p in days_by_product:
                days_by_product[p].append(d.isoformat())
        days_by_product = {p: sorted(set(ds), reverse=True) for p, ds in days_by_product.items()}
        days = days_by_product[product]
        return Response({
            'product': product, 'product_label': spec['label'], 'product_noun': spec['noun'],
            'products': [{'key': k, 'label': v['label']} for k, v in sources.PRODUCTS.items()],
            'day': day.isoformat(), 'is_live': day == today, 'fetched_at': fetched_at, 'note': note,
            'size': size, 'sizes': spec['sizes'], 'days': days, 'days_by_product': days_by_product,
            'comparison': rows, 'insights': _insights(rows, home, size, product),
            'matrix': _matrix(listings, product), 'all_listings': _all_listings(listings, product),
            'spotlight': _spotlight(rows, home),
            'history': _history(size, product),
            'news': [], 'news_errors': [],
            'sources': {'prices': 'Flipkart', 'news': '',
                        'status': live['status'], 'unavailable': sources.UNAVAILABLE_STORES,
                        'stores': sources.STORES},
        })
