"""Offer Letter PDF generation and email automation.

Two conditional letter formats for APIS India Limited:
  • Promotion  → "Annual Compensation Review & Promotion Letter"  (when the
     employee's designation changes)
  • Appraisal  → "Annual Compensation Review & Salary Revision Letter" (salary
     revision only, no designation change)

Page 2 carries Annexure-A (revised salary structure).
"""
import io
import os
import re
from datetime import datetime
from xml.sax.saxutils import escape as _xml_escape
from django.core.mail import EmailMessage
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, Image, HRFlowable, KeepTogether)
from reportlab.lib import colors

LOGO_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'apis_logo.png')
try:
    with open(LOGO_PATH, 'rb') as _lf:
        _LOGO_BYTES = _lf.read()          # cached once → no disk read per letter
except OSError:
    _LOGO_BYTES = None


def _logo_image(width_in):
    """Fresh reportlab Image from the cached logo bytes (avoids per-letter disk I/O)."""
    if not _LOGO_BYTES:
        return None
    return Image(io.BytesIO(_LOGO_BYTES), width=width_in * inch, height=width_in * inch * 109 / 198)


def _esc(v):
    """XML-escape a dynamic value for safe use inside a reportlab Paragraph."""
    return _xml_escape(str(v if v is not None else ''))

# Fixed signatory (same on every letter)
SIGNATORY_NAME = 'Pankaj Tripathi'
SIGNATORY_TITLE = 'General Manager HR &amp; Head IT &amp; Admin'

NAVY = HexColor('#1e3a5f')
BLUE = HexColor('#2d5f8d')
GOLD = HexColor('#d99a00')
GREY = HexColor('#666666')


def _rs(v):
    """Format an amount as Rs. with Indian-style grouping (reliable in reportlab)."""
    try:
        return f"Rs. {float(v):,.2f}"
    except (TypeError, ValueError):
        return "Rs. 0.00"


def _fy_context(effective_date):
    """Derive the reviewed FY and next-review date from the effective date
    (annual April cycle). e.g. effective 01 Apr 2026 → reviewed 2025-26, next March 2027."""
    ey = effective_date.year
    reviewed_fy = f"{ey - 1}-{str(ey)[-2:]}"
    next_review = f"March {ey + 1}"
    return reviewed_fy, next_review


# ── Annexure-A: Compensation Break-up spec (single source of truth) ──────────────
# (key, section, full_label, excel_header, basis)
#   basis 'M' = value entered as MONTHLY  → annual = ×12
#   basis 'A' = value entered as ANNUAL   → monthly = ÷12
SALARY_COMPONENTS = [
    ('basic',     'earnings', 'Basic Salary (Inclusive of DA/VDA)',                          'Basic Salary (Monthly)',        'M'),
    ('hra',       'earnings', 'House Rent Allowance (HRA)',                                   'HRA (Monthly)',                 'M'),
    ('cea',       'earnings', 'Child Education Allowance (CEA) @',                            'CEA (Monthly)',                 'M'),
    ('lta',       'earnings', 'Leave Travel Allowance (LTA)',                                 'LTA (Monthly)',                 'M'),
    ('special',   'earnings', 'Special Allowance (Flexi Pay)',                               'Special Allowance (Monthly)',   'M'),
    ('meal',      'reimb',    'Meal Vocher Reimbursement',                                    'Meal Voucher (Monthly)',        'M'),
    ('telephone', 'reimb',    'Telephone-Handset / Accessories & Internet Reimbursement',     'Telephone/Internet (Monthly)',  'M'),
    ('health',    'reimb',    'Health & Wellness Reimbursement',                              'Health & Wellness (Monthly)',   'M'),
    ('books',     'reimb',    'Books, Periodicals & Professional Development Reimbursement',   'Books/Prof Dev (Monthly)',      'M'),
    ('uniform',   'reimb',    'Uniform & Attire Reimbursement',                               'Uniform & Attire (Monthly)',    'M'),
    ('fuel',      'reimb',    'Fuel & Vehicle Maintenance Reimbursement',                     'Fuel & Vehicle (Monthly)',      'M'),
    ('driver',    'reimb',    'Driver/Chauffer Salary',                                       'Driver Salary (Monthly)',       'M'),
    ('car_lease', 'reimb',    'Car Lease / Company Car Benefit',                              'Car Lease (Monthly)',           'M'),
    ('pf',        'benefits', 'Employer PF Contribution (12% of Basic / PF Ceiling Wages)',    'Employer PF (Monthly)',         'M'),
    ('esi',       'benefits', 'Employer ESI Contribution (3.25% of Gross Salary)',            'Employer ESI (Monthly)',        'M'),
    ('mediclaim', 'benefits', 'Mediclaim Charges ( As per Grade & Applicable Policy)',         'Mediclaim (Monthly)',           'M'),
    ('bonus',     'benefits', 'Statutory Bonus (8.33% of Basic & VDA)',                        'Statutory Bonus (Monthly)',     'M'),
    ('variable',  'other',    'Variable / Performance Pay $',                                  'Variable Pay (Monthly)',        'M'),
    ('gift',      'other',    'Gift Reimbursement',                                            'Gift Reimbursement (Monthly)',  'M'),
]

# Extra Annexure employee-detail columns: (excel_header, model/dict key, annexure label)
ANNEXURE_EMP_FIELDS = [
    ('Function',        'function',        'Function'),
    ('Cadre',           'cadre',           'Cadre'),
    ('Grade',           'grade',           'Grade'),
    ('Date of Joining', 'date_of_joining', 'Date of Joining'),
    ('Work Location',   'work_location',   'Work Location'),
]

_YEL = HexColor('#FFFF00')
_CYAN = HexColor('#00B0F0')
_DGREY = HexColor('#808080')
_LGREEN = HexColor('#92D050')
_PEACH = HexColor('#FCE4D6')


def _amt(v):
    """Format an amount with thousands separators; blank string for empty/zero."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return ''
    if not v:
        return ''
    return f"{round(v):,}"


def _annexure_table(emp_name, emp_id, department, function, designation,
                    cadre, grade, date_of_joining, work_location, breakup, scale=1.0):
    """Build the Compensation Break-up (Annexure-A) table. Components with a
    blank/zero value are skipped (employee not eligible) and excluded from totals.
    `scale` shrinks fonts/padding so the whole table fits one page."""
    breakup = breakup or {}
    s = scale
    c0, c1, c2 = 3.37 * inch, 1.60 * inch, 1.60 * inch

    pB = ParagraphStyle('anxB', fontName='Helvetica-Bold', fontSize=12 * s, leading=14 * s, alignment=TA_CENTER)
    pSub = ParagraphStyle('anxSub', fontName='Helvetica-Bold', fontSize=10 * s, leading=12 * s, alignment=TA_CENTER)
    pHdr = ParagraphStyle('anxHdr', fontName='Helvetica-Bold', fontSize=9.5 * s, leading=11 * s, alignment=TA_CENTER, textColor=colors.white)
    pHdrL = ParagraphStyle('anxHdrL', fontName='Helvetica-Bold', fontSize=9.5 * s, leading=11 * s, alignment=TA_LEFT, textColor=colors.white)
    pLbl = ParagraphStyle('anxLbl', fontName='Helvetica-Bold', fontSize=8.5 * s, alignment=TA_LEFT, leading=10 * s)
    pDL = ParagraphStyle('anxDL', fontName='Helvetica-Bold', fontSize=9 * s, leading=10.5 * s, alignment=TA_LEFT)
    pDV = ParagraphStyle('anxDV', fontName='Helvetica', fontSize=9 * s, leading=10.5 * s, alignment=TA_LEFT)
    pST = ParagraphStyle('anxST', fontName='Helvetica-Bold', fontSize=9 * s, leading=10.5 * s, alignment=TA_LEFT, textColor=colors.white)
    pAmt = ParagraphStyle('anxAmt', fontName='Helvetica', fontSize=9 * s, leading=10.5 * s, alignment=TA_RIGHT)
    pAmtW = ParagraphStyle('anxAmtW', fontName='Helvetica-Bold', fontSize=9 * s, leading=10.5 * s, alignment=TA_RIGHT, textColor=colors.white)

    data, cmds = [], []

    def row(cells):
        data.append(cells)
        return len(data) - 1

    def band(text, bg, para=pHdrL):
        i = row([Paragraph(text, para), '', ''])
        cmds.extend([('SPAN', (0, i), (2, i)), ('BACKGROUND', (0, i), (2, i), bg)])

    # Banner + subtitle
    band('APIS INDIA LIMITED', _YEL, pB)
    i = row([Paragraph('<u>Compensation Break-up Structure - Annexure A</u>', pSub), '', ''])
    cmds.append(('SPAN', (0, i), (2, i)))

    # Employee details
    band('Employee Details', _CYAN, pHdr)
    for lbl, val in [
        ('Employee Code', emp_id), ('Employee Name', emp_name), ('Department', department),
        ('Function', function), ('Designation', designation), ('Cadre', cadre),
        ('Grade', grade), ('Date of Joining', date_of_joining), ('Work Location', work_location),
    ]:
        i = row([Paragraph(lbl, pDL), Paragraph(str(val or ''), pDV), ''])
        cmds.append(('SPAN', (1, i), (2, i)))

    # Salary component header
    i = row([Paragraph('Salary Component #', pHdrL), Paragraph('Monthly', pHdr), Paragraph('Annually', pHdr)])
    cmds.append(('BACKGROUND', (0, i), (2, i), _CYAN))

    def comp_val(key, basis):
        try:
            v = float(breakup.get(key))
        except (TypeError, ValueError):
            return None
        if not v:
            return None
        return (v, v * 12) if basis == 'M' else (v / 12.0, v)

    def add_section(section, peach=False):
        tm = ta = 0.0
        for key, sec, label, _h, basis in SALARY_COMPONENTS:
            if sec != section:
                continue
            vv = comp_val(key, basis)
            if vv is None:
                continue
            m, a = vv
            tm += m
            ta += a
            i = row([Paragraph(label, pLbl), Paragraph(_amt(m), pAmt), Paragraph(_amt(a), pAmt)])
            if peach:
                cmds.append(('BACKGROUND', (0, i), (2, i), _PEACH))
        return tm, ta

    def subtotal(label, m, a, bg):
        i = row([Paragraph(label, pST), Paragraph(_amt(m), pAmtW), Paragraph(_amt(a), pAmtW)])
        cmds.append(('BACKGROUND', (0, i), (2, i), bg))
        return i

    e_m, e_a = add_section('earnings')
    subtotal('GROSS EARNINGS', e_m, e_a, _DGREY)
    r_m, r_a = add_section('reimb', peach=True)
    gs_m, gs_a = e_m + r_m, e_a + r_a
    # Skip this line when there are no reimbursement components — it would be
    # an exact duplicate of GROSS EARNINGS just above.
    if r_m or r_a:
        subtotal('Gross Salary', gs_m, gs_a, _DGREY)
    band('Annual Benefits', _CYAN)
    b_m, b_a = add_section('benefits')
    subtotal('Total Annual Benefits', b_m, b_a, _DGREY)
    tc_m, tc_a = gs_m + b_m, gs_a + b_a
    tc_row = subtotal('TOTAL Salary/ Compensation ( Per Month)', tc_m, tc_a, _LGREEN)
    band('Other Payments (Payout on Quarterly Basis)', _CYAN)
    o_m, o_a = add_section('other')
    subtotal('TOTAL  CTC ( Per Annum)', tc_m + o_m, tc_a + o_a, _LGREEN)

    cmds.extend([
        ('GRID', (0, 0), (-1, -1), 0.6, colors.black),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3.5 * s),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3.5 * s),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        # Extra breathing room below the monthly total, before "Other Payments"
        # starts — added after the uniform padding above so it overrides it.
        ('BOTTOMPADDING', (0, tc_row), (2, tc_row), 11 * s),
    ])
    tbl = Table(data, colWidths=[c0, c1, c2], repeatRows=0)
    tbl.setStyle(TableStyle(cmds))
    return tbl


def generate_offer_letter_pdf(employee, current_ctc, new_ctc, increment_pct, promotion_pct,
                               effective_date, old_designation=None, new_designation=None,
                               performance_rating=None, grade_label=None, employee_id=None,
                               employee_name=None, department=None, salutation_title=None,
                               assessment=None, emp_details=None, salary_breakup=None,
                               special_reward=0, special_reward_note=''):
    """Generate the APIS appraisal / promotion letter PDF.

    Works with both PMS employees and standalone (Excel-uploaded) data.
    Chooses the Promotion letter when the designation changes, otherwise the
    Salary Revision letter.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            topMargin=0.5 * inch, bottomMargin=0.45 * inch,
                            leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                            title='APIS India Limited — Compensation Review Letter')

    styles = getSampleStyleSheet()
    story = []

    # ── Resolve data (employee object OR standalone values) ──────────────────
    #   Keep RAW values for logic (promotion detection, annexure which escapes
    #   internally); use XML-escaped values for the letter body Paragraphs so
    #   names/designations containing & < > (e.g. "Sales & Marketing") don't
    #   break reportlab's mini-markup parser.
    raw_name = employee_name or (employee.name if employee else 'Employee')
    raw_id = employee_id or (employee.employee_id if employee else 'N/A')
    raw_dept = department or (employee.department if employee else '') or ''
    raw_desig = (old_designation or (employee.designation if employee else '') or '')
    raw_new = (new_designation or '').strip()
    raw_title = (salutation_title or 'Mr./Ms.').strip()
    raw_phrase = (assessment or grade_label or 'Strong Performer').strip()

    is_promotion = bool(raw_new and raw_new.lower() != raw_desig.strip().lower())

    emp_name = _esc(raw_name)
    emp_id = _esc(raw_id)
    emp_dept = _esc(raw_dept)
    emp_desig = _esc(raw_desig)
    new_desig = _esc(raw_new)
    title = _esc(raw_title)
    phrase = _esc(raw_phrase)
    article = 'an' if raw_phrase[:1].upper() in 'AEIOU' else 'a'

    reviewed_fy, next_review = _fy_context(effective_date)
    eff_str = effective_date.strftime('%d %B %Y')
    date_str = datetime.now().strftime('%d %B %Y')

    letter_title = ('Annual Compensation Review &amp; Promotion Letter' if is_promotion
                    else 'Annual Compensation Review &amp; Salary Revision Letter')

    # ── Body text (conditional) ──────────────────────────────────────────────
    intro = ("At APIS India Limited, we recognize that our organization's sustained growth "
             "&amp; success is made possible through the consistent commitment and performance "
             "of our people. We remain steadfast in fostering a culture of meritocracy and "
             "accountability, where performance at all levels is objectively evaluated "
             "&amp; appropriately acknowledged.")
    if is_promotion:
        body_paras = [
            f"Following the completion of the performance review cycle for the year {reviewed_fy}, "
            f"we are pleased to inform that you have been assessed as {article} <b>{phrase}</b>, "
            "demonstrating dependable results &amp; meeting the expectations set for your role.",
            "In recognition of your consistent performance and valuable contribution to the "
            f"organization, we are delighted to inform you that you are promoted from "
            f"<b>{emp_desig}</b> to <b>{new_desig}</b> with effect from <b>{eff_str}</b>. The details of "
            "your revised salary structure are enclosed as <b>Annexure–A</b>, which forms an "
            "integral part of this letter.",
            "Your compensation and promotion have been determined after considering your overall "
            "performance, demonstrated capabilities, expanded responsibilities, market "
            "competitiveness, internal equity, leadership potential and the Company's overall "
            "business performance.",
            "Your promotion reflects the confidence that the Management places in your ability to "
            "take on broader responsibilities and contribute meaningfully to the continued growth "
            "and success of APIS India Limited. We are confident that you will continue to lead by "
            "example, uphold the APIS UPLIFT Values, and deliver excellence in your new role.",
            "As we continue building a stronger and future-ready organization, we encourage you to "
            "embrace new opportunities, strengthen collaboration, foster innovation, and create "
            "lasting value for our customers, colleagues, business partners and stakeholders. We "
            "are confident that your continued contribution will help us achieve new milestones "
            f"together. Your next performance review is scheduled for <b>{next_review}</b>.",
            "Congratulations on your well-deserved compensation revision and promotion. We thank "
            "you for your commitment and look forward to your continued partnership in creating a "
            "stronger future for APIS India Limited. <b>Together, We UPLIFT. Together, We Grow.</b>",
        ]
    else:
        body_paras = [
            f"Following the completion of the performance review cycle for the year {reviewed_fy}, "
            f"we are pleased to inform that you have been assessed as {article} <b>{phrase}</b>.",
            f"We are pleased to inform you that your compensation has been revised effective "
            f"<b>{eff_str}</b>. The details of your revised salary structure are enclosed as "
            "<b>Annexure–A</b>, which forms an integral part of this letter. This revision reflects "
            "our annual compensation review and recognizes your performance, responsibilities, "
            "market competitiveness, internal equity and the Company's overall business performance.",
            "As we continue building a stronger and more agile organization, we encourage you to "
            "embody the spirit of UPLIFT by demonstrating ownership, collaboration, innovation, "
            "customer focus, integrity, and a relentless pursuit of excellence. We are confident "
            "that your continued contribution will help us achieve new milestones together. Your "
            f"next performance review is scheduled for <b>{next_review}</b>.",
            "Congratulations on your revised compensation. We thank you for your commitment and "
            "look forward to your continued partnership in creating a stronger future for APIS "
            "India Limited. <b>Together, We UPLIFT. Together, We Grow.</b>",
        ]

    try:
        reward_val = float(special_reward or 0)
    except (TypeError, ValueError):
        reward_val = 0
    reward_para = None
    if reward_val > 0:
        reward_para = ("In addition, in recognition of your exceptional contribution, we are pleased "
                       f"to award you a <b>one-time Special Reward of {_rs(reward_val)}</b>. "
                       "This amount is a one-time payout and does not form part of your recurring "
                       "annual CTC.")
    terms_para = ("All other terms and conditions of your employment shall remain unchanged and "
                  "continue to be governed by your Letter of Appointment and the Company's policies, "
                  "as amended from time to time.")
    confid_para = ("<b>Confidentiality:</b> Please treat this communication and your compensation "
                   "details as strictly confidential. Disclosure or discussion of your package with "
                   "others will be considered a violation of company policy. Your compensation is "
                   "uniquely determined and should not be compared with that of other employees.")
    nda_para = ("<b>Non-Disclosure &amp; Non-Compete:</b> As part of our evolving business practices, "
                "certain roles may require additional confidentiality and compliance measures. Based "
                "on the criticality and sensitivity of your role, you may be asked to sign a "
                "Non-Disclosure and Non-Compete Agreement, as assessed by your HR representative. "
                "This process may be initiated at any point during the year.")
    ack_para = ("I acknowledge receipt of this Salary Revision Letter and accept the revised "
                f"compensation effective <b>{eff_str}</b>.")
    sign_line = ("Employee Signature: ______________________ &nbsp;&nbsp; "
                 "Name: ______________________ &nbsp;&nbsp; Date: ________________")

    ed = emp_details or {}
    annx_desig = new_desig if is_promotion else (emp_desig or '')
    notes = [
        "# Tax applicability as per Income Tax Act &amp; shall be borne by the employee",
        "$ Variable pay is paid as per company's variable pay policy on quarterly basis",
        "@ Children Education Allowance is applicable for a maximum 2 children only",
        "Car Lease as per car lease policy",
        "NPS : Max contribution up to 10 % of Basic in old tax regime &amp; 14 % in new tax regime",
        "Gratuity amount payment as per Code on Social Security, 2020 (PGA 1972)",
        "Your Compensation Break-up Structure have been determined based on your cadre and grade, "
        "in accordance with the company's compensation policy as per the provisions of Code on "
        "Wages &amp; applicable laws",
    ]

    # ── Page builders parameterised by a shrink `scale` (auto-fit to one page) ─
    def build_letter(s):
        t_title = ParagraphStyle('LTitle', fontSize=15 * s, textColor=NAVY, alignment=TA_CENTER,
                                 spaceAfter=2 * s, fontName='Helvetica-Bold', leading=18 * s)
        t_conf = ParagraphStyle('Confid', fontSize=9.5 * s, textColor=HexColor('#b03030'),
                                alignment=TA_CENTER, spaceAfter=8 * s, fontName='Helvetica-BoldOblique')
        t_meta = ParagraphStyle('Meta', fontSize=10 * s, leading=13.5 * s, spaceAfter=1)
        t_body = ParagraphStyle('Body', fontSize=10 * s, leading=13.5 * s, spaceAfter=6.5 * s, alignment=TA_JUSTIFY)
        t_sign = ParagraphStyle('Sign', fontSize=10 * s, leading=13.5 * s)
        t_small = ParagraphStyle('Small', fontSize=8.5 * s, leading=10.5 * s, spaceAfter=4 * s,
                                 alignment=TA_JUSTIFY, textColor=GREY)
        t_ack = ParagraphStyle('Ack', fontSize=9 * s, leading=12 * s, spaceBefore=3 * s, spaceAfter=5 * s)
        f = []
        img = _logo_image(1.5 * s)
        if img is not None:
            img.hAlign = 'CENTER'
            f.append(img)
            f.append(Spacer(1, 0.06 * inch * s))
        f.append(HRFlowable(width='100%', thickness=1.4, color=GOLD, spaceBefore=2, spaceAfter=8 * s))
        f.append(Paragraph(letter_title, t_title))
        f.append(Paragraph('Private &amp; Confidential', t_conf))
        f.append(Paragraph(f"<b>Date:</b> {date_str}", t_meta))
        f.append(Spacer(1, 0.04 * inch * s))
        f.append(Paragraph(f"<b>Employee Name:</b> {emp_name}", t_meta))
        f.append(Paragraph(f"<b>Employee Code:</b> {emp_id}", t_meta))
        f.append(Paragraph(f"<b>Designation:</b> {emp_desig or '—'}", t_meta))
        f.append(Paragraph(f"<b>Department:</b> {emp_dept or '—'}", t_meta))
        f.append(Spacer(1, 0.1 * inch * s))
        f.append(Paragraph(f"Dear {title} {emp_name},", t_body))
        f.append(Paragraph(intro, t_body))
        for p in body_paras:
            f.append(Paragraph(p, t_body))
        if reward_para:
            f.append(Paragraph(reward_para, t_body))
        f.append(Paragraph(terms_para, t_body))
        f.append(Spacer(1, 0.1 * inch * s))
        f.append(KeepTogether([
            Paragraph("With Best Wishes,", t_sign),
            Paragraph("For <b>APIS India Limited</b>", t_sign),
            Spacer(1, 0.32 * inch * s),
            Paragraph(f"<b>{SIGNATORY_NAME}</b>", t_sign),
            Paragraph(SIGNATORY_TITLE, t_sign),
        ]))
        f.append(Spacer(1, 0.08 * inch * s))
        f.append(HRFlowable(width='100%', thickness=0.7, color=colors.grey, spaceAfter=7 * s))
        f.append(Paragraph(confid_para, t_small))
        f.append(Paragraph(nda_para, t_small))
        f.append(Spacer(1, 0.04 * inch * s))
        f.append(Paragraph(ack_para, t_ack))
        f.append(Spacer(1, 0.14 * inch * s))
        f.append(Paragraph(sign_line, t_ack))
        return f

    def build_annexure(s):
        f = []
        right_txt = Paragraph('APIS (COR) / People &amp; Culture<br/>V_01_2026', ParagraphStyle(
            'anxRT', fontName='Helvetica-Bold', fontSize=9 * s, alignment=TA_RIGHT, leading=12 * s))
        logo_cell = _logo_image(0.85 * s) or Paragraph('apis', styles['Normal'])
        top = Table([[logo_cell, right_txt]], colWidths=[3.0 * inch, avail_w - 3.0 * inch])
        top.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                 ('ALIGN', (0, 0), (0, 0), 'LEFT'),
                                 ('LEFTPADDING', (0, 0), (-1, -1), 0),
                                 ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
        f.append(top)
        f.append(Spacer(1, 5 * s))
        f.append(_annexure_table(
            emp_name, emp_id, emp_dept, _esc(ed.get('function', '')), annx_desig,
            _esc(ed.get('cadre', '')), _esc(ed.get('grade', '')), _esc(ed.get('date_of_joining', '')),
            _esc(ed.get('work_location', '')), salary_breakup, scale=s))
        f.append(Spacer(1, 0.13 * inch * s))
        note_l = ParagraphStyle('nL', fontName='Helvetica', fontSize=9.5 * s, alignment=TA_LEFT)
        note_r = ParagraphStyle('nR', fontName='Helvetica-Bold', fontSize=9.5 * s, alignment=TA_RIGHT)
        sig = Table([[Paragraph(f"Date : {date_str}", note_l),
                      Paragraph('Signature of Head People &amp; Culture', note_r)]],
                    colWidths=[avail_w * 0.5, avail_w * 0.5])
        sig.setStyle(TableStyle([('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0)]))
        f.append(sig)
        f.append(Spacer(1, 0.1 * inch * s))
        fn = ParagraphStyle('fn', fontName='Helvetica', fontSize=8 * s, leading=11 * s, alignment=TA_LEFT,
                            textColor=HexColor('#333333'), spaceAfter=2 * s)
        for note in notes:
            f.append(Paragraph(note, fn))
        f.append(Spacer(1, 0.05 * inch * s))
        f.append(HRFlowable(width='100%', thickness=0.8, color=colors.grey, dash=(2, 2), spaceAfter=5 * s))
        f.append(Paragraph(
            "Note: This is a computer generated Compensation Component Break-up Structure. In case of "
            "any discrepancy, please contact your People and Culture Dept.",
            ParagraphStyle('fnote', parent=fn, fontName='Helvetica-Oblique')))
        f.append(Spacer(1, 4 * s))
        f.append(Paragraph('APIS --Approved_P &amp; C', ParagraphStyle(
            'tag', fontName='Helvetica', fontSize=8 * s, alignment=TA_RIGHT, textColor=HexColor('#555555'))))
        return f

    # ── Auto-fit: shrink each page until it fits within one page ──────────────
    avail_w, avail_h = doc.width, doc.height

    def _measure(flows):
        total = 0.0
        for fl in flows:
            content = getattr(fl, '_content', None)  # KeepTogether reports 0 when wrapped alone
            if content:
                total += _measure(content)
                continue
            try:
                _, h = fl.wrap(avail_w, avail_h)
            except Exception:
                h = 0
            total += h
            st = getattr(fl, 'style', None)
            if st is not None:
                total += getattr(st, 'spaceBefore', 0) + getattr(st, 'spaceAfter', 0)
        return total

    def _fit(builder, scales):
        limit = avail_h * 0.96  # small slack for wrap/rounding
        for sc in scales:
            if _measure(builder(sc)) <= limit:
                return sc
        return scales[-1]

    letter_scale = _fit(build_letter, (1.0, 0.97, 0.94, 0.91, 0.88, 0.85, 0.82, 0.79, 0.76, 0.73, 0.70))
    annx_scale = _fit(build_annexure, (1.0, 0.96, 0.92, 0.88, 0.84, 0.80, 0.76, 0.72, 0.68, 0.64))

    story = build_letter(letter_scale) + [PageBreak()] + build_annexure(annx_scale)
    doc.build(story)
    buffer.seek(0)
    return buffer


def send_offer_letter_email(employee_email, employee_name, pdf_buffer, effective_date,
                            offer_letter_id=None, connection=None):
    """Send the compensation review letter PDF via email.

    Pass a shared `connection` (django.core.mail.get_connection) when sending in
    bulk so the whole batch reuses a single SMTP connection instead of opening
    one per email — a major speed-up for hundreds of letters."""
    from django.conf import settings

    subject = f"APIS India — Compensation Review Letter (Effective {effective_date.strftime('%d %B %Y')})"

    body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
<div style="max-width: 600px; margin: 0 auto; background-color: #f9f9f9; padding: 20px; border-radius: 8px;">
    <p>Dear <b>{employee_name}</b>,</p>
    <p>Please find attached your <b>Annual Compensation Review Letter</b> from APIS India Limited,
    detailing your revised compensation effective <b>{effective_date.strftime('%d %B %Y')}</b>,
    along with <b>Annexure-A</b> (revised salary structure).</p>
    <p>Kindly review the attached letter carefully. This communication is
    <b>Private &amp; Confidential</b>.</p>
    <p style="color: #666; font-size: 12px; margin-top: 20px; padding-top: 20px; border-top: 1px solid #ddd;">
        For any questions, please reach out to the HR department.<br>
        <b>APIS India Limited — Human Resources</b>
    </p>
</div>
</body>
</html>
    """

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.OFFER_LETTER_EMAIL_HOST_USER,
        to=[employee_email],
        connection=connection,
    )
    email.content_subtype = 'html'

    pdf_buffer.seek(0)
    email.attach(f'APIS_Compensation_Letter_{employee_name.replace(" ", "_")}.pdf',
                 pdf_buffer.read(), 'application/pdf')
    email.send(fail_silently=False)
