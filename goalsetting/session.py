"""Who is actually making a request.

Everything here used to be taken on trust. A request said `role: 'admin'`, or
named an employee_id, and the API believed it. That is survivable while a
product is being demonstrated to a few people; it is not survivable once every
employee in the company has the URL, because the endpoints that trust it
include `/reset/`, which deletes every goal sheet and every version of it.

So a verified OTP now mints a token, the client returns it in a header, and
identity is read from that alone. Same shape as AdminPulse's, and for the same
reason: an id in a request body is something the caller typed, never proof of
who they are.

Kept in the database cache rather than in memory, because gunicorn runs several
worker processes and a token minted in one must be recognised by the others.
"""
import secrets

from django.core.cache import cache

from .models import EmployeeProfile

SESSION_HEADER = 'X-GoalSetting-Session'
_TTL = 12 * 3600
_PREFIX = 'goalsetting_session_'


def issue_session(employee):
    """-> an opaque token standing for a verified sign-in."""
    token = secrets.token_urlsafe(32)
    cache.set(_PREFIX + token, employee.employee_id, timeout=_TTL)
    return token


def session_employee_id(request):
    """-> the employee_id behind this request, or '' if it is unproven."""
    token = (request.headers.get(SESSION_HEADER) or '').strip()
    if not token:
        auth = (request.headers.get('Authorization') or '').strip()
        if auth.lower().startswith('bearer '):
            token = auth[7:].strip()
    if not token:
        return ''
    return cache.get(_PREFIX + token) or ''


def session_employee(request):
    """-> the EmployeeProfile behind this request, or None."""
    emp_id = session_employee_id(request)
    if not emp_id:
        return None
    return EmployeeProfile.objects.filter(employee_id__iexact=emp_id, is_active=True).first()


def revoke_session(request):
    token = (request.headers.get(SESSION_HEADER) or '').strip()
    if token:
        cache.delete(_PREFIX + token)


def is_admin(request):
    emp = session_employee(request)
    return bool(emp and emp.user_type == 'admin')
