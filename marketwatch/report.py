"""The daily price-comparison report: one Excel file per day.

Built only from the prices saved that day (the last fetch made that day),
so a day's report is the same whenever it is downloaded, and a day nothing
was fetched has no report rather than a made-up one.

Sheets:
  Best price       - plain-language ranking per pack size, best price first,
                     whoever that is, plus which stores were read
  Brand comparison - the brands side by side, one column each, with a chart
  Summary       - where APIS stands at each standard size, and the day's key figures
  By pack size   - every brand's price at 250 g / 500 g / 1 kg (nearest pack, marked)
  All listings   - every listing of the product read that day, with its link
  vs previous    - each brand's price per 100 g against the previous saved day
"""
from io import BytesIO
from statistics import mean

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import sources
from .models import PriceSnapshot
from .views import DEFAULT_PRODUCT, HOME, _compare, _insights, _matrix, _row, _snapshot_day, brand_names, sizes

# The Market Watch panel's own colours.
AMBER = 'FBBF24'        # header band
ORANGE = 'EA580C'       # title
APIS_ROW = 'FFFBEB'     # APIS, as highlighted on screen
LOWEST = 'ECFDF5'       # lowest price per 100 g
GREEN, RED, MUTED = '047857', 'BE123C', '64748B'

RUPEE = '"₹"#,##0'
RUPEE2 = '"₹"#,##0.00'
PCT = '0.0"%"'
EXTRA = '"+₹"#,##0.00;"-₹"#,##0.00;"best price"'   # 0 extra = the best price
_thin = Side(style='thin', color='FDE68A')
BOX = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def _size(g):
    return f'{g // 1000} kg' if g % 1000 == 0 else f'{g} g'


def _title(ws, text, sub):
    ws['A1'] = text
    ws['A1'].font = Font(size=15, bold=True, color=ORANGE)
    ws['A2'] = sub
    ws['A2'].font = Font(size=10, color=MUTED)


def _table(ws, top, headers, rows, formats=None, fills=None, widths=None):
    """A header band and rows from `top`; formats/fills are per column / per row."""
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=top, column=c, value=h)
        cell.font = Font(bold=True, color='1E293B')
        cell.fill = PatternFill('solid', fgColor=AMBER)
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = BOX
    for r, row in enumerate(rows, top + 1):
        fill = (fills or {}).get(r - top - 1)
        for c, v in enumerate(row, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = BOX
            if formats and formats.get(c):
                cell.number_format = formats[c]
            if fill:
                cell.fill = PatternFill('solid', fgColor=fill)
    for c, w in (widths or {}).items():
        ws.column_dimensions[get_column_letter(c)].width = w
    return top + len(rows) + 1


# Each brand's colour on the Market Watch panel, so the chart reads the same.
BRAND_COLOUR = {'APIS': '2A78D6', 'Dabur': 'EB6834', 'Patanjali': '1BAF7A', 'Saffola': 'E87BA4', 'Zandu': '8B5CF6',
                'Aladdin': '0D9488', 'Nutraj': 'DC2626'}


def _cell(ws, r, c, v, fmt=None, bold=False, fill=None, color=None, align='center'):
    cell = ws.cell(row=r, column=c, value=v)
    cell.border = BOX
    cell.alignment = Alignment(horizontal=align, vertical='center', wrap_text=True)
    cell.font = Font(bold=bold, color=color or '1E293B')
    if fmt:
        cell.number_format = fmt
    if fill:
        cell.fill = PatternFill('solid', fgColor=fill)
    return cell


def _head(ws, r, c, v):
    return _cell(ws, r, c, v, bold=True, fill=AMBER)


def _section(ws, r, text):
    ws.cell(row=r, column=1, value=text).font = Font(bold=True, size=12, color=ORANGE)


def _comparison_sheet(wb, listings, brands, when, at, product=DEFAULT_PRODUCT):
    """Brands side by side, one column each, APIS first: the figures a reader
    compares, lined up so the comparison is the first thing they see."""
    ws = wb.create_sheet('Brand comparison', 0)
    _title(ws, 'Brand comparison — every brand side by side',
           f'{when} · Flipkart prices at {at} IST · brands ordered by best price (per 100 g at 500 g) · '
           'green = best price, amber column = APIS')
    matrix = _matrix(listings, product)
    at500 = next(m for m in matrix if m['grams'] == 500)['cells']
    cols = sorted(brands, key=lambda b: at500[b]['per_100g'] if b in at500 else float('inf'))
    ws.column_dimensions['A'].width = 24
    for i in range(len(cols)):
        ws.column_dimensions[get_column_letter(2 + i)].width = 18

    def brand_head(r):
        # Dark name on the amber band, underlined in the brand's chart colour.
        _head(ws, r, 1, '')
        for i, b in enumerate(cols):
            c = _head(ws, r, 2 + i, f'{b}  (ours)' if b == HOME else b)
            c.border = Border(left=_thin, right=_thin, top=_thin,
                              bottom=Side(style='thick', color=BRAND_COLOUR.get(b, '94A3B8')))

    # ── 1. Price per 100 g at a glance, with its chart ──
    _section(ws, 4, '1. Price per 100 g at each pack size (lower is cheaper)')
    top = 5
    brand_head(top)
    for j, m in enumerate(matrix):
        r = top + 1 + j
        _cell(ws, r, 1, m['label'], bold=True, align='left')
        cells = m['cells']
        low = min((c['per_100g'] for c in cells.values()), default=None)
        for i, b in enumerate(cols):
            c = cells.get(b)
            v = c['per_100g'] if c else None
            best = c is not None and v == low and len(cells) > 1
            _cell(ws, r, 2 + i, v if c else 'No pack near', RUPEE2 if c else None, bold=best or b == HOME,
                  fill=LOWEST if best else APIS_ROW if b == HOME else None, color=GREEN if best else None)
    last = top + len(matrix)

    # The chart reads a hidden copy of the table with the gaps left blank:
    # read off the visible table, "No pack near" would be drawn as a 0 bar.
    hid = 2 + len(cols) + 2
    for i, b in enumerate(cols):
        ws.cell(row=top, column=hid + i, value=f'{b}  (ours)' if b == HOME else b)
        for j, m in enumerate(matrix):
            c = m['cells'].get(b)
            ws.cell(row=top + 1 + j, column=hid + i, value=c['per_100g'] if c else None).number_format = RUPEE2
    for i in range(len(cols)):
        ws.column_dimensions[get_column_letter(hid + i)].hidden = True

    chart = BarChart()
    chart.type = 'col'
    chart.title = 'Price per 100 g by brand (₹)'
    chart.y_axis.majorGridlines = None
    chart.height, chart.width = 8, 22
    chart.add_data(Reference(ws, min_col=hid, max_col=hid + len(cols) - 1, min_row=top, max_row=last),
                   titles_from_data=True)
    chart.gapWidth = 80
    chart.visible_cells_only = False      # the data is in hidden columns
    chart.set_categories(Reference(ws, min_col=1, min_row=top + 1, max_row=last))
    for s, b in zip(chart.series, cols):
        s.graphicalProperties.solidFill = BRAND_COLOUR.get(b, '94A3B8')
        s.graphicalProperties.line.solidFill = BRAND_COLOUR.get(b, '94A3B8')
    # The value alone on each bar: Excel otherwise adds series and category too.
    chart.dataLabels = DataLabelList()
    chart.dataLabels.showVal = True
    chart.dataLabels.showSerName = chart.dataLabels.showCatName = False
    chart.dataLabels.showLegendKey = chart.dataLabels.showPercent = False
    chart.dataLabels.numFmt = '"₹"0'
    chart.legend.position = 'b'
    chart.legend.overlay = False
    chart.x_axis.delete = chart.y_axis.delete = False     # openpyxl hides both by default
    chart.y_axis.number_format = '"₹"0'
    ws.add_chart(chart, f'A{last + 2}')
    r = last + 18                       # below the chart

    # ── 2. Side by side at each size ──
    metrics = [('Pack compared', None), ('Selling price', RUPEE), ('MRP', RUPEE), ('Discount off MRP', PCT),
               ('Price per 100 g', RUPEE2), ('Extra vs best price (per 100 g)', EXTRA), ('Rank (1 = best price)', None),
               ('In stock', None)]
    for n, m in enumerate(matrix):
        _section(ws, r, f'{2 + n}. Side by side at {m["label"]}')
        brand_head(r + 1)
        cells = m['cells']
        ranked = sorted(cells.values(), key=lambda c: c['per_100g'])
        rank = {c['brand']: n + 1 for n, c in enumerate(ranked)}
        low = ranked[0]['per_100g'] if ranked else None
        for k, (name, fmt) in enumerate(metrics):
            row = r + 2 + k
            _cell(ws, row, 1, name, bold=True, align='left')
            for i, b in enumerate(cols):
                c = cells.get(b)
                if not c:
                    _cell(ws, row, 2 + i, 'No pack near this size' if k == 0 else '—', color=MUTED)
                    continue
                v = {
                    0: c['pack_label'] + ('' if c['exact'] else ' (nearest)'),
                    1: c['price'], 2: c['mrp'], 3: c['discount_pct'], 4: c['per_100g'],
                    5: round(c['per_100g'] - low, 2),
                    6: f'#{rank[b]} of {len(ranked)}',
                    7: 'Yes' if c['in_stock'] else 'Out of stock',
                }[k]
                best = k in (4, 6) and c['per_100g'] == low and len(cells) > 1
                colour = GREEN if best or (k == 5 and v == 0) else (RED if k == 5 and v else None)
                _cell(ws, row, 2 + i, '—' if v is None else v, fmt if v is not None else None,
                      bold=best or b == HOME, fill=LOWEST if best else APIS_ROW if b == HOME else None, color=colour)
        r += len(metrics) + 3

    # ── 3. Every listing, summed up per brand ──
    _section(ws, r, f'{2 + len(matrix)}. Brand overview — all listings read that day')
    brand_head(r + 1)
    rows_by = {b: [_row(l) for l in listings if l['brand'] == b] for b in cols}
    overview = [
        ('Listings found', lambda ls: len(ls), None, None),
        ('Lowest price per 100 g', lambda ls: min(l['per_100g'] for l in ls), RUPEE2, min),
        ('Average price per 100 g', lambda ls: round(mean(l['per_100g'] for l in ls), 2), RUPEE2, min),
        ('Highest price per 100 g', lambda ls: max(l['per_100g'] for l in ls), RUPEE2, None),
        ('Biggest discount', lambda ls: max(l['discount_pct'] or 0 for l in ls), PCT, max),
        ('Pack sizes sold', lambda ls: ', '.join(sorted({l['pack_label'] for l in ls},
                                                       key=lambda p: next(x['pack_grams'] for x in ls if x['pack_label'] == p))), None, None),
    ]
    for k, (name, fn, fmt, best_of) in enumerate(overview):
        row = r + 2 + k
        _cell(ws, row, 1, name, bold=True, align='left')
        vals = {b: fn(ls) for b, ls in rows_by.items() if ls}
        best = best_of(vals.values()) if best_of and len(vals) > 1 else None
        for i, b in enumerate(cols):
            if b not in vals:
                _cell(ws, row, 2 + i, 'Not listed', color=MUTED)
                continue
            win = best is not None and vals[b] == best
            _cell(ws, row, 2 + i, vals[b], fmt, bold=win or b == HOME,
                  fill=LOWEST if win else APIS_ROW if b == HOME else None, color=GREEN if win else None)
    ws.freeze_panes = 'B4'
    ws.sheet_view.zoomScale = 110
    return ws


ORDINAL = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']
MEDAL = {0: 'FCD34D', 1: 'E2E8F0', 2: 'FDBA74'}


def _easy_sheet(wb, listings, when, at, product=DEFAULT_PRODUCT):
    """The report's front page: for each pack size, which brand is cheapest,
    in plain words, then every brand ranked by what the buyer really pays.
    Nobody is pinned to the top - the order is the prices' order."""
    ws = wb.create_sheet('Best price', 0)
    noun = sources.PRODUCTS[product]['noun']
    _title(ws, f'Which {noun} offers the best price? — daily price comparison',
           f'{when} · real prices read at {at} IST · price after discount · ranked by best price')
    widths = {1: 10, 2: 15, 3: 52, 4: 15, 5: 11, 6: 11, 7: 11, 8: 9, 9: 13, 10: 17, 11: 12, 12: 11}
    for c, w in widths.items():
        ws.column_dimensions[get_column_letter(c)].width = w

    # How to read it, before any number.
    notes = [
        'How to read this report:',
        '•  "You pay" is the selling price after the store\'s discount; MRP is the printed price.',
        '•  Packs come in different sizes, so brands are ranked by what 100 g costs — lower is better value for the buyer.',
        '•  Each brand\'s pack nearest the size is used; "(nearest)" means the brand does not sell that exact size.',
        '•  Green row = best price. Amber row = APIS. Click a product name to open the real listing.',
    ]
    for i, t in enumerate(notes):
        c = ws.cell(row=4 + i, column=1, value=t)
        c.font = Font(bold=i == 0, size=10, color='1E293B' if i == 0 else MUTED)
    r = 4 + len(notes) + 1

    # Which stores were read - so a missing store is never read as "no price".
    _section(ws, r, 'Where the prices come from')
    r += 1
    for k, h in enumerate(['Store', 'Status', 'Note'], 1):
        _head(ws, r, k, h)
    for st in (x for x in sources.STORES if x['live']):    # a store appears once connected
        r += 1
        _cell(ws, r, 1, st['store'], bold=True, align='left')
        _cell(ws, r, 2, 'Live' if st['live'] else 'Not connected', bold=True,
              fill=LOWEST if st['live'] else None, color=GREEN if st['live'] else MUTED)
        note = st['reason'].split(' - ', 1)[-1]
        _cell(ws, r, 3, note[0].upper() + note[1:] + ('' if st['live'] else '. No price is guessed for it.'),
              color=MUTED, align='left')
    r += 2

    headers = ['Position', 'Brand', 'Product', 'Pack', 'You pay', 'MRP', 'You save', 'Off %',
               'Cost of 100 g', 'Extra vs best price', 'In stock', 'Store']
    fmts = {5: RUPEE, 6: RUPEE, 7: RUPEE, 8: PCT, 9: RUPEE2, 10: EXTRA}
    for size in sizes(product):
        comp, _ = _compare(listings, size, product)
        found = sorted((x for x in comp if not x.get('missing')), key=lambda x: x['per_100g'])
        _section(ws, r, f'Around {_size(size)}')
        r += 1
        if not found:
            ws.cell(row=r, column=1, value='No brand had a listing that day.').font = Font(color=MUTED)
            r += 2
            continue
        nosize = [x['brand'] for x in comp if x.get('missing')]
        best = found[0]
        lead = (f"Only {best['brand']} sells a pack near {_size(size)}" if len(found) == 1
                else f"Best price: {best['brand']}")
        line = (f"{lead} — {best['pack_label']} for ₹{best['price']:,.0f}, "
                f"that is ₹{best['per_100g']:,.2f} for every 100 g.")
        pos = next((i for i, x in enumerate(found) if x['brand'] == HOME), None)
        if pos == 0:
            line += ' That is APIS.'
        elif pos is not None:
            line += (f" APIS is {ORDINAL[pos]} of {len(found)}, "
                     f"₹{found[pos]['per_100g'] - best['per_100g']:,.2f} more per 100 g than {best['brand']}.")
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=len(headers))
        v = ws.cell(row=r, column=1, value=line)
        v.font = Font(bold=True, size=11, color=GREEN)
        v.fill = PatternFill('solid', fgColor=LOWEST)
        v.alignment = Alignment(vertical='center', wrap_text=True)
        ws.row_dimensions[r].height = 26
        r += 1
        for k, h in enumerate(headers, 1):
            _head(ws, r, k, h)
        for i, x in enumerate(found):
            r += 1
            save = round(x['mrp'] - x['price'], 2) if x['mrp'] and x['mrp'] > x['price'] else 0
            vals = [ORDINAL[i], x['brand'] + ('  (ours)' if x['brand'] == HOME else ''), x['title'],
                    x['pack_label'] + ('' if x['pack_grams'] == size else ' (nearest)'),
                    x['price'], x['mrp'], save, x['discount_pct'] or 0, x['per_100g'],
                    round(x['per_100g'] - best['per_100g'], 2),
                    'Yes' if x['in_stock'] else 'Out of stock', x['source']]
            fill = LOWEST if i == 0 else APIS_ROW if x['brand'] == HOME else None
            for k, val in enumerate(vals, 1):
                colour = (GREEN if k in (9, 10) and i == 0 else RED if k == 10 else
                          GREEN if k == 7 and save else None)
                _cell(ws, r, k, val, fmts.get(k), bold=k in (1, 2, 5, 9) or i == 0,
                      fill=MEDAL.get(i) if k == 1 else fill, color=colour, align='left' if k == 3 else 'center')
            link = ws.cell(row=r, column=3)
            link.hyperlink = x['url']
            link.font = Font(color='2563EB', underline='single', bold=i == 0)
        if nosize:
            r += 1
            ws.cell(row=r, column=1, value=f"Not in this comparison — no pack near {_size(size)}: "
                                           f"{', '.join(nosize)}.").font = Font(italic=True, size=10, color=MUTED)
        r += 3
    ws.freeze_panes = 'A4'
    ws.sheet_view.zoomScale = 105
    return ws


def _previous_day(day, product=DEFAULT_PRODUCT):
    return (PriceSnapshot.objects.filter(product=product, day__lt=day).order_by('-day')
            .values_list('day', flat=True).first())


def build(day, product=DEFAULT_PRODUCT):
    """-> (filename, xlsx bytes) for one product, or None if no prices were saved that day."""
    fetched_at, listings = _snapshot_day(day, product)
    noun, label = sources.PRODUCTS[product]['noun'], sources.PRODUCTS[product]['label']
    if not fetched_at:
        return None
    brands = brand_names(product)
    listings = [l for l in listings if l['brand'] in brands]
    at = timezone.localtime(fetched_at).strftime('%d %b %Y, %I:%M %p')
    when = day.strftime('%A, %d %B %Y')
    wb = Workbook()

    # ── Summary ──
    ws = wb.active
    ws.title = 'Summary'
    _title(ws, 'APIS Market Watch — Daily price comparison',
           f'{when} · prices read from Flipkart at {at} IST · every figure is a live listing price')
    rows, fills = [], {}
    for size in sizes(product):
        comp, home = _compare(listings, size, product)
        found = [r for r in comp if not r.get('missing')]
        rivals = [r for r in found if r['brand'] != HOME]
        cheapest = min(found, key=lambda r: r['per_100g']) if found else None
        avg = mean(r['per_100g'] for r in rivals) if rivals else None
        rank = 1 + sum(1 for r in rivals if home and r['per_100g'] < home['per_100g']) if home else None
        rows.append([
            _size(size), home['pack_label'] if home else 'Not listed', home['price'] if home else None,
            home['per_100g'] if home else None, f'#{rank} of {len(found)}' if home else '—',
            cheapest['brand'] if cheapest else '—', cheapest['per_100g'] if cheapest else None,
            avg, round((home['per_100g'] - avg) * 100 / avg, 1) if home and avg else None,
        ])
        if home and rank == 1:
            fills[len(rows) - 1] = LOWEST
    end = _table(ws, 4, ['Compared at', 'APIS pack', 'APIS price', 'APIS ₹ / 100 g', 'APIS rank',
                         'Lowest brand', 'Lowest ₹ / 100 g', 'Competitor avg ₹ / 100 g', 'APIS vs avg %'],
                 rows, formats={3: RUPEE, 4: RUPEE2, 7: RUPEE2, 8: RUPEE2, 9: PCT}, fills=fills,
                 widths={1: 26, 2: 18, 3: 12, 4: 14, 5: 12, 6: 16, 7: 16, 8: 20, 9: 14})
    ws.cell(row=end + 1, column=1, value='Key figures at 500 g').font = Font(bold=True, size=12, color=ORANGE)
    comp, home = _compare(listings, 500, product)
    ins = [[i['title'], i['value'], i['detail']] for i in _insights(comp, home, 500, product)]
    # Detail runs on across the empty columns to its right.
    _table(ws, end + 2, ['Figure', 'Value', 'Detail'], ins)
    ws.freeze_panes = 'A5'

    # ── By pack size ──
    ws = wb.create_sheet('By pack size')
    _title(ws, 'Prices by pack size', f'{when} · where a brand does not sell that exact size, '
                                      'its nearest pack is shown and marked "nearest"')
    rows, fills = [], {}
    for r in _matrix(listings, product):
        cells = r['cells']
        low = min((c['per_100g'] for c in cells.values()), default=None)
        for b in sorted(brands, key=lambda b: cells[b]['per_100g'] if b in cells else float('inf')):
            c = cells.get(b)
            if not c:
                rows.append([r['label'], b, 'No pack near this size', '', None, None, None, None, None, ''])
                continue
            vs = round(c['per_100g'] - low, 2)
            rows.append([r['label'], b, c['pack_label'], 'exact' if c['exact'] else 'nearest',
                         c['price'], c['mrp'], c['discount_pct'], c['per_100g'], vs,
                         'Lowest' if c['per_100g'] == low and len(cells) > 1 else ''])
            if c['per_100g'] == low and len(cells) > 1:
                fills[len(rows) - 1] = LOWEST
            elif b == HOME:
                fills[len(rows) - 1] = APIS_ROW
    _table(ws, 4, ['Size', 'Brand', 'Pack', 'Size match', 'Price', 'MRP', 'Off %', '₹ / 100 g',
                   'Extra vs best price ₹ / 100 g', 'Lowest in size'],
           rows, formats={5: RUPEE, 6: RUPEE, 7: PCT, 8: RUPEE2, 9: EXTRA}, fills=fills,
           widths={1: 10, 2: 14, 3: 14, 4: 12, 5: 11, 6: 11, 7: 9, 8: 12, 9: 16, 10: 14})
    ws.freeze_panes = 'A5'
    ws.auto_filter.ref = f'A4:J{4 + len(rows)}'

    # ── All listings ──
    ws = wb.create_sheet('All listings')
    _title(ws, f'Every {noun} listing read that day', f'{when} · {len(listings)} listings, lowest price per 100 g first')
    all_rows = sorted((_row(l) for l in listings), key=lambda r: r['per_100g'])
    rows, fills = [], {}
    for i, l in enumerate(all_rows):
        rows.append([i + 1, l['brand'], l['title'], l['pack_label'], l['pack_grams'], l['price'], l['mrp'],
                     l['discount_pct'], l['per_100g'], 'Yes' if l['in_stock'] else 'Out of stock', l['url']])
        if l['brand'] == HOME:
            fills[i] = APIS_ROW
    _table(ws, 4, ['#', 'Brand', 'Product', 'Pack', 'Grams', 'Price', 'MRP', 'Off %', '₹ / 100 g',
                   'In stock', 'Flipkart link'],
           rows, formats={6: RUPEE, 7: RUPEE, 8: PCT, 9: RUPEE2}, fills=fills,
           widths={1: 5, 2: 12, 3: 60, 4: 12, 5: 8, 6: 10, 7: 10, 8: 8, 9: 11, 10: 12, 11: 40})
    for r in range(5, 5 + len(rows)):
        link = ws.cell(row=r, column=11)
        if link.value:
            link.hyperlink = link.value
            link.value = 'Open listing'
            link.font = Font(color='2563EB', underline='single')
    ws.freeze_panes = 'C5'
    ws.auto_filter.ref = f'A4:K{4 + len(rows)}'

    # ── vs previous saved day ──
    ws = wb.create_sheet('vs previous day')
    prev = _previous_day(day, product)
    if prev:
        _, prev_listings = _snapshot_day(prev, product)
        prev_listings = [l for l in prev_listings if l['brand'] in brands]
        _title(ws, 'Change since the previous saved day',
               f'{when} against {prev.strftime("%A, %d %B %Y")} · price per 100 g of each brand\'s pack nearest the size')
        rows, fills = [], {}
        # The same pack per size as the "By pack size" sheet, on both days.
        for now_m, then_m in zip(_matrix(listings, product), _matrix(prev_listings, product)):
            size = now_m['grams']
            for b in brands:
                r, p = now_m['cells'].get(b), then_m['cells'].get(b)
                if not r:
                    continue
                change = round(r['per_100g'] - p['per_100g'], 2) if p else None
                rows.append([_size(size), r['brand'], p['per_100g'] if p else None, r['per_100g'], change,
                             round(change * 100 / p['per_100g'], 1) if p else None,
                             '—' if change is None else 'Up' if change > 0 else 'Down' if change < 0 else 'Same'])
                if r['brand'] == HOME:
                    fills[len(rows) - 1] = APIS_ROW
        _table(ws, 4, ['Size', 'Brand', f'{prev.strftime("%d %b")} ₹ / 100 g', f'{day.strftime("%d %b")} ₹ / 100 g',
                       'Change ₹', 'Change %', 'Direction'],
               rows, formats={3: RUPEE2, 4: RUPEE2, 5: RUPEE2, 6: PCT}, fills=fills,
               widths={1: 10, 2: 14, 3: 16, 4: 16, 5: 11, 6: 11, 7: 11})
        for r in range(5, 5 + len(rows)):
            d = ws.cell(row=r, column=7)
            d.font = Font(bold=True, color=RED if d.value == 'Up' else GREEN if d.value == 'Down' else MUTED)
        ws.freeze_panes = 'A5'
    else:
        _title(ws, 'Change since the previous saved day', 'No earlier day has saved prices to compare against.')

    _comparison_sheet(wb, listings, brands, when, at, product)
    _easy_sheet(wb, listings, when, at, product)
    wb.active = 0
    for sheet in wb.worksheets:
        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.tabColor = AMBER
    buf = BytesIO()
    wb.save(buf)
    return f'APIS-Market-Watch-{label.replace(" ", "-")}-{day.isoformat()}.xlsx', buf.getvalue()
