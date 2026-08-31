"""Client for Pocket HRMS GetEmployeeMaster, and the sync that feeds the portal.

Two things the vendor's documentation gets wrong, both found by calling the
live API rather than reading the PDF:

  * ModifiedDate is documented as an ISO date ("2024-01-01"). It is actually a
    RANGE, "dd/MM/yyyy - dd/MM/yyyy". The doc's own example cURL fails with
    400 as printed.
  * An unauthenticated or bad-token request answers 500, not 401/403 - so a
    500 here means "your token is wrong", not "their server is broken".

Employee master data is read-only to us. Nothing in this module writes back.
"""
import json
from datetime import date, timedelta

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import (DEFAULT_APPS, SUPERADMIN_BOOTSTRAP_EMAIL,
                      HrmsSyncLog, PortalUser)

# The fields the portal actually needs. Asked for explicitly rather than
# "everything", so a change upstream cannot quietly start shipping salary or
# password columns into a directory that does not need them.
EMPLOYEE_FIELDS = [
    'Id', 'Code', 'Fname', 'Lname', 'EmailId', 'Email',
    'Department', 'Designation', 'Location', 'EmpStatus',
    'PocketReportingManager',
]

PAGE_SIZE = 200          # tuned down if the API starts timing out
MAX_PAGES = 100          # a stop, so a paging bug cannot loop forever


class HrmsError(RuntimeError):
    """Anything that stopped us getting a clean employee list."""


def _cfg(name, default=''):
    return getattr(settings, name, default) or default


def is_configured():
    """Whether a token has been supplied at all - checked before offering sync."""
    return bool(_cfg('POCKET_HRMS_TOKEN'))


def _headers(extra=None):
    token = _cfg('POCKET_HRMS_TOKEN')
    if not token:
        raise HrmsError(
            'No Pocket HRMS token configured. Set POCKET_HRMS_TOKEN in the '
            'environment (ask Pocket HRMS for the company token).')
    h = {
        'Content-Type': 'application/json',
        'authorization': token,
        'EmployeeFields': ','.join(EMPLOYEE_FIELDS),
    }
    h.update(extra or {})
    return h


def fetch_page(take=PAGE_SIZE, offset=0, emp_status='ALL', modified_since=None):
    """One page of the employee master."""
    base = _cfg('POCKET_HRMS_BASE_URL', 'https://api.pockethrms.com').rstrip('/')
    extra = {'Take': str(take), 'OffSet': str(offset), 'EmpStatus': emp_status}
    if modified_since:
        # The range format the API actually wants - see the module docstring.
        extra['ModifiedDate'] = '%s - %s' % (
            modified_since.strftime('%d/%m/%Y'), date.today().strftime('%d/%m/%Y'))
    try:
        r = requests.get(f'{base}/api/EmployeeMaster/GetEmployeeMaster',
                         headers=_headers(extra), timeout=60)
    except requests.RequestException as e:
        raise HrmsError(f'Could not reach Pocket HRMS: {e}') from e

    if r.status_code == 500:
        # Their 500 for auth failures - say what it actually means.
        raise HrmsError('Pocket HRMS rejected the request (HTTP 500). This is what '
                        'their API returns for a missing or invalid company token.')
    if r.status_code != 200:
        raise HrmsError(f'Pocket HRMS returned HTTP {r.status_code}: {r.text[:300]}')
    try:
        body = r.json()
    except ValueError as e:
        raise HrmsError(f'Pocket HRMS returned a non-JSON body: {r.text[:300]}') from e
    if isinstance(body, dict):
        # Their documented failure shape: {"success": false, "message": "..."}
        raise HrmsError(str(body.get('message') or body)[:300])
    if not isinstance(body, list):
        raise HrmsError(f'Expected a JSON array of employees, got {type(body).__name__}.')
    return body


def fetch_all(emp_status='ALL', modified_since=None):
    """Every employee, walking the pagination to the end."""
    rows, offset = [], 0
    for _ in range(MAX_PAGES):
        page = fetch_page(take=PAGE_SIZE, offset=offset,
                          emp_status=emp_status, modified_since=modified_since)
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE
    raise HrmsError(f'Stopped after {MAX_PAGES} pages ({len(rows)} records) - '
                    'the API kept returning full pages, which looks like a paging loop.')


def _pick(row, *names):
    """First non-empty value among several possible column spellings.

    The API resolves lookup columns to strings but is not consistent about
    casing between deployments, and email in particular appears as EmailId on
    some tenants and Email on others.
    """
    for n in names:
        for key in (n, n.lower(), n.upper()):
            v = row.get(key)
            if v not in (None, '', 'NULL'):
                return str(v).strip()
    return ''


def _name_of(row):
    first = _pick(row, 'Fname', 'FName', 'FirstName')
    last = _pick(row, 'Lname', 'LName', 'LastName')
    return ' '.join(p for p in (first, last) if p).strip()


def _is_active(row):
    """EmpStatus comes back as a word when asked for by name."""
    return _pick(row, 'EmpStatus', 'Status').strip().lower() in ('active', 'a', '1', 'true')


@transaction.atomic
def sync_employees(triggered_by='', emp_status='ALL', modified_since=None,
                   deactivate_missing=True):
    """Pull the employee master into PortalUser rows.

    A sync owns identity and nothing else. is_superadmin, app_access and the
    portal's own bookkeeping are never touched here - so re-running a sync can
    never quietly hand someone access or take it away.

    Employees without an email address are counted and skipped rather than
    failing the run: email is the login identity, and a row that cannot sign in
    is worse than absent because it looks like an account that should work.
    """
    log = HrmsSyncLog(triggered_by=triggered_by or 'unknown')
    try:
        rows = fetch_all(emp_status=emp_status, modified_since=modified_since)
    except HrmsError as e:
        log.ok, log.message, log.finished_at = False, str(e), timezone.now()
        log.save()
        raise

    log.fetched = len(rows)
    seen_codes = []

    for row in rows:
        code = _pick(row, 'Code', 'EmpCode', 'EmployeeCode')
        email = _pick(row, 'EmailId', 'Email', 'OfficialEmail', 'EmailAddress').lower()
        if not code:
            continue
        if not email:
            log.skipped_no_email += 1
            continue
        seen_codes.append(code)

        fields = {
            'email': email,
            'name': _name_of(row) or code,
            'designation': _pick(row, 'Designation'),
            'department': _pick(row, 'Department'),
            'location': _pick(row, 'Location'),
            'reporting_manager_code': _pick(row, 'PocketReportingManager', 'ReportingManager'),
            'hrms_id': _pick(row, 'Id'),
            'is_active': _is_active(row),
            'from_hrms': True,
            'last_synced_at': timezone.now(),
            # Verbatim, so the console can show what upstream actually sent.
            'hrms_raw': row,
        }

        existing = (PortalUser.objects.filter(employee_code=code).first()
                    or PortalUser.objects.filter(email__iexact=email).first())
        if existing:
            # A row typed into the console before HRMS knew about this person
            # is adopted rather than duplicated.
            existing.employee_code = code
            for k, v in fields.items():
                setattr(existing, k, v)
            # The founding account can never be deactivated by an upstream
            # status change - that is how a company locks itself out.
            if existing.is_bootstrap_superadmin:
                existing.is_active = True
                existing.is_superadmin = True
            existing.save()
            log.updated += 1
        else:
            PortalUser.objects.create(employee_code=code, app_access=list(DEFAULT_APPS), **fields)
            log.created += 1

    # Someone who has left stops appearing in the feed. Their access is closed
    # rather than their record deleted, so their history stays attributable.
    # Only ever applied to rows HRMS owns, and only on a full sync - a filtered
    # or incremental pull is not evidence that anyone left.
    if deactivate_missing and seen_codes and not modified_since and emp_status == 'ALL':
        gone = (PortalUser.objects
                .filter(from_hrms=True, is_active=True)
                .exclude(employee_code__in=seen_codes)
                .exclude(is_superadmin=True)
                .exclude(email__iexact=SUPERADMIN_BOOTSTRAP_EMAIL))
        log.deactivated = gone.count()
        gone.update(is_active=False)

    log.ok, log.finished_at = True, timezone.now()
    log.message = (f'{log.created} created, {log.updated} updated, '
                   f'{log.deactivated} deactivated, {log.skipped_no_email} skipped (no email).')
    log.save()
    return log
