"""Client for Pocket HRMS GetEmployeeMaster, and the sync that feeds the portal.

Things the vendor's documentation gets wrong, or leaves for their support to
clarify by hand:

  * ModifiedDate is documented as an ISO date ("2024-01-01"). It is actually a
    RANGE, "dd/MM/yyyy - dd/MM/yyyy". The doc's own example cURL fails with
    400 as printed. Confirmed correct by their support after we reported it.
  * An unauthenticated or bad-token request answers 500, not 401/403 - so a
    500 here means "your token is wrong", not "their server is broken".
  * EmployeeFields is not a fixed vocabulary shared across every tenant - it
    is whatever field names THIS company's Pocket HRMS admin configured under
    Cloud Portal > Settings > Configurations > Fields. "EmailId" and "Email"
    are guesses, not confirmed names, until a real call proves them. See
    discover_fields() below - it is the vendor-recommended way to find out
    which names actually exist for APIS's tenant, and it works by leaving the
    EmployeeFields header off entirely rather than guessing at it.
  * Their staging documentation pointed at essapistaging.pockethrms.com:8343,
    which is firewalled from the office network. Their support corrected this
    to https://pockethrmsnext.pockethrms.com - see POCKET_HRMS_BASE_URL in
    settings.py. This is STAGING; production may or may not be the same host,
    and nobody here has confirmed that yet with a working token.

Employee master data is read-only to us. Nothing in this module writes back.
"""
from datetime import date

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import (DEFAULT_APPS, SUPERADMIN_BOOTSTRAP_EMAIL,
                      HrmsSyncLog, PortalUser)

# The fields the portal asks for by default. These are GUESSES at common
# Pocket HRMS column names, not confirmed for this tenant - APIS's actual
# configured field names are only known once discover_fields() has been run
# with a real token and someone has read the result. Override via
# POCKET_HRMS_EMPLOYEE_FIELDS (comma-separated) once they are known, rather
# than editing code - see settings.py.
_DEFAULT_FIELDS = [
    'Id', 'Code', 'Fname', 'Lname', 'EmailId', 'Email',
    'Department', 'Designation', 'Location', 'EmpStatus',
    'PocketReportingManager',
]


def _configured_fields():
    override = getattr(settings, 'POCKET_HRMS_EMPLOYEE_FIELDS', '')
    if override:
        return [f.strip() for f in override.split(',') if f.strip()]
    return list(_DEFAULT_FIELDS)


# Kept as a module-level name for the admin preview endpoint and tests, which
# read EMPLOYEE_FIELDS to show "what we currently ask for". Resolved once at
# import, same as every other Django setting - change POCKET_HRMS_EMPLOYEE_
# FIELDS in .env and restart the process, same as any other config change.
EMPLOYEE_FIELDS = _configured_fields()

PAGE_SIZE = 200          # tuned down if the API starts timing out
MAX_PAGES = 100          # a stop, so a paging bug cannot loop forever


class HrmsError(RuntimeError):
    """Anything that stopped us getting a clean employee list."""


def _cfg(name, default=''):
    return getattr(settings, name, default) or default


def is_configured():
    """Whether a token has been supplied at all - checked before offering sync."""
    return bool(_cfg('POCKET_HRMS_TOKEN'))


def _headers(extra=None, fields=EMPLOYEE_FIELDS):
    """`fields=None` deliberately omits the EmployeeFields header rather than
    sending an empty one - that is what makes fetch_page(fields=None) the
    vendor-recommended discovery call, not just a request for zero columns."""
    token = _cfg('POCKET_HRMS_TOKEN')
    if not token:
        raise HrmsError(
            'No Pocket HRMS token configured. Set POCKET_HRMS_TOKEN in the '
            'environment (ask Pocket HRMS for the company token).')
    h = {'Content-Type': 'application/json', 'authorization': token}
    if fields:
        h['EmployeeFields'] = ','.join(fields)
    h.update(extra or {})
    return h


def fetch_page(take=PAGE_SIZE, offset=0, emp_status='ALL', modified_since=None,
               fields=EMPLOYEE_FIELDS):
    """One page of the employee master.

    Pass fields=None to leave the EmployeeFields header off entirely - Pocket
    HRMS then returns whatever columns are configured for this tenant, which
    is the supported way to find out their real names (see discover_fields).
    """
    base = _cfg('POCKET_HRMS_BASE_URL', 'https://api.pockethrms.com').rstrip('/')
    extra = {'Take': str(take), 'OffSet': str(offset), 'EmpStatus': emp_status}
    if modified_since:
        # The range format the API actually wants - see the module docstring.
        extra['ModifiedDate'] = '%s - %s' % (
            modified_since.strftime('%d/%m/%Y'), date.today().strftime('%d/%m/%Y'))
    try:
        r = requests.get(f'{base}/api/EmployeeMaster/GetEmployeeMaster',
                         headers=_headers(extra, fields=fields), timeout=60)
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


def fetch_all(emp_status='ALL', modified_since=None, fields=EMPLOYEE_FIELDS):
    """Every employee, walking the pagination to the end."""
    rows, offset = [], 0
    for _ in range(MAX_PAGES):
        page = fetch_page(take=PAGE_SIZE, offset=offset, emp_status=emp_status,
                          modified_since=modified_since, fields=fields)
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE
    raise HrmsError(f'Stopped after {MAX_PAGES} pages ({len(rows)} records) - '
                    'the API kept returning full pages, which looks like a paging loop.')


def discover_fields(sample_size=3):
    """What Pocket HRMS is actually configured to call each employee column,
    for THIS tenant - straight from their support: call the API with no
    EmployeeFields header and read back whatever it sends.

    A handful of rows is enough; this exists to read column NAMES, not data.
    """
    rows = fetch_page(take=sample_size, offset=0, emp_status='ALL', fields=None)
    columns = sorted({k for r in rows if isinstance(r, dict) for k in r})
    return columns, rows


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


def sync_employees(triggered_by='', emp_status='ALL', modified_since=None,
                   deactivate_missing=True, fields=EMPLOYEE_FIELDS):
    """Pull the employee master into PortalUser rows.

    A sync owns identity and nothing else. is_superadmin, app_access and the
    portal's own bookkeeping are never touched here - so re-running a sync can
    never quietly hand someone access or take it away.

    Employees without an email address are counted and skipped rather than
    failing the run: email is the login identity, and a row that cannot sign in
    is worse than absent because it looks like an account that should work.

    The write loop runs in its own transaction so that any failure - not just
    a known HrmsError - rolls back the half-written batch of PortalUser rows
    while still leaving a log entry behind. Losing the writes but keeping no
    record of what happened is what made a bad sync invisible.
    """
    log = HrmsSyncLog(triggered_by=triggered_by or 'unknown')
    try:
        rows = fetch_all(emp_status=emp_status, modified_since=modified_since, fields=fields)
    except HrmsError as e:
        log.ok, log.message, log.finished_at = False, str(e), timezone.now()
        log.save()
        raise

    try:
        with transaction.atomic():
            _write_employees(log, rows, deactivate_missing, modified_since, emp_status)
    except Exception as e:
        log.ok, log.message, log.finished_at = False, f'Sync failed partway through: {e}', timezone.now()
        log.save()
        raise

    return log


def _write_employees(log, rows, deactivate_missing, modified_since, emp_status):
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
