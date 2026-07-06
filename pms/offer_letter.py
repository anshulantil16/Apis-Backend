"""Offer Letter PDF generation and email automation."""
import io
from datetime import datetime
from django.core.mail import EmailMessage
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from pypdf import PdfWriter, PdfReader


def generate_offer_letter_pdf(employee, current_ctc, new_ctc, increment_pct, promotion_pct,
                               effective_date, old_designation=None, new_designation=None,
                               performance_rating=None, grade_label=None, employee_id=None,
                               employee_name=None, department=None):
    """Generate a professional offer letter PDF. Works with both PMS employees and standalone data."""

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                           topMargin=0.5*inch, bottomMargin=0.5*inch,
                           leftMargin=0.75*inch, rightMargin=0.75*inch)

    story = []
    styles = getSampleStyleSheet()

    # Use provided data if employee object is None
    emp_name = employee_name or (employee.name if employee else 'Employee')
    emp_id = employee_id or (employee.employee_id if employee else 'N/A')
    emp_dept = department or (employee.department if employee else 'N/A')
    emp_desig = old_designation or (employee.designation if employee else 'N/A')

    # Custom styles
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=16,
        textColor=HexColor('#1e3a5f'),
        spaceAfter=6,
        alignment=1,  # Center
        fontName='Helvetica-Bold',
    )

    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontSize=12,
        textColor=HexColor('#2d5f8d'),
        spaceAfter=8,
        spaceBefore=8,
        fontName='Helvetica-Bold',
    )

    body_style = ParagraphStyle(
        'CustomBody',
        parent=styles['BodyText'],
        fontSize=10,
        leading=14,
        spaceAfter=6,
        alignment=4,  # Justify
    )

    # Header with company logo area
    story.append(Paragraph("APIS INDIA", title_style))
    story.append(Paragraph("Annual Appraisal & CTC Letter", styles['Italic']))
    story.append(Spacer(1, 0.3*inch))

    # Date and letter number
    date_str = datetime.now().strftime("%d %B, %Y")
    story.append(Paragraph(f"<b>Date:</b> {date_str}", body_style))
    story.append(Spacer(1, 0.1*inch))

    # Employee details
    story.append(Paragraph("<b>TO:</b>", heading_style))
    story.append(Paragraph(f"{emp_name}<br/>"
                          f"Employee ID: {emp_id}<br/>"
                          f"Department: {emp_dept}<br/>"
                          f"Current Designation: {emp_desig}",
                          body_style))
    story.append(Spacer(1, 0.2*inch))

    # Salutation
    story.append(Paragraph(f"<b>Dear {emp_name},</b>", body_style))
    story.append(Spacer(1, 0.15*inch))

    # Main content
    story.append(Paragraph(
        "We are pleased to inform you that your performance has been appreciated by the management, "
        "and we are delighted to offer you a revised compensation package effective from "
        f"<b>{effective_date.strftime('%d %B, %Y')}</b>.",
        body_style
    ))
    story.append(Spacer(1, 0.15*inch))

    # Performance section
    if performance_rating:
        story.append(Paragraph("<b>Performance Assessment</b>", heading_style))
        story.append(Paragraph(
            f"Your performance rating for FY 2025-26: <b>{performance_rating}</b> ({grade_label or 'N/A'})",
            body_style
        ))
        story.append(Spacer(1, 0.1*inch))

    # CTC Details Table
    story.append(Paragraph("<b>Revised Compensation Details</b>", heading_style))
    story.append(Spacer(1, 0.1*inch))

    ctc_data = [
        ['Description', 'Current CTC (Annual)', 'New CTC (Annual)', 'Difference'],
        [f'CTC', f'₹ {float(current_ctc):,.2f}', f'₹ {float(new_ctc):,.2f}', f'₹ {float(new_ctc - current_ctc):,.2f}'],
        ['Monthly Salary', f'₹ {float(current_ctc)/12:,.2f}', f'₹ {float(new_ctc)/12:,.2f}', f'₹ {float((new_ctc - current_ctc)/12):,.2f}'],
    ]

    ctc_table = Table(ctc_data, colWidths=[2*inch, 1.5*inch, 1.5*inch, 1.5*inch])
    ctc_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), HexColor('#2d5f8d')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), HexColor('#f0f4f8')),
        ('GRID', (0, 0), (-1, -1), 1, colors.grey),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), HexColor('#f9f9f9')]),
    ]))
    story.append(ctc_table)
    story.append(Spacer(1, 0.15*inch))

    # Breakdown of increase
    total_increase = float(new_ctc) - float(current_ctc)
    total_increase_pct = (total_increase / float(current_ctc) * 100) if current_ctc else 0

    story.append(Paragraph(f"<b>Total Increase:</b> ₹ {total_increase:,.2f} ({total_increase_pct:.2f}%)", body_style))

    # Components breakdown
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph("<b>Components of Increase:</b>", heading_style))

    components = []
    if increment_pct:
        inc_amount = float(current_ctc) * float(increment_pct) / 100
        components.append(f"• Performance Increment: {increment_pct}% (₹ {inc_amount:,.2f})")

    if promotion_pct:
        promo_amount = float(current_ctc) * float(promotion_pct) / 100
        components.append(f"• Promotion Benefit: {promotion_pct}% (₹ {promo_amount:,.2f})")

    if new_designation and old_designation and new_designation != old_designation:
        components.append(f"• Designation: {old_designation} → {new_designation}")

    if components:
        for comp in components:
            story.append(Paragraph(comp, body_style))
    else:
        story.append(Paragraph("• Revised CTC as per performance assessment and business requirements.", body_style))

    story.append(Spacer(1, 0.15*inch))

    # Terms and conditions
    story.append(Paragraph("<b>Terms and Conditions:</b>", heading_style))
    story.append(Paragraph(
        "• This offer is subject to continued satisfactory performance and compliance with company policies.<br/>"
        "• The revised CTC is effective from the date mentioned above.<br/>"
        "• Please confirm your acceptance of this offer within 3 business days.<br/>"
        "• For any queries, please reach out to the Human Resources department.",
        body_style
    ))
    story.append(Spacer(1, 0.2*inch))

    # Closing
    story.append(Paragraph("We look forward to your continued contribution to APIS INDIA's success.", body_style))
    story.append(Spacer(1, 0.1*inch))
    story.append(Paragraph("Regards,<br/><br/>Human Resources Department<br/>APIS INDIA", body_style))

    # Build PDF
    doc.build(story)
    buffer.seek(0)

    # Add password protection using Employee ID with pypdf
    password = emp_id or 'EMPLOYEE'
    try:
        # Read the generated PDF
        reader = PdfReader(buffer)
        writer = PdfWriter()

        # Copy all pages
        for page in reader.pages:
            writer.add_page(page)

        # Encrypt with employee ID as password
        writer.encrypt(user_password=password, owner_password='APIS_ADMIN')

        # Write encrypted PDF to new buffer
        encrypted_buffer = io.BytesIO()
        writer.write(encrypted_buffer)
        encrypted_buffer.seek(0)
        return encrypted_buffer
    except Exception as e:
        # If encryption fails, log error and return unencrypted PDF
        import logging
        logging.error(f"PDF encryption failed: {str(e)}")
        buffer.seek(0)
        return buffer


def send_offer_letter_email(employee_email, employee_name, pdf_buffer, effective_date):
    """Send offer letter PDF via email."""
    from django.conf import settings

    subject = f"Your CTC Revision Letter - Effective {effective_date.strftime('%d %B, %Y')}"

    body = f"""
Dear {employee_name},

Please find attached your CTC Revision Letter. This letter contains details of your revised compensation package.

If you have any questions or concerns, please reach out to the HR department.

Best Regards,
APIS INDIA - Human Resources Team
    """

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.EMAIL_HOST_USER,
        to=[employee_email],
    )

    email.attach(f'CTC_Letter_{employee_name.replace(" ", "_")}.pdf',
                pdf_buffer.read(),
                'application/pdf')

    email.send(fail_silently=False)
