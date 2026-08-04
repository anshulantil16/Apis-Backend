"""Warning / disciplinary letter PDF generation and email automation.

Second component of the Letters Generator (see offer_letter.py for the
appraisal / CTC-revision letters). Shares the same letterhead assets, signatory
and auto-fit machinery so both letter types look like they came from the same
office, but keeps its own body content, DB table and history.

Supported letter types (WarningLetter.WARNING_TYPE_CHOICES):
  • Verbal Warning          • Second Written Warning   • Show Cause Notice
  • First Written Warning   • Final Warning
A free-text `warning_type_label` overrides the heading when HR needs wording
the fixed list doesn't cover.

Target length is ONE page. Content is never dropped to make it fit — the
font auto-shrinks instead (same rule as the appraisal letters).
"""
import io
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                TableStyle, Image, HRFlowable, KeepTogether)
from reportlab.lib import colors

# Reuse the appraisal letter's letterhead assets/signatory so both letters
# share one visual identity and one place to update the signature image.
from .offer_letter import (_logo_image, _signature_image, _esc,
                           SIGNATORY_NAME, SIGNATORY_TITLE,
                           NAVY, GOLD, GREY)

# Heading + the "nature of this letter" sentence for each type. Keeping these
# side by side makes it obvious that escalation wording changes with severity.
WARNING_TYPE_TEXT = {
    'verbal': (
        'Verbal Warning',
        'This letter serves as a formal record of the verbal warning issued to you.',
    ),
    'first': (
        'First Written Warning',
        'This letter serves as a <b>First Written Warning</b> and is being placed on your '
        'personnel record.',
    ),
    'second': (
        'Second Written Warning',
        'This letter serves as a <b>Second Written Warning</b>. Please note that this follows '
        'earlier counselling/warning on the same or a related matter.',
    ),
    'final': (
        'Final Warning',
        'This letter serves as a <b>Final Warning</b>. Any further recurrence will leave the '
        'Management with no option but to initiate disciplinary action, which may include '
        'termination of your employment.',
    ),
    'show_cause': (
        'Show Cause Notice',
        'This letter is a <b>Show Cause Notice</b> requiring your written explanation before the '
        'Management decides on any further course of action.',
    ),
}

RED = HexColor('#b03030')


def _type_text(warning_type, custom_label=''):
    """(heading, nature-sentence) for a warning type; custom label wins on the heading."""
    heading, nature = WARNING_TYPE_TEXT.get(warning_type, WARNING_TYPE_TEXT['first'])
    label = (custom_label or '').strip()
    return (_esc(label) if label else heading), nature


def _fmt_date(v):
    """Human 'dd Month yyyy' from a date/datetime/whatever-was-typed. Non-dates
    pass through unchanged so free text like 'week of 12 May' still prints."""
    if v is None or str(v).strip() == '':
        return ''
    if isinstance(v, datetime):
        return v.strftime('%d %B %Y')
    if hasattr(v, 'strftime'):
        return v.strftime('%d %B %Y')
    s = str(v).strip()
    try:
        from dateutil import parser as _dateparser
        return _dateparser.parse(s, dayfirst=True).strftime('%d %B %Y')
    except Exception:
        return s


def _paras(text):
    """Split a free-text block into paragraphs on blank lines / newlines, escaped
    for reportlab. HR types these into a textarea, so newlines are meaningful."""
    if not text:
        return []
    chunks = [c.strip() for c in str(text).replace('\r\n', '\n').split('\n')]
    return [_esc(c) for c in chunks if c]


def generate_warning_letter_pdf(employee_name, employee_code, designation='', department='',
                                salutation='Mr./Ms.', warning_type='first', warning_type_label='',
                                subject='', incident_date=None, incident_description='',
                                previous_warning_ref='', corrective_action='',
                                response_due_days=0, letter_date=None,
                                issued_by='', issued_by_designation='',
                                emp_details=None, remarks=''):
    """Render a one-page warning letter and return a BytesIO of the PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=0.72 * inch, rightMargin=0.72 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        title=f"Warning Letter - {employee_name}", author='APIS India Limited',
    )

    ed = emp_details or {}
    letter_date = letter_date or datetime.now().date()
    date_str = _fmt_date(letter_date)
    inc_str = _fmt_date(incident_date)

    heading, nature_sentence = _type_text(warning_type, warning_type_label)
    emp_name = _esc(employee_name or 'Employee')
    emp_code = _esc(employee_code or '—')
    emp_desig = _esc(designation or '')
    emp_dept = _esc(department or '')
    title = _esc((salutation or 'Mr./Ms.').strip())
    signatory = _esc(issued_by.strip()) if (issued_by or '').strip() else SIGNATORY_NAME
    signatory_title = (_esc(issued_by_designation.strip())
                       if (issued_by_designation or '').strip() else SIGNATORY_TITLE)

    subj = _esc(subject.strip()) if (subject or '').strip() else heading

    # ── Body ────────────────────────────────────────────────────────────────
    opening = (
        "This letter is with reference to your conduct/performance in the course of your "
        "employment with <b>APIS India Limited</b>"
        + (f", specifically in connection with the incident dated <b>{inc_str}</b>." if inc_str
           else ".")
    )

    body_paras = [opening]
    incident_bits = _paras(incident_description)
    if incident_bits:
        body_paras.append("The details of the matter are as follows:")
        body_paras.extend(incident_bits)

    if (previous_warning_ref or '').strip():
        body_paras.append(
            "You have previously been advised on this matter "
            f"({_esc(previous_warning_ref.strip())}). Despite this, the Management observes that "
            "the required improvement has not been demonstrated."
        )

    body_paras.append(
        "Such conduct is not in line with the standards of discipline, professionalism and "
        "accountability expected of every employee of APIS India Limited, and is viewed seriously "
        "by the Management. " + nature_sentence
    )

    corrective_bits = _paras(corrective_action)
    if corrective_bits:
        body_paras.append("You are advised to take the following corrective action with immediate effect:")
        body_paras.extend(corrective_bits)
    else:
        body_paras.append(
            "You are advised to ensure immediate and sustained improvement, and to strictly adhere "
            "to the Company's policies, code of conduct and the standards expected of your role."
        )

    try:
        due_days = int(response_due_days or 0)
    except (TypeError, ValueError):
        due_days = 0
    if due_days > 0:
        body_paras.append(
            f"You are required to submit your written explanation to the People &amp; Culture "
            f"department within <b>{due_days} day(s)</b> of receipt of this letter. Failure to "
            "respond within the stipulated time will be construed as your acceptance of the above, "
            "and the Management shall be free to proceed as it deems appropriate."
        )

    if warning_type != 'final':
        body_paras.append(
            "Please treat this as a serious communication. Any recurrence of such conduct will "
            "invite further disciplinary action as per the Company's policies."
        )

    body_paras.append(
        "We trust you will take this communication in the right spirit and demonstrate the "
        "improvement expected of you."
    )

    remark_bits = _paras(remarks)

    ack_line = (
        "<b>Acknowledgement:</b> I have received and read the contents of this letter and "
        "understand the same."
    )
    sign_line = ("Employee Signature: ______________________ &nbsp;&nbsp; "
                 "Name: ______________________ &nbsp;&nbsp; Date: ________________")
    copy_line = "<b>cc:</b> Personnel File &nbsp;|&nbsp; Reporting Manager &nbsp;|&nbsp; People &amp; Culture"

    # ── Page builder parameterised by shrink scale (auto-fit to one page) ────
    def build(s):
        t_title = ParagraphStyle('WTitle', fontSize=15 * s, textColor=NAVY, alignment=TA_CENTER,
                                 spaceAfter=2 * s, fontName='Helvetica-Bold', leading=18 * s)
        t_conf = ParagraphStyle('WConf', fontSize=9.5 * s, textColor=RED, alignment=TA_CENTER,
                                spaceAfter=8 * s, fontName='Helvetica-BoldOblique')
        t_meta = ParagraphStyle('WMeta', fontSize=10 * s, leading=13.5 * s, spaceAfter=1)
        t_subj = ParagraphStyle('WSubj', fontSize=10.5 * s, leading=14 * s, spaceBefore=6 * s,
                                spaceAfter=6 * s, fontName='Helvetica-Bold')
        t_body = ParagraphStyle('WBody', fontSize=10 * s, leading=13.5 * s, spaceAfter=6.5 * s,
                                alignment=TA_JUSTIFY)
        t_sign = ParagraphStyle('WSign', fontSize=10 * s, leading=13.5 * s)
        t_small = ParagraphStyle('WSmall', fontSize=8.5 * s, leading=10.5 * s, spaceAfter=4 * s,
                                 alignment=TA_JUSTIFY, textColor=GREY)
        t_ack = ParagraphStyle('WAck', fontSize=9 * s, leading=12 * s, spaceBefore=3 * s,
                               spaceAfter=5 * s)

        f = []
        img = _logo_image(1.5 * s)
        if img is not None:
            img.hAlign = 'CENTER'
            f.append(img)
            f.append(Spacer(1, 0.06 * inch * s))
        f.append(HRFlowable(width='100%', thickness=1.4, color=GOLD, spaceBefore=2, spaceAfter=8 * s))
        f.append(Paragraph(heading, t_title))
        f.append(Paragraph('Private &amp; Confidential', t_conf))
        f.append(Paragraph(f"<b>Date:</b> {date_str}", t_meta))
        f.append(Spacer(1, 0.04 * inch * s))
        f.append(Paragraph(f"<b>Employee Name:</b> {emp_name}", t_meta))
        f.append(Paragraph(f"<b>Employee Code:</b> {emp_code}", t_meta))
        f.append(Paragraph(f"<b>Designation:</b> {emp_desig or '—'}", t_meta))
        f.append(Paragraph(f"<b>Department:</b> {emp_dept or '—'}", t_meta))
        if ed.get('work_location'):
            f.append(Paragraph(f"<b>Work Location:</b> {_esc(ed['work_location'])}", t_meta))
        if ed.get('reporting_manager'):
            f.append(Paragraph(f"<b>Reporting Manager:</b> {_esc(ed['reporting_manager'])}", t_meta))
        f.append(Paragraph(f"<b>Subject:</b> {subj}", t_subj))
        f.append(Paragraph(f"Dear {title} {emp_name},", t_body))
        for p in body_paras:
            f.append(Paragraph(p, t_body))
        for p in remark_bits:
            f.append(Paragraph(p, t_body))
        f.append(Spacer(1, 0.06 * inch * s))
        f.append(KeepTogether([
            Paragraph("For <b>APIS India Limited</b>", t_sign),
            (_signature_image(1.3 * s) or Spacer(1, 0.32 * inch * s)),
            Paragraph(f"<b>{signatory}</b>", t_sign),
            Paragraph(f"<b>{signatory_title}</b>", t_sign),
        ]))
        f.append(Spacer(1, 0.08 * inch * s))
        f.append(HRFlowable(width='100%', thickness=0.7, color=colors.grey, spaceAfter=7 * s))
        f.append(Paragraph(ack_line, t_ack))
        f.append(Paragraph(sign_line, t_ack))
        f.append(Paragraph(copy_line, t_small))
        return f

    # ── Auto-fit: shrink until it fits one page (never drop content) ─────────
    avail_w, avail_h = doc.width, doc.height

    def _measure(flows):
        total = 0.0
        for fl in flows:
            content = getattr(fl, '_content', None)  # KeepTogether reports 0 alone
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

    # 0.90 (not a razor-thin 0.96) leaves real margin because _measure()
    # approximates the final layout — same reasoning as the appraisal letters.
    limit = avail_h * 0.90
    scales = (1.0, 0.97, 0.94, 0.91, 0.88, 0.85, 0.82, 0.79, 0.76, 0.73, 0.70,
              0.67, 0.64, 0.61, 0.58, 0.55)
    scale = scales[-1]
    for sc in scales:
        if _measure(build(sc)) <= limit:
            scale = sc
            break

    doc.build(build(scale))
    buffer.seek(0)
    return buffer


def send_warning_letter_email(employee_email, employee_name, pdf_buffer, heading,
                              warning_letter_id=None, connection=None, filename=None,
                              cc=None):
    """Email the warning letter PDF.

    From = the HR account (WARNING_LETTER_/OFFER_LETTER_ email settings), To =
    the employee, Cc = `cc` (list of addresses — typically the reporting
    manager, HOD and People & Culture, who need a record of the warning).

    Deliberately terse and neutral compared with the celebratory appraisal
    email — this is a disciplinary communication. Pass a shared `connection`
    when sending in bulk so one SMTP session serves the whole batch."""
    from django.core.mail import EmailMessage
    from django.conf import settings
    from html import escape as _hesc

    safe_name = _hesc(employee_name or 'Employee')
    safe_heading = _hesc(heading or 'Warning Letter')
    subject = f"APIS India — {safe_heading} (Private & Confidential)"

    FONT = "font-family: Aptos, Calibri, Arial, sans-serif;"
    body = f"""
<html>
<body style="{FONT} font-size: 11pt; line-height: 1.6; color: #222;">
<div style="max-width: 640px; padding: 0 12px;">
    <p>Dear {safe_name},</p>

    <p>Please find attached a communication from the People &amp; Culture department of
    <b>APIS INDIA LIMITED</b> regarding <b>{safe_heading}</b>.</p>

    <p>You are requested to go through the attached letter carefully and acknowledge receipt of the
    same. Should you wish to submit any explanation or clarification, please respond within the
    timeline mentioned in the letter.</p>

    <p>This communication is <b>strictly private and confidential</b> and is intended solely for the
    addressee.</p>

    <p>Regards,<br>
    <b>People &amp; Culture</b><br>
    APIS India Limited</p>

    <p style="font-size: 9pt; color: #777;">This is a system-generated email. For any clarification,
    please contact the People &amp; Culture department.</p>
</div>
</body>
</html>
"""
    from_email = (getattr(settings, 'WARNING_LETTER_EMAIL_HOST_USER', None)
                  or getattr(settings, 'OFFER_LETTER_EMAIL_HOST_USER', None)
                  or settings.EMAIL_HOST_USER)
    # Drop blanks and anything identical to the To address — a duplicate Cc
    # delivers the employee two copies of their own warning letter.
    cc_clean = [e.strip() for e in (cc or []) if e and e.strip()
                and e.strip().lower() != str(employee_email or '').strip().lower()]
    msg = EmailMessage(subject=subject, body=body, from_email=from_email,
                       to=[employee_email], cc=cc_clean or None, connection=connection)
    msg.content_subtype = 'html'
    pdf_buffer.seek(0)
    msg.attach(filename or f"Warning_Letter_{(employee_name or 'employee').replace(' ', '_')}.pdf",
               pdf_buffer.read(), 'application/pdf')
    msg.send(fail_silently=False)
    return True
