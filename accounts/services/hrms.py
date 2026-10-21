"""Client for Pocket HRMS GetEmployeeMaster, and the sync that feeds the portal.

Things the vendor's documentation gets wrong, or leaves for their support to
clarify by hand:

  * OffSet is 1-BASED, and 0 is not "start at the beginning" - it returns an
    empty array. Every request must start at OffSet=1 or nothing comes back
    at all, with a perfectly healthy 200 OK and an empty list to show for it.
    This cost us weeks of "the token must be wrong"; confirmed by their
    support 09-09-2026.
  * ModifiedDate is documented as an ISO date ("2024-01-01"). It is actually a
    RANGE, "dd/MM/yyyy - dd/MM/yyyy". The doc's own example cURL fails with
    400 as printed. Confirmed correct by their support after we reported it.
  * An unauthenticated or bad-token request answers 500, not 401/403 - so a
    500 here means "your token is wrong", not "their server is broken".
  * EmployeeFields is REQUIRED to get values, not just to narrow the payload.
    Without it the API still returns a row per employee, but almost every
    column comes back null - so an omitted header looks like "we have 2000
    blank employees" rather than an error.
  * The resolved-name columns (DepartmentString, DesignationString,
    LocationString, GradeString, CategoryString) only populate if the RAW
    numeric column is requested alongside them. Asking for DepartmentString
    on its own returns null; asking for "Department,DepartmentString" returns
    "SALES (MT)". Hence the pairs in _DEFAULT_FIELDS below - do not "tidy" the
    raw ids out of that list, they are load-bearing.
  * EmpStatus as a REQUEST header does not filter anything - Live, ALL and A
    all return the same complete list. The filtering has to happen here. As a
    RESPONSE value it is a word: Live, Resignation, Separation, Suspension,
    Termination, Abscond, Expired, Other. Only "Live" is currently employed.
  * Their staging documentation pointed at essapistaging.pockethrms.com:8343,
    which is firewalled from the office network. Their support corrected this
    to https://pockethrmsnext.pockethrms.com - see POCKET_HRMS_BASE_URL in
    settings.py. This is STAGING; production is api.pockethrms.com, confirmed
    working 09-09-2026.

Not available on APIS's tenant as of 09-09-2026: the reporting-manager link.
PocketReportingManager, POCKETREPORTINGMANAGERstring, ManagerNames, LeaderId
and Employeeleaderstring are all empty for all 2,365 employees, so who reports
to whom cannot be synced yet and still has to be maintained by hand. They are
requested anyway, so the day HR fills them in this starts working on its own.

Employee master data is read-only to us. Nothing in this module writes back.
"""
import re
from datetime import date, datetime

import requests
from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone

from ..models import (DEFAULT_APPS, SUPERADMIN_BOOTSTRAP_EMAIL,
                      HrmsSyncLog, PortalUser)

# The fields the portal asks for. Confirmed against APIS's live tenant on
# 09-09-2026 - these are the real column names, no longer guesses.
#
# The raw/String pairs are deliberate: DepartmentString is null unless
# Department is asked for too (see the module docstring). Override via
# POCKET_HRMS_EMPLOYEE_FIELDS (comma-separated) rather than editing code.
_DEFAULT_FIELDS = [
    'Id', 'Code', 'FName', 'MName', 'LName',
    'Email', 'PersonalEmail',
    'Department', 'DepartmentString',
    'Designation', 'DesignationString',
    'Location', 'LocationString',
    # GRADE, not Grade/GradeString. On the filtered endpoint the cadre code
    # arrives under this name and carries the value itself -- "05", "M4" --
    # with no String partner. Both spellings are asked for and both are read,
    # so neither endpoint loses it.
    'GRADE', 'Grade', 'GradeString',
    'Category', 'CategoryString',
    # Asked for in CAPITALS, which is not a style choice. Their own example
    # sends POCKETREPORTINGMANAGER and the reply comes back keyed
    # PocketReportingManager; asked for in that mixed case the column is
    # simply absent, with a 200 and no complaint -- the same silent miss that
    # hid GRADE for months. First tried on 15-10-2026 in mixed case: one
    # employee in 703 came back with a manager. Every spelling is sent, since
    # an unrecognised field name costs nothing and a missing column costs a
    # re-run against 2,400 people.
    'POCKETREPORTINGMANAGER',
    'PocketReportingManager', 'POCKETREPORTINGMANAGERstring',
    'EmpStatus', 'DateOfJoining', 'DateOfBirth',
    'OfficeMobileNo', 'PersonalMobileNo',

    # Proved to carry values by `manage.py hrms_columns --probe` on
    # 15-10-2026. Asked for so the master data is complete in one place and
    # nobody has to re-type it into a spreadsheet for the next tool.
    'CostCentre', 'State', 'Gender', 'MaritalStatus', 'ConfirmationDate',

    # Real columns that nobody has filled in yet. Costs nothing to ask, and
    # the day HR maps the reporting line this starts working on its own --
    # which is exactly how PocketReportingManager behaved: written off as
    # "not available on this tenant" for months, then one employee had it.
    # There is no CADRE column of any spelling; the cadre is the letter in
    # front of the grade -- O2, M3, W1 -- so `grade` already carries it.
    'HOD', 'Branch',
]

# Which endpoint. "...filtered" is not a tidier version of the other one: it
# is the only one that answers with GRADE and PocketReportingManager at all.
# On GetEmployeeMaster both come back missing however they are asked for,
# which read for months like "this tenant does not have that data". Their
# support told us the difference on 15-10-2026. Overridable in case they
# rename it again.
EMPLOYEE_PATH = '/api/EmployeeMaster/GetEmployeeMasterfiltered'

# The older endpoint, kept for one job only. The filtered one refuses a call
# with no EmployeeFields header -- 400, "pass at least one field" -- and that
# call is exactly how their support said to read back the column names a
# tenant actually has. So discovery asks the old endpoint, which still
# answers it, and everything else asks the filtered one.
LEGACY_EMPLOYEE_PATH = '/api/EmployeeMaster/GetEmployeeMaster'

# The one EmpStatus that means "works here today". Everything else - the
# resignations, separations, suspensions, terminations - is a former employee
# and must not get a working portal login.
ACTIVE_EMP_STATUS = 'live'

# What to send as the EmpStatus header to be given only current staff.
#
# On the old endpoint this header did nothing: Live, ALL and A all returned
# the complete history, so the filtering had to happen here. On the filtered
# endpoint it works, and it solves something our own code could not. The feed
# carries a rehired person twice, under their old code and their new one, and
# nothing in either row says which is current -- that is why Kanchan Juneja
# appeared as both HO0080 and HO0149, and Gopa Chakraborty as AONE30 and
# HO0175. Asked for Active only, the separated record is simply not there,
# and the code that comes back is the one that is current today.
ACTIVE_ONLY = 'Active'


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

# The share of the directory one sync may switch off before it refuses. See
# the deactivation step in _write_employees.
DEACTIVATION_CEILING = 0.10

PAGE_SIZE = 200          # tuned down if the API starts timing out
MAX_PAGES = 100          # a stop, so a paging bug cannot loop forever
FIRST_OFFSET = 1         # 1-based, and 0 silently returns nothing at all


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


def fetch_page(take=PAGE_SIZE, offset=FIRST_OFFSET, emp_status='ALL', modified_since=None,
               fields=EMPLOYEE_FIELDS, path=None):
    """One page of the employee master.

    offset is 1-based and offset=0 returns an empty list, not the first page -
    see the module docstring. Nothing here should ever pass 0.

    Pass fields=None to leave the EmployeeFields header off entirely - Pocket
    HRMS then returns whatever columns are configured for this tenant, which
    is how discover_fields() reads their names. Note that this also blanks
    almost every VALUE, so it is only good for discovery.
    """
    base = _cfg('POCKET_HRMS_BASE_URL', 'https://api.pockethrms.com').rstrip('/')
    path = path or _cfg('POCKET_HRMS_EMPLOYEE_PATH', EMPLOYEE_PATH)
    extra = {'Take': str(take), 'OffSet': str(offset), 'EmpStatus': emp_status}
    if modified_since:
        # The range format the API actually wants - see the module docstring.
        extra['ModifiedDate'] = '%s - %s' % (
            modified_since.strftime('%d/%m/%Y'), date.today().strftime('%d/%m/%Y'))
    try:
        r = requests.get(f'{base}{path}',
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
    rows, offset = [], FIRST_OFFSET
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
    rows = fetch_page(take=sample_size, offset=FIRST_OFFSET, emp_status='ALL',
                      fields=None, path=LEGACY_EMPLOYEE_PATH)
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


def _resolved(row, base, string_name=None):
    """The readable name behind one of the lookup columns.

    The two endpoints disagree about where it lives, and reading only one of
    them empties a column on the other.

    On the old endpoint a lookup comes back twice: a numeric id under the
    plain name and the text under "<name>String". On the filtered endpoint
    there is no String partner at all -- the plain name carries the text
    itself, the same way GRADE does. A probe on 15-10-2026 confirmed it:
    Category answered with values, CategoryString was not a column.

    So: take the String if it is there, otherwise the plain column -- unless
    the plain column is just digits, which is the old endpoint's id and is
    worse than a blank, because "37" looks like data and reads like nonsense
    on a screen.
    """
    text = _pick(row, string_name or f'{base}String')
    if text:
        return text
    plain = _pick(row, base)
    return '' if plain.isdigit() else plain


def _manager(row):
    """-> (code, name) of this person's reporting manager.

    The feed sends one string with both in it: "(HO0235)Pankaj Tripathi".
    Split rather than stored whole, because the code is what joins a person
    to their manager's record and the name is what a screen shows -- and a
    string holding both is useless for either without doing this first.

    Anything that does not match the shape is kept as the name with no code,
    which is the honest reading of "we were given something, but not an
    employee code".
    """
    raw = _pick(row, 'PocketReportingManager', 'POCKETREPORTINGMANAGERstring',
                'ReportingManager', 'ManagerNames')
    if not raw:
        return '', ''
    m = re.match(r'^\s*\(([^)]+)\)\s*(.*)$', raw)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return '', raw.strip()


def _grade(row):
    """-> the cadre code: "05", "M4", "O2".

    GRADE on the filtered endpoint, Grade/GradeString on the old one. Read
    from whichever is there so a change of endpoint cannot silently empty a
    column that PMS reads.
    """
    return _pick(row, 'GRADE', 'Grade', 'GradeString')


def _date(row, *names):
    """A date out of the feed, or None.

    Values arrive as "1994-03-22T00:00:00". Anything unparseable is dropped
    rather than guessed at - a wrong birthday is worse than a missing one.
    Pocket HRMS also uses 0001-01-01 as its empty date, which is not a real
    date and must not become one.
    """
    raw = _pick(row, *names)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00')).date()
    except ValueError:
        return None
    return parsed if parsed.year > 1900 else None


def _scrub(row, requested):
    """The row, reduced to the columns we actually asked for.

    Keeping it verbatim was fine while the EmployeeFields header was
    honoured and correctly set, which is a sentence with two conditions in
    it. The full employee record includes PanNo, AadharNumberPocket,
    BankAccNo, UANNumber, PFAccNo, ESINo and Password; none of that is any
    business of an intranet, and none of it should be one misconfiguration
    away from sitting in our database.

    The allowlist is what we requested, matched case-insensitively because
    the API is not consistent about casing, plus the "<name>String" partners
    of the lookup columns, which is where the readable values live.
    """
    if not requested:
        return {}
    allowed = set()
    for name in requested:
        allowed.add(name.lower())
        allowed.add(f'{name}string'.lower())
    return {k: v for k, v in row.items()
            if isinstance(k, str) and k.lower() in allowed}


def _name_of(row):
    """Whole name, however this tenant happens to have split it.

    APIS's data mostly puts the entire name in FName ("Ankit Kumar Sharma")
    and leaves MName and LName empty, but a handful of records do use all
    three, so all three are joined rather than assuming either shape.
    """
    parts = [_pick(row, 'FName', 'Fname', 'FirstName'),
             _pick(row, 'MName', 'Mname', 'MiddleName'),
             _pick(row, 'LName', 'Lname', 'LastName')]
    return ' '.join(p for p in parts if p).strip()


def _is_active(row):
    """Whether this person still works here.

    EmpStatus is a word, and "Live" is the only one that means employed - see
    the module docstring for the full list. Everything else (Resignation,
    Separation, Suspension, Termination, Abscond, Expired) is a leaver.
    """
    return _pick(row, 'EmpStatus', 'Status').strip().lower() == ACTIVE_EMP_STATUS


# The widest value each text column will hold, read off the model so the two
# can never drift apart.
def _limit(field):
    return PortalUser._meta.get_field(field).max_length


# Columns that describe a person rather than identify them. Upstream is free
# text typed by HR -- a grade label longer than the column, a second phone
# number squeezed into the mobile field -- and MySQL refuses an over-long
# write outright. That refusal used to end the entire sync: on 12-10-2026 one
# grade of more than 20 characters rolled back all 2,410 rows and the
# directory stopped updating for everybody until somebody noticed.
#
# These are cut to fit and the cut is named in the log. A shortened grade is
# a cosmetic problem; a directory frozen on last week is not, and skipping
# the whole person over one long label would stop their name, department and
# designation updating too.
_TRIMMABLE = ('name', 'designation', 'department', 'location',
              'reporting_manager_code', 'grade', 'category', 'office_mobile')


def _fit(value, field, code, trimmed):
    limit = _limit(field)
    if value and limit and len(value) > limit:
        trimmed.append(f'{code}: {field} was {len(value)} characters, kept the '
                       f'first {limit} — "{value}"')
        return value[:limit]
    return value


def sync_employees(triggered_by='', emp_status=ACTIVE_ONLY, modified_since=None,
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
            _write_employees(log, rows, deactivate_missing, modified_since,
                             emp_status, fields)
    except Exception as e:
        log.ok, log.message, log.finished_at = False, f'Sync failed partway through: {e}', timezone.now()
        log.save()
        raise

    return log


def _write_employees(log, rows, deactivate_missing, modified_since, emp_status,
                     requested=None):
    log.fetched = len(rows)
    seen_codes = []
    claimed_by = {}          # email -> the employee code that got there first
    clashes = []
    trimmed = []             # values cut to fit their column -- see _fit
    refused = []             # rows the database would not take at all

    for row in rows:
        code = _pick(row, 'Code', 'EmpCode', 'EmployeeCode')
        # Official mailbox first. PersonalEmail is a deliberate fallback, not a
        # preference: it is the login identity, and a personal address that
        # reaches the person beats no account at all.
        email = _pick(row, 'Email', 'EmailId', 'OfficialEmail', 'EmailAddress',
                      'PersonalEmail').lower()
        if not code:
            continue
        # No address is no longer a reason to leave somebody out of the
        # directory. They are imported and counted; they simply cannot sign
        # in until HR fills the address in, at which point the next sync
        # finds them by code and adds it. Until then the column is NULL, not
        # '' -- see PortalUser.email for why that distinction matters.
        if not email:
            log.skipped_no_email += 1

        seen_codes.append(code)
        active = _is_active(row)

        # The fallback is only asked when there IS an address to match on.
        # Matching '' against a column that stores NULL finds nothing, so
        # this is a pointless query rather than a dangerous one -- but it
        # runs for every address-less person on every sync, and saying
        # plainly that the fallback needs an address is worth more than the
        # query it saves.
        existing = PortalUser.objects.filter(employee_code=code).first()
        if not existing and email:
            existing = PortalUser.objects.filter(email__iexact=email).first()

        # A leaver who never had a portal account does not get one now. The
        # feed carries every employee APIS has ever had - two thirds of the
        # rows are resignations - and importing them would bury the people who
        # actually work here under a directory of ex-staff. Anyone who DID have
        # an account falls through and is deactivated below, so no history is
        # lost. Checked before the email claim on purpose: 58 of these leavers
        # share an address with someone still employed, and letting them claim
        # it would lock the live person out of the portal entirely.
        if not existing and not active:
            log.skipped_leavers += 1
            continue

        # Two people with the same address, both still here. Real in APIS's
        # data. Email is the unique login identity and the lookup above falls
        # back to matching on it, so without this the second row would quietly
        # take over the first person's account, employee code and all, and one
        # of them would lose their login with nothing saying so. First row
        # keeps it; the clash is named in the log so HR can fix it upstream.
        # Only a real address can be claimed. Keyed on '' instead, every
        # address-less person after the first would look like a duplicate of
        # the first and be skipped -- the very bug this block exists to stop,
        # inverted.
        if email:
            if claimed_by.get(email, code) != code:
                log.skipped_duplicate_email += 1
                clashes.append(f'{code} and {claimed_by[email]} both use {email}')
                continue
            claimed_by[email] = code

        fields = {
            'email': email or None,
            'name': _name_of(row) or code,
            'designation': _resolved(row, 'Designation'),
            'department': _resolved(row, 'Department'),
            'location': _resolved(row, 'Location'),
            'reporting_manager_code': _manager(row)[0],
            'reporting_manager_name': _manager(row)[1],
            'date_of_birth': _date(row, 'DateOfBirth'),
            'date_of_joining': _date(row, 'DateOfJoining'),
            'grade': _grade(row),
            'category': _resolved(row, 'Category'),
            'office_mobile': _pick(row, 'OfficeMobileNo'),
            'hrms_id': _pick(row, 'Id'),
            'is_active': active,
            'from_hrms': True,
            'last_synced_at': timezone.now(),
            # What upstream sent for the columns we asked for -- not the
            # whole row. See _scrub: the full record carries PAN, Aadhaar,
            # bank account, UAN, PF and ESI numbers and a password column,
            # and this field is written to the database and shown on a
            # console screen.
            'hrms_raw': _scrub(row, requested or EMPLOYEE_FIELDS),
        }

        for key in _TRIMMABLE:
            fields[key] = _fit(fields[key], key, code, trimmed)

        # Each row writes inside its own savepoint. Whatever upstream sends,
        # one unusable record must cost that one record and not the run: the
        # outer transaction is already broken the moment a write fails, so
        # without a savepoint here every row after it fails too and the
        # rollback takes the whole directory back with it.
        try:
            with transaction.atomic():
                if existing:
                    # A row typed into the console before HRMS knew about this
                    # person is adopted rather than duplicated.
                    existing.employee_code = code
                    for k, v in fields.items():
                        # An address we already hold is never replaced with
                        # "none". Somebody was typed into the console with a
                        # working address precisely BECAUSE Pocket HRMS has
                        # none for them -- overwriting it would take away the
                        # login that was created to work around the gap, and
                        # the sync would do it silently every night.
                        if k == 'email' and v is None and existing.email:
                            continue
                        setattr(existing, k, v)
                    # The founding account can never be deactivated by an
                    # upstream status change - that is how a company locks
                    # itself out.
                    if existing.is_bootstrap_superadmin:
                        existing.is_active = True
                        existing.is_superadmin = True
                    existing.save()
                    log.updated += 1
                else:
                    PortalUser.objects.create(employee_code=code,
                                              app_access=list(DEFAULT_APPS), **fields)
                    log.created += 1
        except DatabaseError as e:
            log.skipped_bad_row += 1
            refused.append(f'{code} ({email}): {e}')

    # Someone who has left stops appearing in the feed. Their access is closed
    # rather than their record deleted, so their history stays attributable.
    # Only ever applied to rows HRMS owns, and only on a full sync - a filtered
    # or incremental pull is not evidence that anyone left.
    if deactivate_missing and seen_codes and not modified_since and emp_status in ('ALL', ACTIVE_ONLY):
        gone = (PortalUser.objects
                .filter(from_hrms=True, is_active=True)
                .exclude(employee_code__in=seen_codes)
                .exclude(is_superadmin=True)
                .exclude(email__iexact=SUPERADMIN_BOOTSTRAP_EMAIL))
        # A short feed must not be read as a redundancy. Asking for Active
        # employees only means the list IS the staff list, so anyone absent
        # from it has left -- which is right, and is also exactly what a half
        # answer from upstream looks like. A bad page, a filter they change
        # again, a timeout mid-walk, and a few hundred people lose their
        # login at once. Past this share it stops and says so instead; a sync
        # that deactivates nobody is a morning's annoyance, one that
        # deactivates everybody is a day of the company locked out.
        standing = PortalUser.objects.filter(from_hrms=True, is_active=True).count()
        count = gone.count()
        if standing and count > standing * DEACTIVATION_CEILING:
            log.message_prefix = (
                f'Refused to deactivate {count} of {standing} people: that is more '
                f'than {int(DEACTIVATION_CEILING * 100)}% of the directory in one run, '
                f'which looks like a short answer from Pocket HRMS rather than that '
                f'many leavers. Nobody was deactivated. Check the feed, then re-run.')
        else:
            log.deactivated = count
            gone.update(is_active=False)

    log.ok, log.finished_at = True, timezone.now()
    prefix = getattr(log, 'message_prefix', '')
    log.message = (prefix + '\n\n') if prefix else ''
    log.message += (f'{log.created} created, {log.updated} updated, '
                   f'{log.deactivated} deactivated, {log.skipped_no_email} without an email '
                   f'(in the directory, cannot sign in), '
                   f'{log.skipped_leavers} skipped (already left).')
    if refused:
        log.message += ('\n\nThe database would not accept these rows, so '
                        'they were left as they were — needs fixing in Pocket '
                        'HRMS:\n  ' + '\n  '.join(refused[:20]))
        if len(refused) > 20:
            log.message += f'\n  ...and {len(refused) - 20} more.'
    if trimmed:
        log.message += ('\n\nLonger than the column allows, stored '
                        'shortened:\n  ' + '\n  '.join(trimmed[:20]))
        if len(trimmed) > 20:
            log.message += f'\n  ...and {len(trimmed) - 20} more.'
    if clashes:
        # Named, not just counted - "1 duplicate address" is not something
        # anyone can act on; two employee codes and the address is.
        log.message += ('\n\nShared email addresses, second record skipped — '
                        'needs fixing in Pocket HRMS:\n  ' + '\n  '.join(clashes[:20]))
        if len(clashes) > 20:
            log.message += f'\n  ...and {len(clashes) - 20} more.'
    log.save()
