"""The daily price report as a PDF: one A4 report per day, written to be
read by anyone, not only by someone used to spreadsheets.

Built from the same saved prices as the Excel report (the last fetch made
that day), so a day's PDF and Excel always agree, and a day nothing was
fetched has no report rather than a made-up one.

Reads top to bottom in the order a reader asks:
  1. Today at a glance  - the answer in plain sentences
  2. Four key numbers   - at 500 g, the size most people compare
  3. Around 250 g / 500 g / 1 kg - every brand ranked, best price first
  4. What changed since the previous saved day
  5. Every listing read that day, with a link to it
  6. How to read this report

Prices are written "Rs" rather than with the rupee sign: the PDF's standard
fonts have no rupee glyph, and a font that does may not be on the server.
"""
import os
from io import BytesIO
from statistics import mean
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

from . import sources
from .views import DEFAULT_PRODUCT, HOME, _compare, _row, _snapshot_day, brand_names, sizes

LOGO = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'ctc', 'assets', 'apis_logo.png')

# The Market Watch panel's colours.
INK = colors.HexColor('#1E293B')
MUTED = colors.HexColor('#64748B')
FAINT = colors.HexColor('#94A3B8')
RULE = colors.HexColor('#E2E8F0')
AMBER = colors.HexColor('#F59E0B')
AMBER_SOFT = colors.HexColor('#FFFBEB')
AMBER_LINE = colors.HexColor('#FDE68A')
ORANGE = colors.HexColor('#EA580C')
GREEN = colors.HexColor('#047857')
GREEN_SOFT = colors.HexColor('#ECFDF5')
GREEN_LINE = colors.HexColor('#A7F3D0')
RED = colors.HexColor('#BE123C')
HEAD_BG = colors.HexColor('#FEF3C7')
BRAND_COLOUR = {'APIS': '#2A78D6', 'Dabur': '#EB6834', 'Patanjali': '#1BAF7A',
                'Saffola': '#E87BA4', 'Zandu': '#8B5CF6',
                'Aladdin': '#0D9488', 'Nutraj': '#DC2626'}
ORDINAL = ['1st', '2nd', '3rd', '4th', '5th', '6th', '7th', '8th']

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm
WIDTH = PAGE_W - 2 * MARGIN


def _st(name, size, bold=False, color=INK, align=TA_LEFT, leading=None):
    return ParagraphStyle(name, fontName='Helvetica-Bold' if bold else 'Helvetica', fontSize=size,
                          leading=leading or size * 1.3, textColor=color, alignment=align)


TITLE = _st('title', 17, True, INK)
SUB = _st('sub', 9, False, MUTED)
H2 = _st('h2', 12.5, True, ORANGE, leading=16)
BODY = _st('body', 9.5, False, INK, leading=13.5)
SMALL = _st('small', 8, False, MUTED, leading=10.5)
CELL = _st('cell', 8.3, False, INK, leading=10.2)
CELL_B = _st('cellb', 8.3, True, INK, leading=10.2)
CELL_R = _st('cellr', 8.3, False, INK, TA_RIGHT, leading=10.2)
CELL_RB = _st('cellrb', 8.3, True, INK, TA_RIGHT, leading=10.2)
HEAD = _st('head', 7.6, True, colors.HexColor('#92400E'), leading=9.5)
HEAD_R = _st('headr', 7.6, True, colors.HexColor('#92400E'), TA_RIGHT, leading=9.5)
KPI_LABEL = _st('kl', 7.8, True, MUTED, leading=10)
KPI_VALUE = _st('kv', 14, True, INK, leading=17)
KPI_NOTE = _st('kn', 7.6, False, MUTED, leading=9.6)


def rs(n, dp=0):
    """Rs 1,23,456 - Indian grouping, as the panel shows it."""
    if n is None:
        return '-'
    whole = f'{abs(n):.{dp}f}'
    head, _, frac = whole.partition('.')
    if len(head) > 3:
        tail, head = head[-3:], head[:-3]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        head = ','.join(([head] if head else []) + groups) + ',' + tail
    return ('-' if n < 0 else '') + 'Rs ' + head + (f'.{frac}' if frac else '')


def _off(pct):
    if not pct:
        return '-'
    return '<1%' if pct < 1 else f'{pct:.0f}%'


def _size(g):
    return f'{g // 1000} kg' if g % 1000 == 0 else f'{g} g'


def _p(text, style=CELL):
    return Paragraph(text, style)


def _brand(b, bold=True):
    dot = BRAND_COLOUR.get(b, '#94A3B8')
    ours = ' <font size="6.5" color="#B45309">(OURS)</font>' if b == HOME else ''
    return _p(f'<font color="{dot}">&#9679;</font> <b>{escape(b)}</b>{ours}' if bold
              else f'<font color="{dot}">&#9679;</font> {escape(b)}{ours}', CELL)


def _bar(value, top, colour, width=22 * mm, height=3 * mm):
    """A small horizontal bar, longer = dearer, drawn in the brand's colour."""
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.HexColor('#F1F5F9'), strokeColor=None))
    d.add(Rect(0, 0, max(1.5, width * value / top) if top else 0, height,
               fillColor=colors.HexColor(colour), strokeColor=None))
    return d


def _ranked(listings, size, product=DEFAULT_PRODUCT):
    comp, _ = _compare(listings, size, product)
    found = sorted((r for r in comp if not r.get('missing')), key=lambda r: r['per_100g'])
    absent = [r['brand'] for r in comp if r.get('missing')]
    return found, absent


def _verdict(found, size):
    """One sentence a reader takes away for the size."""
    if not found:
        return f'No brand sold a pack near {_size(size)} on Flipkart that day.'
    best = found[0]
    lead = (f'Only <b>{best["brand"]}</b> sold a pack near {_size(size)}'
            if len(found) == 1 else f'Best price: <b>{best["brand"]}</b>')
    s = (f'{lead} - {escape(best["pack_label"])} for <b>{rs(best["price"])}</b>, '
         f'that is <b>{rs(best["per_100g"], 2)} for every 100 g</b>.')
    pos = next((i for i, r in enumerate(found) if r['brand'] == HOME), None)
    if pos == 0 and len(found) > 1:
        s += ' That is APIS.'
    elif pos:
        gap = found[pos]['per_100g'] - best['per_100g']
        s += f' APIS is <b>{ORDINAL[pos]} of {len(found)}</b>, {rs(gap, 2)} more per 100 g than {best["brand"]}.'
    elif pos is None:
        s += ' APIS had no pack near this size.'
    return s


def _changes(listings, prev_listings, product=DEFAULT_PRODUCT):
    """[(brand, size, then, now, change)] per brand and size, both days."""
    out = []
    for size in sizes(product):
        now, _ = _ranked(listings, size, product)
        then = {r['brand']: r for r in _ranked(prev_listings, size, product)[0]}
        for r in now:
            p = then.get(r['brand'])
            if p:
                out.append((r['brand'], size, p['per_100g'], r['per_100g'],
                            round(r['per_100g'] - p['per_100g'], 2)))
    return out


def _table(rows, widths, head_rows=1, extra=()):
    t = Table(rows, colWidths=widths, repeatRows=head_rows)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, head_rows - 1), HEAD_BG),
        ('LINEBELOW', (0, head_rows - 1), (-1, head_rows - 1), 0.8, AMBER),
        ('LINEBELOW', (0, head_rows), (-1, -1), 0.4, RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3.2), ('BOTTOMPADDING', (0, 0), (-1, -1), 3.2),
        ('LEFTPADDING', (0, 0), (-1, -1), 4), ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        *extra,
    ]))
    return t


def _box(flowables, bg, line):
    t = Table([[flowables]], colWidths=[WIDTH])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), bg), ('BOX', (0, 0), (-1, -1), 0.7, line),
        ('LEFTPADDING', (0, 0), (-1, -1), 9), ('RIGHTPADDING', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 7), ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
    ]))
    return t


def _kpis(listings, product=DEFAULT_PRODUCT):
    """Four numbers at 500 g, the size most people compare."""
    found, _ = _ranked(listings, 500, product)
    rivals = [r for r in found if r['brand'] != HOME]
    apis = next((i for i, r in enumerate(found) if r['brand'] == HOME), None)
    deal = max((_row(x) for x in listings), key=lambda r: r['discount_pct'] or 0, default=None)
    cells = []

    def tile(label, value, note, hi=False):
        return (Table([[_p(label, KPI_LABEL)], [_p(value, KPI_VALUE)], [_p(note, KPI_NOTE)]],
                      colWidths=[WIDTH / 4 - 3 * mm]), hi)

    if found:
        b = found[0]
        cells.append(tile('BEST PRICE AT 500 G', escape(b['brand']),
                          f'{rs(b["per_100g"], 2)} per 100 g ({escape(b["pack_label"])})'))
    if apis is not None:
        a = found[apis]
        cells.append(tile('APIS AT 500 G', f'{ORDINAL[apis]} of {len(found)}',
                          f'{rs(a["per_100g"], 2)} per 100 g ({escape(a["pack_label"])})', hi=True))
    if rivals:
        cells.append(tile('COMPETITOR AVERAGE', rs(mean(r['per_100g'] for r in rivals), 2),
                          f'per 100 g, across {len(rivals)} competitor brand{"s" if len(rivals) > 1 else ""}'))
    if deal and deal['discount_pct']:
        cells.append(tile('BIGGEST DISCOUNT', f'{deal["discount_pct"]:.0f}% off',
                          f'{escape(deal["brand"])} {escape(deal["pack_label"])}: {rs(deal["price"])} '
                          f'against MRP {rs(deal["mrp"])}'))
    if not cells:
        return None
    t = Table([[c for c, _ in cells]], colWidths=[WIDTH / len(cells)] * len(cells))
    style = [('VALIGN', (0, 0), (-1, -1), 'TOP'),
             ('LEFTPADDING', (0, 0), (-1, -1), 6), ('RIGHTPADDING', (0, 0), (-1, -1), 6),
             ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]
    for i, (_, hi) in enumerate(cells):
        style += [('BOX', (i, 0), (i, 0), 0.7, AMBER_LINE if hi else RULE),
                  ('BACKGROUND', (i, 0), (i, 0), AMBER_SOFT if hi else colors.white)]
    t.setStyle(TableStyle(style))
    return t


def build(day, product=DEFAULT_PRODUCT):
    """-> (filename, pdf bytes) for one product, or None if no prices were saved that day."""
    from .report import _previous_day

    fetched_at, listings = _snapshot_day(day, product)
    noun, label = sources.PRODUCTS[product]['noun'], sources.PRODUCTS[product]['label']
    if not fetched_at:
        return None
    brands = brand_names(product)
    listings = [l for l in listings if l['brand'] in brands]
    at = timezone.localtime(fetched_at)
    when = day.strftime('%A, %d %B %Y')
    prev = _previous_day(day, product)
    prev_listings = []
    if prev:
        _, prev_listings = _snapshot_day(prev, product)
        prev_listings = [l for l in prev_listings if l['brand'] in brands]

    story = []

    # -- 0. header ---------------------------------------------------------------
    logo = Image(LOGO, width=22 * mm, height=11 * mm) if os.path.exists(LOGO) else ''
    head = Table([[logo, [_p(f'Market Watch - Daily {label} Price Report', TITLE),
                          _p(f'{when} &middot; real prices read from Flipkart at '
                             f'{at.strftime("%I:%M %p").lstrip("0")} IST &middot; '
                             f'{len(listings)} {noun} listings across {len({l["brand"] for l in listings})} brands',
                             SUB)]]],
                 colWidths=[26 * mm, WIDTH - 26 * mm])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                              ('LINEBELOW', (0, 0), (-1, 0), 2, AMBER),
                              ('BOTTOMPADDING', (0, 0), (-1, -1), 7), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 5 * mm)]

    # -- 1. at a glance ------------------------------------------------------------
    glance = [_p('<b>Today at a glance</b>', _st('g', 11, True, GREEN)), Spacer(1, 2)]
    for size in sizes(product):
        found, _ = _ranked(listings, size, product)
        glance.append(_p(f'&bull; <b>Around {_size(size)}:</b> {_verdict(found, size)}', BODY))
    if prev_listings:
        moved = [c for c in _changes(listings, prev_listings, product) if c[4]]
        if moved:
            moved.sort(key=lambda c: -abs(c[4]))
            parts = [f'{escape(b)} {_size(s)} {"up" if ch > 0 else "down"} {rs(abs(ch), 2)}'
                     for b, s, _t, _n, ch in moved[:3]]
            glance.append(_p(f'&bull; <b>Since {prev.strftime("%d %b")}:</b> price per 100 g moved for '
                             f'{len(moved)} brand-size{"s" if len(moved) > 1 else ""} - '
                             + '; '.join(parts) + ('.' if len(moved) <= 3 else '; see section 4.'), BODY))
        else:
            glance.append(_p(f'&bull; <b>Since {prev.strftime("%d %b")}:</b> no brand\'s price per 100 g changed.',
                             BODY))
    story += [_box(glance, GREEN_SOFT, GREEN_LINE), Spacer(1, 4 * mm)]

    kpis = _kpis(listings, product)
    if kpis:
        story += [kpis, Spacer(1, 5 * mm)]

    # -- 2. each size, ranked -----------------------------------------------------
    widths = [12 * mm, 25 * mm, 42 * mm, 18 * mm, 16 * mm, 15 * mm, 11 * mm, 18 * mm, WIDTH - 157 * mm]
    for n, size in enumerate(sizes(product), 1):
        found, absent = _ranked(listings, size, product)
        block = [_p(f'{n}. Around {_size(size)} - who offers the best price?', H2), Spacer(1, 1.5 * mm),
                 _p(_verdict(found, size), BODY), Spacer(1, 2 * mm)]
        if found:
            top = max(r['per_100g'] for r in found)
            best = found[0]['per_100g']
            rows = [[_p('RANK', HEAD), _p('BRAND', HEAD), _p('PRODUCT', HEAD), _p('PACK', HEAD),
                     _p('YOU PAY', HEAD_R), _p('MRP', HEAD_R), _p('OFF', HEAD_R),
                     _p('PER 100 G', HEAD_R), _p('vs BEST PRICE', HEAD)]]
            extra = []
            for i, r in enumerate(found):
                gap = r['per_100g'] - best
                title = escape(r['title'][:70] + ('...' if len(r['title']) > 70 else ''))
                rows.append([
                    _p(f'<b>{ORDINAL[i]}</b>', CELL),
                    _brand(r['brand']),
                    _p(f'<link href="{escape(r["url"])}" color="#2563EB">{title}</link>', CELL),
                    _p(escape(r['pack_label']) + ('' if r['pack_grams'] == size else
                                                  '<br/><font size="6.5" color="#94A3B8">nearest pack</font>'),
                       CELL),
                    _p(rs(r['price']), CELL_RB),
                    _p(f'<strike>{rs(r["mrp"])}</strike>' if r['mrp'] and r['mrp'] > r['price'] else rs(r['mrp']),
                       _st('m', 8.3, False, FAINT, TA_RIGHT)),
                    _p(_off(r['discount_pct']), _st('o', 8.3, True, GREEN, TA_RIGHT)),
                    _p(rs(r['per_100g'], 2), _st('c', 8.6, True, GREEN if i == 0 else INK, TA_RIGHT)),
                    [_bar(r['per_100g'], top, BRAND_COLOUR.get(r['brand'], '#94A3B8')),
                     _p('<b>Best price</b>' if i == 0 else f'+{rs(gap, 2)} /100 g',
                        _st('g', 7.4, i == 0, GREEN if i == 0 else RED, leading=9))],
                ])
                if i == 0:
                    extra.append(('BACKGROUND', (0, i + 1), (-1, i + 1), GREEN_SOFT))
                elif r['brand'] == HOME:
                    extra.append(('BACKGROUND', (0, i + 1), (-1, i + 1), AMBER_SOFT))
                if not r['in_stock']:
                    rows[-1][1] = _p(f'{rows[-1][1].text}<br/><font size="6.5" color="#BE123C">out of stock</font>',
                                     CELL)
            block.append(_table(rows, widths, extra=extra))
        if absent:
            block += [Spacer(1, 1.5 * mm),
                      _p(f'Not in this comparison - no pack near {_size(size)} on Flipkart: '
                         f'{", ".join(absent)}.', SMALL)]
        story += [KeepTogether(block), Spacer(1, 5 * mm)]

    # -- 3. what changed -----------------------------------------------------------
    n = len(sizes(product)) + 1
    if prev_listings:
        ch = _changes(listings, prev_listings, product)
        block = [_p(f'{n}. What changed since {prev.strftime("%A, %d %B")}', H2), Spacer(1, 1.5 * mm),
                 _p('Price per 100 g of each brand\'s pack near each size, on both days. '
                    'Down is cheaper for the buyer.', SMALL), Spacer(1, 2 * mm)]
        if ch:
            rows = [[_p('BRAND', HEAD), _p('SIZE', HEAD), _p(prev.strftime('%d %b').upper(), HEAD_R),
                     _p(day.strftime('%d %b').upper(), HEAD_R), _p('CHANGE', HEAD_R), _p('', HEAD)]]
            for b, s, then, now, c in ch:
                word, col = (('Up', RED) if c > 0 else ('Down', GREEN) if c < 0 else ('Same', FAINT))
                rows.append([_brand(b), _p(_size(s), CELL), _p(rs(then, 2), CELL_R), _p(rs(now, 2), CELL_RB),
                             _p(('+' if c > 0 else '') + rs(c, 2) if c else '-', _st('ch', 8.3, True, col, TA_RIGHT)),
                             _p(f'<b>{word}</b>', _st('w', 8.3, True, col))])
            block.append(_table(rows, [36 * mm, 22 * mm, 30 * mm, 30 * mm, 30 * mm, WIDTH - 148 * mm]))
        else:
            block.append(_p('No brand was listed near the same size on both days.', BODY))
        story += [KeepTogether(block), Spacer(1, 5 * mm)]
        n += 1

    # -- 4. every listing ---------------------------------------------------------
    every = sorted((_row(l) for l in listings), key=lambda r: r['per_100g'])
    rows = [[_p('#', HEAD), _p('BRAND', HEAD), _p('PRODUCT (click to open)', HEAD), _p('PACK', HEAD),
             _p('PRICE', HEAD_R), _p('MRP', HEAD_R), _p('OFF', HEAD_R), _p('PER 100 G', HEAD_R),
             _p('STOCK', HEAD)]]
    extra = []
    for i, r in enumerate(every, 1):
        rows.append([_p(str(i), _st('i', 7.8, False, FAINT)), _brand(r['brand'], bold=False),
                     _p(f'<link href="{escape(r["url"])}" color="#2563EB">{escape(r["title"][:80])}</link>',
                        _st('t', 7.6, leading=9.4)),
                     _p(escape(r['pack_label']), CELL), _p(rs(r['price']), CELL_RB),
                     _p(rs(r['mrp']), _st('m2', 8.3, False, FAINT, TA_RIGHT)),
                     _p(_off(r['discount_pct']), _st('o2', 8.3, False, GREEN, TA_RIGHT)),
                     _p(rs(r['per_100g'], 2), CELL_RB),
                     _p('In stock' if r['in_stock'] else '<font color="#BE123C">Out</font>', _st('s', 7.6, False, MUTED))])
        if r['brand'] == HOME:
            extra.append(('BACKGROUND', (0, i), (-1, i), AMBER_SOFT))
    story += [_p(f'{n}. Every {noun} listing read that day ({len(every)})', H2), Spacer(1, 1.5 * mm),
              _p('Lowest price per 100 g first. APIS rows are shaded amber.', SMALL), Spacer(1, 2 * mm),
              _table(rows, [8 * mm, 23 * mm, 57 * mm, 17 * mm, 15 * mm, 15 * mm, 11 * mm, 18 * mm,
                            WIDTH - 164 * mm], extra=extra),
              Spacer(1, 5 * mm)]

    # -- 5. how to read --------------------------------------------------------------
    story.append(KeepTogether([_box([
        _p('<b>How to read this report</b>', _st('h', 10, True, INK)), Spacer(1, 2),
        _p('&bull; <b>You pay</b> is the selling price on Flipkart after its discount; <b>MRP</b> is the printed price.', SMALL),
        _p('&bull; Packs come in different sizes, so brands are ranked by what <b>100 g</b> costs - lower is '
           'better value for the buyer. "Nearest pack" means the brand does not sell that exact size.', SMALL),
        _p('&bull; A brand is compared at a size only if it sells a pack between half and 1.6 times that size; '
           'otherwise it is listed as having no pack near it.', SMALL),
        _p('&bull; Green = best price. Amber = APIS. Every price is a real listing read that day; nothing is '
           'estimated. Click a product name to open it on Flipkart.', SMALL),
    ], colors.HexColor('#F8FAFC'), RULE)]))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7.5)
        canvas.setFillColor(FAINT)
        canvas.drawString(MARGIN, 9 * mm, f'APIS India - Market Watch - {day.strftime("%d %b %Y")} - '
                                          'internal use only')
        canvas.drawRightString(PAGE_W - MARGIN, 9 * mm, f'Page {doc.page}')
        canvas.restoreState()

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=12 * mm, bottomMargin=16 * mm,
                            title=f'Market Watch - Daily {label} Price Report - {day.isoformat()}',
                            author='APIS India Intranet')
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return f'APIS-Market-Watch-{label.replace(" ", "-")}-{day.isoformat()}.pdf', buf.getvalue()
