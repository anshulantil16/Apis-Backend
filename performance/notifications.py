"""
Appraisal email notifications — sent after each stage transition.
Emails are dispatched in a daemon thread so the HTTP response is never delayed
by SMTP latency. Failures are silently ignored.
"""
import threading

from django.conf import settings
from django.core.mail import send_mail

from .models import EmployeeProfile


def _send(subject: str, body: str, recipient_emails: list[str]):
    emails = [e for e in recipient_emails if e]
    if not emails:
        return

    def _do_send():
        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=emails,
                fail_silently=True,
            )
        except Exception:
            pass

    t = threading.Thread(target=_do_send, daemon=True)
    t.start()


def notify_manager_on_employee_submit(gc):
    """Employee submitted → notify their reporting manager."""
    emp = gc.employee
    cycle_name = gc.cycle.name

    try:
        manager = EmployeeProfile.objects.get(employee_id=emp.reporting_manager_id)
    except EmployeeProfile.DoesNotExist:
        return

    subject = f"[APIS Appraisal] {emp.name} has submitted their appraisal form"
    body = f"""Dear {manager.name},

{emp.name} ({emp.designation}) has submitted their Performance Appraisal Form for {cycle_name}.

Please log in to the Appraisal Hub to review and rate their submission.

Employee Details:
  Name        : {emp.name}
  ID          : {emp.employee_id}
  Designation : {emp.designation}
  Department  : {emp.department or '—'}
  Zone        : {emp.zone or '—'}

Action Required: Review KPIs, fill UPLIFT values assessment, and submit your rating.

Regards,
APIS India — Appraisal System
"""
    _send(subject, body, [manager.email])


def notify_on_manager_approval(gc, manager_name: str):
    """Manager approved → notify HOD and the employee."""
    emp = gc.employee
    cycle_name = gc.cycle.name

    try:
        hod = EmployeeProfile.objects.get(employee_id=emp.hod_id)
        hod_email = hod.email
        hod_name = hod.name
    except EmployeeProfile.DoesNotExist:
        hod_email = ''
        hod_name = 'HOD'

    # Notify HOD
    hod_subject = f"[APIS Appraisal] Manager review completed for {emp.name} — awaiting your review"
    hod_body = f"""Dear {hod_name},

{manager_name} has completed the Manager Review for {emp.name}'s appraisal for {cycle_name}.

The form is now awaiting your HOD review.

Employee Details:
  Name        : {emp.name}
  ID          : {emp.employee_id}
  Designation : {emp.designation}
  Department  : {emp.department or '—'}
  Zone        : {emp.zone or '—'}

Please log in to the Appraisal Hub to complete your HOD assessment.

Regards,
APIS India — Appraisal System
"""
    _send(hod_subject, hod_body, [hod_email])

    # Notify employee
    emp_subject = f"[APIS Appraisal] Your appraisal has been reviewed by your Manager"
    emp_body = f"""Dear {emp.name},

Your Performance Appraisal Form for {cycle_name} has been reviewed and approved by your Reporting Manager ({manager_name}).

Your form has been forwarded to the HOD for further review.

You will receive another notification once the HOD review is complete.

Regards,
APIS India — Appraisal System
"""
    _send(emp_subject, emp_body, [emp.email])


def notify_on_hod_approval(gc, hod_name: str):
    """HOD approved → notify the employee and all HR admins."""
    emp = gc.employee
    cycle_name = gc.cycle.name

    # Notify employee
    emp_subject = f"[APIS Appraisal] Your appraisal has been approved by the HOD"
    emp_body = f"""Dear {emp.name},

Great news! Your Performance Appraisal Form for {cycle_name} has been reviewed and approved by the HOD ({hod_name}).

Your appraisal is now complete and has been forwarded to HR for final processing.

Thank you for completing your appraisal for this cycle.

Regards,
APIS India — Appraisal System
"""
    _send(emp_subject, emp_body, [emp.email])

    # Notify all active HR admins
    hr_emails = list(
        EmployeeProfile.objects.filter(user_type='hr', is_active=True)
        .values_list('email', flat=True)
    )
    if hr_emails:
        hr_subject = f"[APIS Appraisal] HOD review completed for {emp.name}"
        hr_body = f"""Dear HR Team,

The HOD ({hod_name}) has completed the review for {emp.name}'s appraisal for {cycle_name}.

Employee Details:
  Name        : {emp.name}
  ID          : {emp.employee_id}
  Designation : {emp.designation}
  Department  : {emp.department or '—'}
  Zone        : {emp.zone or '—'}

The appraisal is now awaiting HR's final review and processing.

Regards,
APIS India — Appraisal System
"""
        _send(hr_subject, hr_body, hr_emails)
