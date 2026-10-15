"""The compensation break-up as a PDF — "Compensation Break-up Structure,
Annexure A", laid out like P & C's approved sheet.

Takes a candidate and the dict calculator.calculate() returned; draws, does
not decide. Amounts are written "Rs." rather than with the rupee sign, which
the built-in PDF fonts do not carry.
"""
import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LOGO_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'apis_logo.png')

AMBER = colors.HexColor('#F59E0B')
AMBER_SOFT = colors.HexColor('#FEF3C7')
AMBER_DARK = colors.HexColor('#B45309')
SLATE = colors.HexColor('#334155')
GRID = colors.HexColor('#E2E8F0')

FOOTNOTES = [
    '# Tax applicability as per Income Tax Act & shall be borne by the employee.',
    "$ Variable pay is paid as per company's variable pay policy on quarterly basis.",
    '@ Children Education Allowance is applicable for a maximum 2 children only.',
    'Car Lease as per car lease policy.',
    'NPS: Max contribution up to 10% of Basic in old tax regime & 14% in new tax regime.',
    'Gratuity amount payment as per Code on Social Security, 2020 (PGA 1972).',
]


def rs(n):
    """Rs. with Indian digit grouping: 1234567 -> Rs. 12,34,567."""
    n = int(n or 0)
    sign, s = ('-' if n < 0 else ''), str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ','.join(groups) + ',' + tail
    return f'{sign}Rs. {s}'


def build_pdf(candidate, b) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=9 * mm, bottomMargin=9 * mm,
                            title=f'CTC Break-up - {candidate.candidate_name}',
                            author='APIS India Limited - People & Culture')
    width = A4[0] - 28 * mm
    ss = getSampleStyleSheet()
    small = ParagraphStyle('small', parent=ss['Normal'], fontSize=7, leading=8.5, textColor=SLATE)
    cell = ParagraphStyle('cell', parent=ss['Normal'], fontSize=8, leading=9.5)
    cell_b = ParagraphStyle('cellb', parent=cell, fontName='Helvetica-Bold')
    title = ParagraphStyle('title', parent=ss['Title'], fontSize=13, leading=16, spaceAfter=0,
                           textColor=colors.black)
    sub = ParagraphStyle('sub', parent=ss['Normal'], fontSize=9.5, alignment=TA_CENTER, textColor=SLATE)

    story = []

    # -- header ---------------------------------------------------------------
    logo = Image(LOGO_PATH, width=26 * mm, height=26 * mm * 109 / 198) if os.path.exists(LOGO_PATH) else ''
    head = Table([[logo, [Paragraph('APIS INDIA LIMITED', title),
                          Paragraph('Compensation Break-up Structure - Annexure A', sub)],
                   Paragraph('APIS (COR) / People &amp; Culture<br/>V_01_2026', small)]],
                 colWidths=[32 * mm, width - 72 * mm, 40 * mm])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                              ('ALIGN', (2, 0), (2, 0), 'RIGHT')]))
    story += [head, Spacer(1, 3 * mm)]

    # -- employee details -----------------------------------------------------
    details = [
        ('Candidate Name', candidate.candidate_name), ('Category', candidate.category or '-'),
        ('Department', candidate.department or '-'), ('Designation', candidate.designation or '-'),
        ('Cadre', f"{b['cadre']} - {b['cadreName']}"), ('Grade', b['grade'] or '-'),
        ('Work Location', candidate.work_location or '-'), ('Total CTC (Per Annum)', rs(b['ctcAnnual'])),
    ]
    rows = [[Paragraph('Employee Details', cell_b), '', '', '']]
    for i in range(0, len(details), 2):
        (k1, v1), (k2, v2) = details[i], details[i + 1]
        rows.append([Paragraph(k1, cell_b), Paragraph(str(v1), cell),
                     Paragraph(k2, cell_b), Paragraph(str(v2), cell)])
    t = Table(rows, colWidths=[width * .2, width * .3, width * .2, width * .3])
    t.setStyle(TableStyle([
        ('SPAN', (0, 0), (-1, 0)), ('BACKGROUND', (0, 0), (-1, 0), AMBER),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.5, GRID), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND', (0, 1), (0, -1), AMBER_SOFT), ('BACKGROUND', (2, 1), (2, -1), AMBER_SOFT),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
    ]))
    story += [t, Spacer(1, 3 * mm)]

    # -- the break-up -----------------------------------------------------------
    data = [[Paragraph('Salary Component #', cell_b), Paragraph('Monthly', cell_b),
             Paragraph('Annually', cell_b), Paragraph('Basis', cell_b)]]
    style = [('BACKGROUND', (0, 0), (-1, 0), AMBER), ('TEXTCOLOR', (0, 0), (-1, 0), colors.white)]

    def add(label, monthly, annual, note='', kind='row'):
        r = len(data)
        lab = Paragraph(label, cell_b if kind != 'row' else cell)
        data.append([lab, rs(monthly), rs(annual), Paragraph(note, small)])
        if kind == 'total':
            style.extend([('BACKGROUND', (0, r), (-1, r), AMBER_SOFT),
                          ('FONTNAME', (1, r), (2, r), 'Helvetica-Bold')])
        elif kind == 'grand':
            style.extend([('BACKGROUND', (0, r), (-1, r), AMBER_DARK),
                          ('TEXTCOLOR', (0, r), (-1, r), colors.white),
                          ('FONTNAME', (1, r), (2, r), 'Helvetica-Bold')])
            data[r][0] = Paragraph(f'<font color="white">{label}</font>', cell_b)

    def heading(label):
        r = len(data)
        data.append([Paragraph(label, cell_b), '', '', ''])
        style.extend([('SPAN', (0, r), (-1, r)), ('BACKGROUND', (0, r), (-1, r), colors.HexColor('#F8FAFC'))])

    for x in b['earnings']:
        add(x['label'], x['monthly'], x['annual'], x['note'])
    add('GROSS EARNINGS', b['grossEarnings'], b['grossEarnings'] * 12, kind='total')
    if b['reimbursements']:
        heading('Reimbursements')
        for x in b['reimbursements']:
            add(x['label'], x['monthly'], x['annual'], x['note'])
    add('Gross Salary', b['grossSalary'], b['grossSalary'] * 12, kind='total')
    heading('Annual Benefits')
    for x in b['benefits']:
        add(x['label'], x['monthly'], x['annual'], x['note'])
    add('Total Annual Benefits', b['benefitsTotal'], b['benefitsTotal'] * 12, kind='total')
    add('TOTAL Salary / Compensation (Fixed)', b['fixedMonthly'], b['fixedAnnual'], kind='total')
    heading('Other Payments (Payout on Quarterly Basis)')
    for x in b['otherPayments']:
        add(x['label'], x['monthly'], x['annual'], x['note'])
    add('TOTAL CTC', b['totalCtcMonthly'], b['totalCtcAnnual'], kind='grand')

    t = Table(data, colWidths=[width * .44, width * .16, width * .16, width * .24], repeatRows=1)
    t.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.5, GRID), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (2, -1), 'RIGHT'), ('FONTSIZE', (1, 1), (2, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 1.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
        *style,
    ]))
    story += [t, Spacer(1, 3 * mm)]

    for n in b['notes']:
        story.append(Paragraph(f'Note: {n}', small))
    story.append(Spacer(1, 2 * mm))
    for f in FOOTNOTES:
        story.append(Paragraph(f, small))
    story += [Spacer(1, 3 * mm), Paragraph(
        "Your Compensation Break-up Structure has been determined based on your cadre and grade, "
        "in accordance with the company's compensation policy as per the provisions of the Code on "
        'Wages &amp; applicable laws.', small)]
    story += [Spacer(1, 8 * mm), Table(
        [[Paragraph('Date : ____________', cell), Paragraph('Signature of Head People &amp; Culture', cell)]],
        colWidths=[width / 2, width / 2], style=[('ALIGN', (1, 0), (1, 0), 'RIGHT')])]
    story += [Spacer(1, 4 * mm), Paragraph(
        'Note: This is a computer generated Compensation Component Break-up Structure. In case of any '
        'discrepancy, please contact your HR Dept.', small)]

    doc.build(story)
    return buf.getvalue()
