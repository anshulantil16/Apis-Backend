"""The compensation break-up as a PDF, in P & C's own format: the
"CTC MASTER Approved" sheet of the Compensation Structure workbook
("Compensation Break-up Structure - Annexure A"), row for row.

Colours, row order and wording are taken from that sheet: the yellow title
bar, the blue section bars, light-orange
reimbursement rows, grey sub-totals and green totals. Every row the sheet
has is printed, at 0 if it doesn't apply, so each candidate's PDF has the
same shape. The sheet's double borders are drawn as single rules (the PDF
toolkit has no double line), and its Abadi font as Helvetica.

Takes a candidate and the dict calculator.calculate() returned; draws, does
not decide. Function is not collected by the upload, so it is left blank
for P & C to fill. (The sheet's red Employee Code label is a fill-me-in
marker, so it is not reproduced.)
"""
import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

LOGO_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'apis_logo.png')

# From the sheet's fills.
YELLOW = colors.HexColor('#FFFF00')
BLUE = colors.HexColor('#00B0F0')
GREY = colors.HexColor('#7C7C7C')        # theme accent3 (#A5A5A5) darkened 25%
ORANGE_SOFT = colors.HexColor('#FBE5D6')  # theme accent2 (#ED7D31) lightened 80%
GREEN = colors.HexColor('#92D050')
INK = colors.HexColor('#1A1A1A')
RULE = colors.HexColor('#404040')

# The reimbursement rows in the sheet's order (calculator keys).
REIMBURSEMENT_ROWS = [
    ('meal',      'Meal Vocher Reimbursement'),
    ('telephone', 'Telephone-Handset / Accessories & Internet Reimbursement'),
    ('health',    'Health & Wellness Reimbursement'),
    ('books',     'Books, Periodicals & Professional Development Reimbursement'),
    ('uniform',   'Uniform & Attire Reimbursement'),
    ('fuel',      'Fuel & Vehicle Maintenance Reimbursement'),
    ('driver',    'Driver/Chauffer Salary'),
]

FOOTNOTES = [
    '# Tax applicability as per Income Tax Act & shall be borne by the employee',
    "$ Variable pay is paid as per company's variable pay policy on quarterly basis",
    '@ Children Education Allowance is applicable for a maximum 2 children only',
    'Car Lease as per car lease policy',
    'NPS : Max contribution up to 10 % of Basic in old tax regime & 14 % in new tax regime',
    'Gratuity amount payment as per Code on Social Security, 2020 (PGA 1972)',
]


def amount(n):
    """Indian digit grouping, no symbol, as the sheet's cells show: 1,23,456."""
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
    return sign + s


def _style(name, size, bold=False, color=INK, align=TA_LEFT):
    return ParagraphStyle(name, fontName='Helvetica-Bold' if bold else 'Helvetica',
                          fontSize=size, leading=size + 2.5, textColor=color, alignment=align)


def build_pdf(candidate, b) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm,
                            topMargin=8 * mm, bottomMargin=8 * mm,
                            title=f'Compensation Break-up - {candidate.candidate_name}',
                            author='APIS India Limited - People & Culture')
    W = A4[0] - 28 * mm
    LABEL, NUM = W * 0.56, W * 0.22

    label = _style('label', 8.5, bold=True)
    label_plain = _style('labelp', 8.5)
    value = _style('value', 8.5)
    bar = _style('bar', 9.5, bold=True, color=colors.white, align=TA_CENTER)
    small = _style('small', 7.4)
    tiny = _style('tiny', 7.2)

    story = []

    # -- top: logo left, form reference right ------------------------------------
    logo = (Image(LOGO_PATH, width=20 * mm, height=20 * mm * 109 / 198)
            if os.path.exists(LOGO_PATH) else '')
    top = Table([[logo, Paragraph('APIS (COR) / People &amp; Culture<br/>V_01_2026',
                                  _style('ref', 8, align=TA_RIGHT))]],
                colWidths=[W / 2, W / 2])
    top.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'),
                             ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
    top.hAlign = 'CENTER'
    story.append(top)

    # -- title bar, subtitle, employee details -----------------------------------
    rows = [
        [Paragraph('APIS INDIA LIMITED', _style('t', 13, bold=True, align=TA_CENTER)), ''],
        [Paragraph('<u>Compensation Break-up Structure - Annexure A</u>', _style('s', 10, align=TA_CENTER)), ''],
        [Paragraph('Employee Details', bar), ''],
    ]
    details = [
        ('Employee Name (Candidate Name)', candidate.candidate_name),
        ('Department', candidate.department),
        ('Designation', candidate.designation),
        ('Cadre', b['cadre']),
        ('Grade', b['grade']),
        ('Work Location', candidate.work_location),
        ('Tax Regime', getattr(candidate, 'tax_regime', '')),
    ]
    for k, v in details:
        rows.append([Paragraph(k, label), Paragraph(str(v or ''), value)])
    head = Table(rows, colWidths=[LABEL, W - LABEL])
    head.setStyle(TableStyle([
        ('SPAN', (0, 0), (1, 0)), ('BACKGROUND', (0, 0), (1, 0), YELLOW),
        ('SPAN', (0, 1), (1, 1)),
        ('SPAN', (0, 2), (1, 2)), ('BACKGROUND', (0, 2), (1, 2), BLUE),
        ('GRID', (0, 2), (-1, -1), 0.6, RULE),
        ('BOX', (0, 0), (-1, 0), 0.6, RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 1.4), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.4),
    ]))
    story.append(head)

    # -- the break-up -----------------------------------------------------------
    data, style = [], []

    def header_row():
        data.append([Paragraph('Salary Component #', bar), Paragraph('Monthly', bar), Paragraph('Annually', bar)])
        style.append(('BACKGROUND', (0, len(data) - 1), (-1, len(data) - 1), BLUE))

    def row(text, monthly, annual, bold=True, fill=None):
        data.append([Paragraph(text, label if bold else label_plain), amount(monthly), amount(annual)])
        if fill:
            style.append(('BACKGROUND', (0, len(data) - 1), (-1, len(data) - 1), fill))

    def total(text, monthly, annual, fill):
        r = len(data)
        data.append([Paragraph(text, bar), amount(monthly), amount(annual)])
        style.extend([('BACKGROUND', (0, r), (-1, r), fill), ('TEXTCOLOR', (1, r), (2, r), colors.white),
                      ('FONTNAME', (1, r), (2, r), 'Helvetica-Bold')])

    def section(text):
        r = len(data)
        data.append([Paragraph(text, bar), '', ''])
        style.extend([('SPAN', (0, r), (-1, r)), ('BACKGROUND', (0, r), (-1, r), BLUE)])

    e = {x['label'].split(' (')[0]: x for x in b['earnings']}

    def earning(text, key):
        row(text, e[key]['monthly'], e[key]['annual'])

    header_row()
    earning('Basic Salary (Inclusive of DA/VDA)', 'Basic Salary')
    earning('House Rent Allowance (HRA)', 'House Rent Allowance')
    earning('Child Education Allowance (CEA) @', 'Child Education Allowance')
    earning('Leave Travel Allowance (LTA)', 'Leave Travel Allowance')
    earning('Special Allowance (Flexi Pay)', 'Special Allowance')
    total('GROSS EARNINGS', b['grossEarnings'], b['grossEarningsAnnual'], GREY)

    # Cadres O and W get no reimbursements, so their sheet leaves the rows out.
    if b['cadre'] not in ('O', 'W'):
        reimb = {x.get('key'): x for x in b['reimbursements']}
        for key, text in REIMBURSEMENT_ROWS:
            x = reimb.get(key, {'monthly': 0, 'annual': 0})
            row(text, x['monthly'], x['annual'], fill=ORANGE_SOFT)
        if b.get('carLease'):                      # opted on the template; outside the CTC
            data.append([Paragraph('Car Lease / Company Car Benefit', label), 'As per policy', 'As per policy'])
        else:
            row('Car Lease / Company Car Benefit', 0, 0)
    total('Gross Salary', b['grossSalary'], b['grossSalaryAnnual'], GREY)

    section('Annual Benefits')
    ben = {x['label']: x for x in b['benefits']}

    def benefit(text, key):
        row(text, ben[key]['monthly'], ben[key]['annual'], bold=False)

    benefit('Employer PF Contribution (12% of Gross less HRA / max Rs 3,000)', 'Employer PF Contribution')
    benefit('Employer ESI Contribution (3.25% of Gross Salary)', 'Employer ESI Contribution')
    benefit('Mediclaim Charges ( As per Grade &amp; Applicable Policy)', 'Mediclaim Charges')
    benefit('Statutory Bonus (8.33% of Basic &amp; VDA)', 'Statutory Bonus')
    if ben['Gratuity']['monthly']:                 # not on the sheet unless a rate is set
        benefit('Gratuity', 'Gratuity')
    total('Total Annual Benefits', b['benefitsTotal'], b['benefitsTotalAnnual'], GREY)
    total('TOTAL Salary/ Compensation ( Per Month)', b['fixedMonthly'], b['fixedAnnual'], GREEN)

    section('Other Payments (Payout on Quarterly Basis)')
    vpp, gift = b['otherPayments']
    row('Variable / Performance Pay $', vpp['monthly'], vpp['annual'], bold=False)
    row('Gift Reimbursement', gift['monthly'], gift['annual'], bold=False)
    total('TOTAL  CTC ( Per Annum)', b['totalCtcMonthly'], b['totalCtcAnnual'], GREEN)

    t = Table(data, colWidths=[LABEL, NUM, NUM])
    t.setStyle(TableStyle([
        ('GRID', (0, 0), (-1, -1), 0.6, RULE),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('FONTNAME', (1, 1), (-1, -1), 'Helvetica'), ('FONTSIZE', (1, 0), (-1, -1), 8.5),
        ('TOPPADDING', (0, 0), (-1, -1), 1.4), ('BOTTOMPADDING', (0, 0), (-1, -1), 1.4),
        ('RIGHTPADDING', (1, 0), (-1, -1), 6),
        *style,
    ]))
    story += [t, Spacer(1, 7 * mm)]

    # -- signature, footnotes ---------------------------------------------------
    sig = Table([[Paragraph('Date : DD/MM/YYYY', _style('d', 9.5)),
                  Paragraph('Signature of Head People &amp; Culture', _style('sg', 9.5, align=TA_CENTER))]],
                colWidths=[LABEL, W - LABEL])
    sig.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0)]))
    story += [sig, Spacer(1, 3 * mm)]
    for f in FOOTNOTES:
        story.append(Paragraph(f.replace('&', '&amp;'), small))
    story += [Spacer(1, 2 * mm), Paragraph(
        "Your Compensation Break-up Structure have been determined based on your cadre and grade, in "
        "accordance with the company's compensation policy as per the provisions of Code on Wages &amp; "
        'applicable laws', small), Spacer(1, 3 * mm), Paragraph(
        'Note: This is a computer generated Compensation Component Break-up Structure. In case of any '
        'discrepancy, please contact your HR Dept.', tiny),
        Paragraph('APIS - Approved_P &amp; C', _style('ap', 7.2, align=TA_RIGHT))]

    doc.build(story)
    return buf.getvalue()
