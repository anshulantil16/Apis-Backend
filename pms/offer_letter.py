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
from datetime import datetime
from django.core.mail import EmailMessage
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, PageBreak, Image, HRFlowable, KeepTogether)
from reportlab.lib import colors

LOGO_PATH = os.path.join(os.path.dirname(__file__), 'assets', 'apis_logo.png')

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


def generate_offer_letter_pdf(employee, current_ctc, new_ctc, increment_pct, promotion_pct,
                               effective_date, old_designation=None, new_designation=None,
                               performance_rating=None, grade_label=None, employee_id=None,
                               employee_name=None, department=None, salutation_title=None,
                               assessment=None):
    """Generate the APIS appraisal / promotion letter PDF.

    Works with both PMS employees and standalone (Excel-uploaded) data.
    Chooses the Promotion letter when the designation changes, otherwise the
    Salary Revision letter.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4,
                            topMargin=0.55 * inch, bottomMargin=0.55 * inch,
                            leftMargin=0.85 * inch, rightMargin=0.85 * inch,
                            title='APIS India Limited — Compensation Review Letter')

    styles = getSampleStyleSheet()
    story = []

    # ── Resolve data (employee object OR standalone values) ──────────────────
    emp_name = employee_name or (employee.name if employee else 'Employee')
    emp_id = employee_id or (employee.employee_id if employee else 'N/A')
    emp_dept = department or (employee.department if employee else '')
    emp_desig = old_designation or (employee.designation if employee else '')
    new_desig = (new_designation or '').strip()
    title = (salutation_title or 'Mr./Ms.').strip()

    is_promotion = bool(new_desig and new_desig.lower() != (emp_desig or '').strip().lower())

    reviewed_fy, next_review = _fy_context(effective_date)
    eff_str = effective_date.strftime('%d %B %Y')
    date_str = datetime.now().strftime('%d %B %Y')

    # assessment phrase, e.g. "Strong Performer"
    phrase = (assessment or grade_label or 'Strong Performer').strip()
    article = 'an' if phrase[:1].upper() in 'AEIOU' else 'a'

    # ── Styles ───────────────────────────────────────────────────────────────
    title_style = ParagraphStyle('LTitle', parent=styles['Heading1'], fontSize=15,
                                 textColor=NAVY, alignment=TA_CENTER, spaceAfter=2,
                                 fontName='Helvetica-Bold', leading=19)
    confid_style = ParagraphStyle('Confid', parent=styles['Normal'], fontSize=9.5,
                                  textColor=HexColor('#b03030'), alignment=TA_CENTER,
                                  spaceAfter=10, fontName='Helvetica-BoldOblique')
    meta_style = ParagraphStyle('Meta', parent=styles['Normal'], fontSize=10,
                               leading=15, spaceAfter=1)
    body_style = ParagraphStyle('Body', parent=styles['BodyText'], fontSize=10,
                               leading=15, spaceAfter=8, alignment=TA_JUSTIFY)
    sign_style = ParagraphStyle('Sign', parent=styles['Normal'], fontSize=10, leading=15)
    small_style = ParagraphStyle('Small', parent=styles['Normal'], fontSize=8.5,
                                leading=12, spaceAfter=6, alignment=TA_JUSTIFY,
                                textColor=GREY)
    ack_style = ParagraphStyle('Ack', parent=styles['Normal'], fontSize=9.5,
                              leading=14, spaceBefore=6, spaceAfter=10)
    annx_title = ParagraphStyle('Annx', parent=styles['Heading2'], fontSize=13,
                               textColor=NAVY, alignment=TA_CENTER, fontName='Helvetica-Bold',
                               spaceAfter=4)

    # ── Letterhead (logo) ────────────────────────────────────────────────────
    def add_logo(width_in=1.5):
        if os.path.exists(LOGO_PATH):
            img = Image(LOGO_PATH, width=width_in * inch, height=width_in * inch * 109 / 198)
            img.hAlign = 'CENTER'
            story.append(img)
            story.append(Spacer(1, 0.08 * inch))

    add_logo(1.6)
    story.append(HRFlowable(width='100%', thickness=1.4, color=GOLD, spaceBefore=2, spaceAfter=10))

    # ── Title + confidentiality ──────────────────────────────────────────────
    letter_title = ('Annual Compensation Review &amp; Promotion Letter' if is_promotion
                    else 'Annual Compensation Review &amp; Salary Revision Letter')
    story.append(Paragraph(letter_title, title_style))
    story.append(Paragraph('Private &amp; Confidential', confid_style))

    # ── Date + employee meta ─────────────────────────────────────────────────
    story.append(Paragraph(f"<b>Date:</b> {date_str}", meta_style))
    story.append(Spacer(1, 0.06 * inch))
    story.append(Paragraph(f"<b>Employee Name:</b> {emp_name}", meta_style))
    story.append(Paragraph(f"<b>Employee Code:</b> {emp_id}", meta_style))
    story.append(Paragraph(f"<b>Designation:</b> {emp_desig or '—'}", meta_style))
    story.append(Paragraph(f"<b>Department:</b> {emp_dept or '—'}", meta_style))
    story.append(Spacer(1, 0.14 * inch))

    story.append(Paragraph(f"Dear {title} {emp_name},", body_style))

    # ── Body (conditional) ───────────────────────────────────────────────────
    intro = ("At APIS India Limited, we recognize that our organization's sustained growth "
             "&amp; success is made possible through the consistent commitment and performance "
             "of our people. We remain steadfast in fostering a culture of meritocracy and "
             "accountability, where performance at all levels is objectively evaluated "
             "&amp; appropriately acknowledged.")
    story.append(Paragraph(intro, body_style))

    if is_promotion:
        story.append(Paragraph(
            f"Following the completion of the performance review cycle for the year {reviewed_fy}, "
            f"we are pleased to inform that you have been assessed as {article} <b>{phrase}</b>, "
            "demonstrating dependable results &amp; meeting the expectations set for your role.",
            body_style))
        story.append(Paragraph(
            "In recognition of your consistent performance and valuable contribution to the "
            f"organization, we are delighted to inform you that you are promoted from "
            f"<b>{emp_desig}</b> to <b>{new_desig}</b> with effect from <b>{eff_str}</b>. The details of "
            "your revised salary structure are enclosed as <b>Annexure–A</b>, which forms an "
            "integral part of this letter.",
            body_style))
        story.append(Paragraph(
            "Your compensation and promotion have been determined after considering your overall "
            "performance, demonstrated capabilities, expanded responsibilities, market "
            "competitiveness, internal equity, leadership potential and the Company's overall "
            "business performance.",
            body_style))
        story.append(Paragraph(
            "Your promotion reflects the confidence that the Management places in your ability to "
            "take on broader responsibilities and contribute meaningfully to the continued growth "
            "and success of APIS India Limited. We are confident that you will continue to lead by "
            "example, uphold the APIS UPLIFT Values, and deliver excellence in your new role.",
            body_style))
        story.append(Paragraph(
            "As we continue building a stronger and future-ready organization, we encourage you to "
            "embrace new opportunities, strengthen collaboration, foster innovation, and create "
            "lasting value for our customers, colleagues, business partners and stakeholders. We "
            "are confident that your continued contribution will help us achieve new milestones "
            f"together. Your next performance review is scheduled for <b>{next_review}</b>.",
            body_style))
        story.append(Paragraph(
            "Congratulations on your well-deserved compensation revision and promotion. We thank "
            "you for your commitment and look forward to your continued partnership in creating a "
            "stronger future for APIS India Limited. <b>Together, We UPLIFT. Together, We Grow.</b>",
            body_style))
    else:
        story.append(Paragraph(
            f"Following the completion of the performance review cycle for the year {reviewed_fy}, "
            f"we are pleased to inform that you have been assessed as {article} <b>{phrase}</b>.",
            body_style))
        story.append(Paragraph(
            f"We are pleased to inform you that your compensation has been revised effective "
            f"<b>{eff_str}</b>. The details of your revised salary structure are enclosed as "
            "<b>Annexure–A</b>, which forms an integral part of this letter. This revision reflects "
            "our annual compensation review and recognizes your performance, responsibilities, "
            "market competitiveness, internal equity and the Company's overall business performance.",
            body_style))
        story.append(Paragraph(
            "As we continue building a stronger and more agile organization, we encourage you to "
            "embody the spirit of UPLIFT by demonstrating ownership, collaboration, innovation, "
            "customer focus, integrity, and a relentless pursuit of excellence. We are confident "
            "that your continued contribution will help us achieve new milestones together. Your "
            f"next performance review is scheduled for <b>{next_review}</b>.",
            body_style))
        story.append(Paragraph(
            "Congratulations on your revised compensation. We thank you for your commitment and "
            "look forward to your continued partnership in creating a stronger future for APIS "
            "India Limited. <b>Together, We UPLIFT. Together, We Grow.</b>",
            body_style))

    story.append(Paragraph(
        "All other terms and conditions of your employment shall remain unchanged and continue to "
        "be governed by your Letter of Appointment and the Company's policies, as amended from "
        "time to time.",
        body_style))

    # ── Signature (kept together so it never splits across pages) ────────────
    story.append(Spacer(1, 0.12 * inch))
    story.append(KeepTogether([
        Paragraph("With Best Wishes,", sign_style),
        Paragraph("For <b>APIS India Limited</b>", sign_style),
        Spacer(1, 0.35 * inch),
        Paragraph(f"<b>{SIGNATORY_NAME}</b>", sign_style),
        Paragraph(SIGNATORY_TITLE, sign_style),
    ]))

    # ── Confidentiality + NDA ────────────────────────────────────────────────
    story.append(Spacer(1, 0.1 * inch))
    story.append(HRFlowable(width='100%', thickness=0.7, color=colors.grey, spaceAfter=8))
    story.append(Paragraph(
        "<b>Confidentiality:</b> Please treat this communication and your compensation details as "
        "strictly confidential. Disclosure or discussion of your package with others will be "
        "considered a violation of company policy. Your compensation is uniquely determined and "
        "should not be compared with that of other employees.",
        small_style))
    story.append(Paragraph(
        "<b>Non-Disclosure &amp; Non-Compete:</b> As part of our evolving business practices, certain "
        "roles may require additional confidentiality and compliance measures. Based on the "
        "criticality and sensitivity of your role, you may be asked to sign a Non-Disclosure and "
        "Non-Compete Agreement, as assessed by your HR representative. This process may be "
        "initiated at any point during the year.",
        small_style))

    # ── Acknowledgement ──────────────────────────────────────────────────────
    story.append(Spacer(1, 0.06 * inch))
    story.append(Paragraph(
        f"I acknowledge receipt of this Salary Revision Letter and accept the revised compensation "
        f"effective <b>{eff_str}</b>.",
        ack_style))
    story.append(Spacer(1, 0.18 * inch))
    story.append(Paragraph(
        "Employee Signature: ______________________ &nbsp;&nbsp; "
        "Name: ______________________ &nbsp;&nbsp; Date: ________________",
        ack_style))

    # ── Page 2: Annexure-A (revised salary structure summary) ────────────────
    story.append(PageBreak())
    add_logo(1.3)
    story.append(HRFlowable(width='100%', thickness=1.2, color=GOLD, spaceBefore=2, spaceAfter=10))
    story.append(Paragraph("Annexure – A", annx_title))
    story.append(Paragraph("Revised Salary Structure", ParagraphStyle(
        'AnnxSub', parent=styles['Normal'], fontSize=10.5, textColor=BLUE,
        alignment=TA_CENTER, spaceAfter=10, fontName='Helvetica-Bold')))

    story.append(Paragraph(f"<b>Employee:</b> {emp_name} &nbsp;&nbsp; <b>Code:</b> {emp_id}", meta_style))
    desig_line = (f"<b>Designation:</b> {emp_desig or '—'} to <b>{new_desig}</b>" if is_promotion
                  else f"<b>Designation:</b> {emp_desig or '—'}")
    story.append(Paragraph(desig_line, meta_style))
    story.append(Paragraph(f"<b>Effective Date:</b> {eff_str}", meta_style))
    story.append(Spacer(1, 0.14 * inch))

    cc = float(current_ctc or 0)
    nc = float(new_ctc or 0)
    diff = nc - cc
    diff_pct = (diff / cc * 100) if cc else 0

    ctc_rows = [
        ['Particulars', 'Current (Annual)', 'Revised (Annual)', 'Difference'],
        ['Cost to Company (CTC)', _rs(cc), _rs(nc), _rs(diff)],
        ['Monthly (approx.)', _rs(cc / 12), _rs(nc / 12), _rs(diff / 12)],
    ]
    ctc_table = Table(ctc_rows, colWidths=[1.9 * inch, 1.55 * inch, 1.55 * inch, 1.55 * inch])
    ctc_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BLUE),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9.5),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (-1, 0), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('GRID', (0, 0), (-1, -1), 0.6, colors.grey),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, HexColor('#f4f7fb')]),
    ]))
    story.append(ctc_table)
    story.append(Spacer(1, 0.12 * inch))
    story.append(Paragraph(
        f"<b>Total Annual Increase:</b> {_rs(diff)} ({diff_pct:.2f}%)", meta_style))

    comp = []
    if increment_pct and float(increment_pct) > 0:
        comp.append(f"Performance Increment: {float(increment_pct):g}%")
    if promotion_pct and float(promotion_pct) > 0:
        comp.append(f"Promotion Benefit: {float(promotion_pct):g}%")
    if comp:
        story.append(Paragraph("<b>Components:</b> " + " &nbsp;|&nbsp; ".join(comp), meta_style))

    story.append(Spacer(1, 0.18 * inch))
    story.append(Paragraph(
        "The figures above are indicative of your revised Cost to Company. The complete "
        "component-wise salary structure forms part of this Annexure and is subject to applicable "
        "statutory deductions and Company policies.",
        small_style))

    doc.build(story)
    buffer.seek(0)
    return buffer


def send_offer_letter_email(employee_email, employee_name, pdf_buffer, effective_date, offer_letter_id=None):
    """Send the compensation review letter PDF via email."""
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
        from_email=settings.EMAIL_HOST_USER,
        to=[employee_email],
    )
    email.content_subtype = 'html'

    pdf_buffer.seek(0)
    email.attach(f'APIS_Compensation_Letter_{employee_name.replace(" ", "_")}.pdf',
                 pdf_buffer.read(), 'application/pdf')
    email.send(fail_silently=False)
