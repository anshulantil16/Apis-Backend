"""Arrears Compensation Structure - the PDF, and the mail that carries it.

Built alongside pms/offer_letter.py and deliberately close to it in shape, so
whoever maintains one can read the other. What differs is what the document
says, and that difference is the whole point:

  * An offer letter states a monthly and annual CTC going forward. This states
    ONE column - arrears already owed for a period that has passed.
  * The deductions here are the EMPLOYEE's (their PF, ESI, mediclaim, NPS,
    income tax), not the employer's contributions. They come off the total.
  * It ends at what actually reaches the bank: Gross, less deductions, plus
    the quarterly payouts.

On the arithmetic: In Hand is Gross MINUS deductions. The source spreadsheet
this was modelled on adds them instead, so its "Total In Hand" came out above
its own Gross - impossible for a figure that is defined as what is left after
deductions. Computed correctly here. See the note in views/arrears_letters.py.
"""
from io import BytesIO

from django.conf import settings
from django.core.mail import EmailMessage
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                NextPageTemplate, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

# Component key, section, and the label printed on the row. Section drives
# which subtotal a row rolls into, so nothing is summed by position.
#
# 'earn'  -> GROSS EARNINGS (A)
# 'reimb' -> Reimbursement Salary (B)
# 'ded'   -> Total Employee Deductions, SUBTRACTED from A+B
# 'other' -> paid quarterly, added after In Hand
ARREARS_COMPONENTS = [
    ('basic',     'earn',  'Basic Salary (Inclusive of DA/VDA)'),
    ('hra',       'earn',  'House Rent Allowance (HRA)'),
    ('cea',       'earn',  'Child Education Allowance (CEA) @'),
    ('lta',       'earn',  'Leave Travel Allowance (LTA)'),
    ('special',   'earn',  'Special Allowance (Flexi Pay)'),

    ('meal',      'reimb', 'Meal Vocher Reimbursement'),
    ('telephone', 'reimb', 'Telephone-Handset / Accessories & Internet Reimbursement'),
    ('health',    'reimb', 'Health & Wellness Reimbursement'),
    ('books',     'reimb', 'Books, Periodicals & Professional Development Reimbursement'),
    ('uniform',   'reimb', 'Uniform & Attire Reimbursement'),
    ('fuel',      'reimb', 'Fuel & Vehicle Maintenance Reimbursement'),
    ('driver',    'reimb', 'Driver/Chauffer Salary'),
    ('car_lease', 'reimb', 'Car Lease / Company Car Benefit'),

    ('emp_pf',    'ded',   'Employe PF Contribution'),
    ('emp_esi',   'ded',   'Employee ESI Contribution'),
    ('mediclaim', 'ded',   'Mediclaim Charges'),
    ('nps',       'ded',   'NPS'),
    ('income_tax', 'ded',  'Income Tax'),

    ('variable',  'other', 'Variable / Performance Pay $'),
    ('gift',      'other', 'Gift Reimbursement'),
]

# Column heading for each component in the upload template, so the sheet an
# admin fills in and the rows printed here cannot drift apart.
COMPONENT_HEADERS = {
    'basic': 'Basic Salary', 'hra': 'HRA', 'cea': 'CEA', 'lta': 'LTA',
    'special': 'Special Allowance', 'meal': 'Meal Voucher',
    'telephone': 'Telephone/Internet', 'health': 'Health & Wellness',
    'books': 'Books/Prof Dev', 'uniform': 'Uniform & Attire',
    'fuel': 'Fuel & Vehicle', 'driver': 'Driver Salary', 'car_lease': 'Car Lease',
    'emp_pf': 'Employee PF', 'emp_esi': 'Employee ESI', 'mediclaim': 'Mediclaim',
    'nps': 'NPS', 'income_tax': 'Income Tax',
    'variable': 'Variable / Performance Pay', 'gift': 'Gift Reimbursement',
}

EMP_FIELDS = [
    ('employee_name', 'Employee Name'),
    ('department',    'Department'),
    ('designation',   'Designation'),
    ('cadre',         'Cadre'),
    ('grade',         'Grade'),
    ('paid_days',     'Paid Days (Monthly)'),
]

# Palette lifted from the sheet this replaces, so a recipient who has seen the
# spreadsheet recognises the document.
BLUE = HexColor('#00B0F0')
GREY = HexColor('#808080')
GREEN = HexColor('#92D050')
YELLOW = HexColor('#FFFF00')
PEACH = HexColor('#FCE4D6')
LINE = HexColor('#000000')


# The shrink ladder, and the scale each page shape last settled on.
#
# Every letter in a run shares a component list and a month count, so they all
# land on the same scale; remembering it turns a three- or four-build search
# into one build for every letter after the first. Bounded by construction -
# a handful of page shapes, one small tuple key each - and only ever a hint,
# since the result is still measured before it is used.
SCALES = (1.0, 0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72, 0.68, 0.64,
          0.60, 0.56, 0.52)
_SCALE_MEMO = {}


def _f(v):
    """A component amount as a number. Blank, None and junk all mean zero."""
    try:
        return round(float(str(v).replace(',', '').strip() or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _indian(n):
    """1,90,250 - lakh grouping, not 190,250."""
    neg = n < 0
    whole = f'{abs(int(round(n))):d}'
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ','.join(parts) + ',' + tail
    return ('-' if neg else '') + whole


def _rs(n):
    """A rupee cell.

    "Rs." rather than the ₹ sign, matching the offer letters. None of
    reportlab's built-in fonts carry U+20B9, so ₹ renders as a black box; the
    only fix is shipping a TTF, and a system font would work on a developer's
    Windows machine and be missing on the Ubuntu server.

    Zero prints as a dash, the way the source sheet does - an explicit "nil"
    reads better on a salary document than 0.00.
    """
    return 'Rs.  -' if round(n or 0, 2) == 0 else f'Rs.  {_indian(n)}'


def totals(breakup):
    """Every subtotal on the document, computed once and used everywhere.

    Kept apart from rendering so the API can return the same numbers the PDF
    prints without either recomputing the other's arithmetic.
    """
    by_section = {'earn': 0.0, 'reimb': 0.0, 'ded': 0.0, 'other': 0.0}
    for key, section, _label in ARREARS_COMPONENTS:
        by_section[section] += _f((breakup or {}).get(key))

    gross_earnings = round(by_section['earn'], 2)
    reimbursement = round(by_section['reimb'], 2)
    gross = round(gross_earnings + reimbursement, 2)
    deductions = round(by_section['ded'], 2)
    in_hand = round(gross - deductions, 2)
    other = round(by_section['other'], 2)
    return {
        'gross_earnings': gross_earnings,
        'reimbursement': reimbursement,
        'gross': gross,
        'deductions': deductions,
        'in_hand': in_hand,
        'other_payments': other,
        'total_with_other': round(in_hand + other, 2),
    }


def month_rows(monthly):
    """The month-wise distribution, normalised and in the order given.

    `monthly` is what the upload parser built: a list of
    {'month': 'Apr 2026', 'present_days': '30',
     'components': {key: {'old': x, 'new': y}}}.

    Each month comes back with every component present (missing ones are
    zero) and the difference computed rather than read - a difference that
    could disagree with the two figures beside it is a difference nobody
    can trust.
    """
    out = []
    for m in (monthly or []):
        comps = (m or {}).get('components') or {}
        row = {'month': str((m or {}).get('month', '') or '').strip(),
               'present_days': str((m or {}).get('present_days', '') or '').strip(),
               'components': {}}
        for key, _section, _label in ARREARS_COMPONENTS:
            c = comps.get(key) or {}
            old = _f(c.get('old'))
            new = _f(c.get('new'))
            row['components'][key] = {'old': old, 'new': new,
                                      'diff': round(new - old, 2)}
        row.update(month_totals(row))
        out.append(row)
    return out


def month_totals(row):
    """One month's net arrears: what the differences add up to.

    Deductions are the employee's own, so a rise in them takes money off the
    arrears rather than adding to it - the same sign convention `totals()`
    uses on the summary page, so the two pages cannot tell different stories.
    """
    by_section = {'earn': 0.0, 'reimb': 0.0, 'ded': 0.0, 'other': 0.0}
    for key, section, _label in ARREARS_COMPONENTS:
        by_section[section] += row['components'][key]['diff']
    gross = by_section['earn'] + by_section['reimb']
    return {
        'diff_gross': round(gross, 2),
        'diff_deductions': round(by_section['ded'], 2),
        'diff_other': round(by_section['other'], 2),
        'net': round(gross - by_section['ded'] + by_section['other'], 2),
    }


def totals_from_months(monthly):
    """Arrears owed per component: each month's difference, added up.

    This is what the summary page's single column means once a month-wise
    breakdown exists, so it is computed from the months rather than typed in
    a second time - two independent inputs for one figure is two chances to
    disagree, on a document about money somebody is owed.
    """
    rows = month_rows(monthly)
    if not rows:
        return {}
    out = {}
    for key, _section, _label in ARREARS_COMPONENTS:
        out[key] = round(sum(r['components'][key]['diff'] for r in rows), 2)
    return out


def generate_arrears_pdf(letter):
    """The Arrears Compensation Structure for one person, as a PDF buffer.

    `letter` is an ArrearsLetter (or anything with the same attributes), so
    this can be previewed without a saved row.
    """
    buf = BytesIO()

    # Two page templates rather than one. The summary is a tall, narrow table
    # and belongs on portrait; the month-wise distribution is the opposite
    # shape - three columns per month - and squeezing it into portrait width
    # either wraps every figure or shrinks it past reading. So that page, and
    # only that page, turns sideways.
    LAND = landscape(A4)
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        title=f'Arrears Compensation Structure - {letter.employee_name}',
    )
    p_w, p_h = A4[0] - 1.8 * inch, A4[1] - 1.2 * inch
    l_w, l_h = LAND[0] - 1.2 * inch, LAND[1] - 1.0 * inch
    doc.addPageTemplates([
        PageTemplate(id='portrait', pagesize=A4, frames=[
            Frame(0.9 * inch, 0.6 * inch, p_w, p_h, id='pf')]),
        PageTemplate(id='wide', pagesize=LAND, frames=[
            Frame(0.6 * inch, 0.5 * inch, l_w, l_h, id='lf')]),
    ])

    ss = getSampleStyleSheet()
    months = month_rows(getattr(letter, 'monthly_breakup', None))
    master = getattr(letter, 'master_breakup', None) or {}

    # With a month-wise breakdown in hand the summary column is the sum of the
    # monthly differences, not a separately typed figure - one number, one
    # source. Without one, the uploaded totals stand as before.
    breakup = totals_from_months(months) or (letter.salary_breakup or {})
    t = totals(breakup)

    def build(s):
        """The whole letter at shrink factor `s` (1.0 = full size).

        Type sizes and cell padding both scale, because a table row's height
        is leading plus padding - shrinking only the type leaves the padding
        holding the page open and buys almost nothing.
        """
        label = ParagraphStyle('lbl', parent=ss['Normal'], fontSize=8.5 * s, leading=11 * s)
        label_b = ParagraphStyle('lblb', parent=label, fontName='Helvetica-Bold')
        band = ParagraphStyle('band', parent=ss['Normal'], fontSize=9.5 * s, leading=12 * s,
                              fontName='Helvetica-Bold', alignment=TA_CENTER)
        title = ParagraphStyle('t', parent=ss['Normal'], fontSize=14 * s, leading=17 * s,
                               fontName='Helvetica-Bold', alignment=TA_CENTER)
        sub = ParagraphStyle('s', parent=ss['Normal'], fontSize=10 * s, leading=13 * s,
                             alignment=TA_CENTER)

        # Column widths stay put: it is the height that overflows, and
        # narrowing the label column would only wrap long component names
        # onto a second line and make the table taller again.
        W = [3.9 * inch, 2.2 * inch]
        rows, styles = [], []

        def band_row(text, fill, fg=colors.white):
            rows.append([Paragraph(f'<font color="{fg}">{text}</font>', band), ''])
            i = len(rows) - 1
            # .extend, not += : inside a closure "styles += ..." rebinds the name
            # and Python treats it as local, so the whole build fails.
            styles.extend([('SPAN', (0, i), (1, i)), ('BACKGROUND', (0, i), (1, i), fill)])

        def money_row(text, amount, *, fill=None, bold=False, fg=None):
            style = label_b if bold else label
            txt = f'<font color="{fg}">{text}</font>' if fg else text
            amt = f'<font color="{fg}">{_rs(amount)}</font>' if fg else _rs(amount)
            rows.append([Paragraph(txt, style), Paragraph(amt, style)])
            i = len(rows) - 1
            styles.append(('ALIGN', (1, i), (1, i), 'RIGHT'))
            if fill:
                styles.append(('BACKGROUND', (0, i), (1, i), fill))
            return i

        def total_row(text, amount, fill=GREY):
            """A subtotal: centred white label on a filled band, amount alongside."""
            rows.append([Paragraph(f'<font color="#FFFFFF">{text}</font>', band),
                         Paragraph(f'<font color="#FFFFFF">{_rs(amount)}</font>', band)])
            i = len(rows) - 1
            styles.append(('BACKGROUND', (0, i), (1, i), fill))

        # ── masthead ────────────────────────────────────────────────────────
        rows.append([Paragraph('APIS INDIA LIMITED', title), ''])
        styles += [('SPAN', (0, 0), (1, 0)), ('BACKGROUND', (0, 0), (1, 0), YELLOW)]
        rows.append([Paragraph('<u>Arrears Compensation Structure</u>', sub), ''])
        styles.append(('SPAN', (0, 1), (1, 1)))

        # ── employee details ────────────────────────────────────────────────
        band_row('Employee Details', BLUE)
        for attr, text in EMP_FIELDS:
            value = getattr(letter, attr, '') or ''
            rows.append([Paragraph(text, label_b), Paragraph(str(value), label)])

        # ── the money ───────────────────────────────────────────────────────
        rows.append([Paragraph('<font color="#FFFFFF">Salary Component #</font>', band),
                     Paragraph('<font color="#FFFFFF">Total Arrears Amount</font>', band)])
        i = len(rows) - 1
        styles.append(('BACKGROUND', (0, i), (1, i), BLUE))

        for key, section, text in ARREARS_COMPONENTS:
            if section == 'earn':
                money_row(text, _f(breakup.get(key)))
        total_row('GROSS EARNINGS (A)', t['gross_earnings'])

        for key, section, text in ARREARS_COMPONENTS:
            if section == 'reimb':
                # Every reimbursement row shares one fill, car lease included -
                # a single unshaded row in the middle of a block reads as an
                # error rather than a distinction.
                money_row(text, _f(breakup.get(key)), fill=PEACH)
        total_row('Reimbursement Salary (B)', t['reimbursement'])
        total_row('Gross Salary( A+B)', t['gross'])

        band_row('Employee Deductions', BLUE)
        for key, section, text in ARREARS_COMPONENTS:
            if section == 'ded':
                money_row(text, _f(breakup.get(key)))
        total_row('Total Employee Deductions', t['deductions'])
        total_row('Total In Hand Salary', t['in_hand'], fill=GREEN)

        band_row('Other Payments (Payout on Quarterly Basis)', BLUE)
        for key, section, text in ARREARS_COMPONENTS:
            if section == 'other':
                money_row(text, _f(breakup.get(key)))
        total_row('TOTAL  In Hand with Other Payments CTC ( Monthly)',
                  t['total_with_other'], fill=GREEN)

        styles += [
            ('GRID', (0, 0), (-1, -1), 0.75, LINE),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 6 * s),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6 * s),
            ('TOPPADDING', (0, 0), (-1, -1), 4 * s),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4 * s),
        ]
        table = Table(rows, colWidths=W, repeatRows=0)
        table.setStyle(TableStyle(styles))

        note = Paragraph(
            'Note: This is a computer generated Compensation Component Break-up Structure. '
            'In case of any discrepancy, please contact P &amp; C Dept.',
            ParagraphStyle('n', parent=ss['Normal'], fontSize=7.5 * s, leading=10 * s))
        sign = Paragraph('APIS - Approved_P &amp; C',
                         ParagraphStyle('sg', parent=ss['Normal'], fontSize=7.5 * s,
                                        leading=10 * s, alignment=2))

        return [table, Spacer(1, 10 * s), KeepTogether([note, Spacer(1, 6 * s), sign])]

    # ── Auto-fit to a single page ───────────────────────────────────────────
    # Same approach as the appraisal letter (offer_letter.py): build the story
    # at progressively smaller scales and keep the first one that measures
    # inside the printable height. A salary structure split across two pages
    # is not just untidy - the totals land on their own page, away from the
    # rows they total, which is how a reader loses the thread of the figures.
    #
    # The list of components is fixed, so a letter overflows because of long
    # employee detail values, not an unbounded number of rows; the scales
    # below cover that with room to spare and stay legible in print.
    avail_w, avail_h = p_w, p_h

    def _measure(flows):
        total = 0.0
        for fl in flows:
            # KeepTogether reports 0 when wrapped on its own, so measure what
            # it holds instead of trusting its own answer.
            content = getattr(fl, '_content', None)
            if content:
                total += _measure(content)
                continue
            try:
                _, h = fl.wrap(avail_w, avail_h)
            except Exception:
                h = 0
            total += h
        return total

    def _fit(builder, height, memo_key):
        """First scale whose story measures inside `height`, else the last.

        Building a story is most of the cost of making one of these, and the
        search used to build three or four per page - on a run of a thousand
        people that was minutes of pure rework, because every letter in a
        batch has the same component list and the same number of months and
        so lands on the same scale.

        So the answer is remembered and tried first. It is still measured, and
        the scan still walks down from there if this particular letter needs
        more room, so the memo can only ever save work - it can never let an
        oversized letter through.
        """
        start = _SCALE_MEMO.get(memo_key)
        if start is not None:
            out = builder(start)
            if _measure(out) <= height * 0.97:
                return out

        for sc in SCALES:
            out = builder(sc)
            # 0.97 rather than a flush 1.0: wrap() measures a table accurately
            # but the frame still needs a hair of clearance, and a letter that
            # spills by two points is exactly as broken as one that spills by
            # an inch.
            if _measure(out) <= height * 0.97:
                _SCALE_MEMO[memo_key] = sc
                return out
        return out

    # Keyed on what actually drives the height: the longest detail value, since
    # the row count is fixed. Bucketed, so near-identical letters share an entry
    # instead of each minting its own.
    longest = max([len(str(getattr(letter, a, '') or '')) for a, _l in EMP_FIELDS] or [0])
    story = _fit(build, avail_h, ('summary', longest // 8))

    # ── Page 2: the month-wise distribution, when there is one ──────────────
    if months:
        avail_w, avail_h = l_w, l_h          # _measure closes over these
        story += [NextPageTemplate('wide'), PageBreak()]
        widest_digits = max(
            [len(_indian(c[f])) for m in months for c in m['components'].values()
             for f in ('old', 'new', 'diff')] or [1])
        story += _fit(lambda sc: _distribution(ss, letter, months, master,
                                               breakup, l_w, sc), l_h,
                      ('dist', len(months), widest_digits))

    doc.build(story)
    buf.seek(0)
    return buf


def _distribution(ss, letter, months, master, breakup, width, s):
    """The month-wise grid: what each component earned old vs new, per month.

    One column group per month, so a reader can see where the arrears came
    from rather than only what they add up to. The last column is that
    component's total across every month, which is exactly the figure the
    summary page prints for it - the two pages are the same arithmetic seen
    from two directions, and printing both is what makes either checkable.
    """
    n_money = 2 + 3 * len(months) + 1           # master pair, months, total
    # The label column gives up width as months are added, but never below a
    # point where component names become unreadable stacks of single words.
    label_w = max(1.5 * inch, min(2.4 * inch, width - n_money * 0.44 * inch))
    money_w = (width - label_w) / n_money
    W = [label_w] + [money_w] * n_money

    # A money column has to hold two things that cannot wrap on a space: the
    # word "Difference", and a figure like 1,23,456. Left at a nominal size
    # they broke mid-word ("Differenc e") and mid-number, which on a document
    # about somebody's pay reads as a fault rather than a tight fit. So both
    # are sized to the column that actually exists.
    #
    # 0.95 because a word measured at exactly the available width still wraps:
    # reportlab breaks on >=, not >.
    cell_w = (money_w - 6 * s) * 0.95

    def _size_to_fit(text, font, nominal):
        return min(nominal, cell_w / stringWidth(text, font, 1))

    # Shortened rather than shrunk past reading. Beside "Earned Old" and
    # "Earned New" the abbreviation is unambiguous, and a 4pt full word helps
    # nobody.
    diff_label = 'Difference'
    head_size = _size_to_fit(diff_label, 'Helvetica-Bold', 7.5 * s)
    if head_size < 5.5:
        diff_label = 'Diff.'
        head_size = _size_to_fit(diff_label, 'Helvetica-Bold', 7.5 * s)

    # Measured against the widest figure this employee actually has, not
    # against the widest one imaginable - a letter with small numbers should
    # not be set in small type because a bigger number could have existed.
    widest = max([stringWidth(_indian(v), 'Helvetica-Bold', 1) for v in
                  [_f((master.get(k) or {}).get(side))
                   for k, _s2, _l in ARREARS_COMPONENTS for side in ('old', 'new')]
                  + [c[f] for m in months for c in m['components'].values()
                     for f in ('old', 'new', 'diff')]
                  + [_f(breakup.get(k)) for k, _s2, _l in ARREARS_COMPONENTS]
                  + [m['net'] for m in months]
                  + [sum(m['net'] for m in months)]] or [1.0])
    num_size = min(7 * s, cell_w / widest) if widest else 7 * s

    head = ParagraphStyle('dh', parent=ss['Normal'], fontSize=head_size,
                          leading=head_size * 1.2, fontName='Helvetica-Bold',
                          alignment=TA_CENTER, textColor=colors.white)
    lbl = ParagraphStyle('dl', parent=ss['Normal'], fontSize=7 * s, leading=8.5 * s)
    lbl_b = ParagraphStyle('dlb', parent=lbl, fontName='Helvetica-Bold')
    num = ParagraphStyle('dn', parent=ss['Normal'], fontSize=num_size,
                         leading=num_size * 1.22, alignment=2)
    num_b = ParagraphStyle('dnb', parent=num, fontName='Helvetica-Bold')
    band = ParagraphStyle('db', parent=ss['Normal'], fontSize=7.5 * s,
                          leading=9 * s, fontName='Helvetica-Bold',
                          textColor=colors.white)
    title = ParagraphStyle('dt', parent=ss['Normal'], fontSize=12 * s,
                           leading=15 * s, fontName='Helvetica-Bold',
                           alignment=TA_CENTER)
    sub = ParagraphStyle('ds', parent=ss['Normal'], fontSize=8 * s,
                         leading=11 * s, alignment=TA_CENTER)

    def n(v):
        """A plain figure. No "Rs." here - it would repeat on every one of a
        few hundred cells and cost the width the figures themselves need; the
        caption above the table says the unit once."""
        return '-' if round(v or 0, 2) == 0 else _indian(v)

    rows, styles = [], []

    # ── two header rows ────────────────────────────────────────────────────
    h0 = [Paragraph('Salary Component', head), Paragraph('Master', head), '']
    h1 = ['', Paragraph('Old', head), Paragraph('New', head)]
    for m in months:
        days = f" (Present - {m['present_days']} days)" if m['present_days'] else ''
        h0 += [Paragraph(f"Month - {m['month']}{days}", head), '', '']
        h1 += [Paragraph('Earned Old', head), Paragraph('Earned New', head),
               Paragraph(diff_label, head)]
    h0.append(Paragraph('Total Arrears', head))
    h1.append('')
    rows += [h0, h1]
    styles += [
        ('BACKGROUND', (0, 0), (-1, 1), BLUE),
        ('SPAN', (0, 0), (0, 1)),                       # Salary Component
        ('SPAN', (1, 0), (2, 0)),                       # Master
        ('SPAN', (n_money, 0), (n_money, 1)),           # Total Arrears
        ('VALIGN', (0, 0), (-1, 1), 'MIDDLE'),
    ]
    for i in range(len(months)):
        c = 3 + 3 * i
        styles.append(('SPAN', (c, 0), (c + 2, 0)))

    def band_row(text):
        rows.append([Paragraph(text, band)] + [''] * n_money)
        i = len(rows) - 1
        styles.extend([('SPAN', (0, i), (-1, i)),
                       ('BACKGROUND', (0, i), (-1, i), GREY)])

    SECTIONS = [('earn', 'Gross Earnings'),
                ('reimb', 'Reimbursements'),
                ('ded', 'Employee Deductions'),
                ('other', 'Other Payments (Quarterly)')]
    for section, heading in SECTIONS:
        band_row(heading)
        for key, sec, text in ARREARS_COMPONENTS:
            if sec != section:
                continue
            m_old = _f((master.get(key) or {}).get('old'))
            m_new = _f((master.get(key) or {}).get('new'))
            line = [Paragraph(text, lbl), Paragraph(n(m_old), num),
                    Paragraph(n(m_new), num)]
            for m in months:
                c = m['components'][key]
                line += [Paragraph(n(c['old']), num), Paragraph(n(c['new']), num),
                         Paragraph(n(c['diff']), num_b)]
            line.append(Paragraph(n(_f(breakup.get(key))), num_b))
            rows.append(line)
            if section == 'reimb':
                i = len(rows) - 1
                styles.append(('BACKGROUND', (0, i), (-1, i), PEACH))

    # ── the bottom line, per month and overall ─────────────────────────────
    net = [Paragraph('NET ARREARS', lbl_b), '', '']
    for m in months:
        net += ['', '', Paragraph(n(m['net']), num_b)]
    net.append(Paragraph(n(sum(m['net'] for m in months)), num_b))
    rows.append(net)
    i = len(rows) - 1
    styles += [('BACKGROUND', (0, i), (-1, i), GREEN), ('SPAN', (0, i), (2, i))]

    styles += [
        ('GRID', (0, 0), (-1, -1), 0.6, LINE),
        ('VALIGN', (0, 2), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 3 * s),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3 * s),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5 * s),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5 * s),
    ]
    table = Table(rows, colWidths=W, repeatRows=2)
    table.setStyle(TableStyle(styles))

    return [
        Paragraph('Arrears Distribution - Month Wise', title),
        Paragraph(f'{letter.employee_name} &nbsp;|&nbsp; All figures in Rupees', sub),
        Spacer(1, 6 * s),
        table,
    ]


def send_arrears_email(employee_email, employee_name, pdf_buffer, *,
                       period='', connection=None, filename=None):
    """Mail one arrears statement.

    Short on purpose. This document settles money already owed for a period
    that has passed - the reader wants the figure and who to ask about it,
    not a message about the company's transformation journey.
    """
    period_line = f' for {period}' if period else ''
    subject = f'APIS India — Arrears Compensation Statement{period_line}'
    body = f"""
<html><body style="font-family: Calibri, Arial, sans-serif; font-size: 14px; color: #202020;">
<div style="max-width: 640px;">
    <p>Dear {employee_name},</p>

    <p>Please find attached your <b>Arrears Compensation Structure</b>{period_line},
    setting out the arrears due to you and how they have been arrived at &mdash;
    earnings, reimbursements, deductions, and the amount payable.</p>

    <p>Kindly review it. If anything does not match your records, write to the
    People &amp; Culture team and we will look into it.</p>

    <p>Warm regards,<br>
    People &amp; Culture<br>
    <b>APIS INDIA LIMITED</b></p>

    <p style="font-size: 11px; color: #707070;">This is a computer generated
    compensation break-up. In case of any discrepancy, please contact the P &amp; C Dept.</p>
</div>
</body></html>
"""
    mail = EmailMessage(
        subject=subject, body=body,
        from_email=settings.OFFER_LETTER_EMAIL_HOST_USER,
        to=[employee_email], connection=connection,
    )
    mail.content_subtype = 'html'
    pdf_buffer.seek(0)
    mail.attach(filename or f'APIS_Arrears_{employee_name.replace(" ", "_")}.pdf',
                pdf_buffer.read(), 'application/pdf')
    mail.send(fail_silently=False)
