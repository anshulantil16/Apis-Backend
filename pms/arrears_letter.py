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
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

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


def generate_arrears_pdf(letter):
    """The Arrears Compensation Structure for one person, as a PDF buffer.

    `letter` is an ArrearsLetter (or anything with the same attributes), so
    this can be previewed without a saved row.
    """
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=0.9 * inch, rightMargin=0.9 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        title=f'Arrears Compensation Structure - {letter.employee_name}',
    )
    ss = getSampleStyleSheet()
    label = ParagraphStyle('lbl', parent=ss['Normal'], fontSize=8.5, leading=11)
    label_b = ParagraphStyle('lblb', parent=label, fontName='Helvetica-Bold')
    band = ParagraphStyle('band', parent=ss['Normal'], fontSize=9.5, leading=12,
                          fontName='Helvetica-Bold', alignment=TA_CENTER)
    title = ParagraphStyle('t', parent=ss['Normal'], fontSize=14, leading=17,
                           fontName='Helvetica-Bold', alignment=TA_CENTER)
    sub = ParagraphStyle('s', parent=ss['Normal'], fontSize=10, leading=13,
                         alignment=TA_CENTER)

    t = totals(letter.salary_breakup or {})
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

    # ── masthead ────────────────────────────────────────────────────────────
    rows.append([Paragraph('APIS INDIA LIMITED', title), ''])
    styles += [('SPAN', (0, 0), (1, 0)), ('BACKGROUND', (0, 0), (1, 0), YELLOW)]
    rows.append([Paragraph('<u>Arrears Compensation Structure</u>', sub), ''])
    styles.append(('SPAN', (0, 1), (1, 1)))

    # ── employee details ────────────────────────────────────────────────────
    band_row('Employee Details', BLUE)
    for attr, text in EMP_FIELDS:
        value = getattr(letter, attr, '') or ''
        rows.append([Paragraph(text, label_b), Paragraph(str(value), label)])

    # ── the money ───────────────────────────────────────────────────────────
    rows.append([Paragraph('<font color="#FFFFFF">Salary Component #</font>', band),
                 Paragraph('<font color="#FFFFFF">Total Arrears Amount</font>', band)])
    i = len(rows) - 1
    styles.append(('BACKGROUND', (0, i), (1, i), BLUE))

    breakup = letter.salary_breakup or {}
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
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    table = Table(rows, colWidths=W, repeatRows=0)
    table.setStyle(TableStyle(styles))

    note = Paragraph(
        'Note: This is a computer generated Compensation Component Break-up Structure. '
        'In case of any discrepancy, please contact P &amp; C Dept.',
        ParagraphStyle('n', parent=ss['Normal'], fontSize=7.5, leading=10))
    sign = Paragraph('APIS - Approved_P &amp; C',
                     ParagraphStyle('sg', parent=ss['Normal'], fontSize=7.5,
                                    leading=10, alignment=2))

    doc.build([table, Spacer(1, 10), KeepTogether([note, Spacer(1, 6), sign])])
    buf.seek(0)
    return buf


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
