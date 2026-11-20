"""Reading day-by-day attendance out of Pocket HRMS, live.

This is the GetAttendanceAPI endpoint, which is a different animal from the
employee master in accounts/services/hrms.py: it takes its dates as QUERY
parameters, not headers, and it needs no EmployeeFields header at all.

Everything below was confirmed against APIS's live tenant on 19-11-2026 by
fetching 01/11-10/11 (718 employees, 7,180 rows) and poking at the edges.
The vendor's document is right about the shape and wrong or silent about
most of what follows.

What the rows actually look like
--------------------------------
Thirteen fields, all strings:

    EmployeeCode EmployeeName Designation Grade Date
    InTime OutTime EarlyTime LateTime
    TotalWorkingHrs OTHours NormalWorkingHours NormalWorkingHoursWOHO

  * **Dates go in as `yyyy-MM-dd` and come back as `dd/MM/yyyy`.** The two
    formats are not interchangeable and nothing warns you.
  * **`-` is the empty value**, everywhere, for every field.
  * **`InTime` and `OutTime` can be the literal word `Unswiped`.** That is a
    punch that was never made, not a time -- 649 out-punches in ten days.
    Anything parsing these as times has to cope, and `TotalWorkingHrs` is
    `-` on those rows, so no duration can be derived from one.
  * `EarlyTime` and `LateTime` are DURATIONS -- how early, how late -- in
    HH:mm, measured against that employee's own shift. They are not clock
    times, and `LateTime: 00:25` means twenty-five minutes late.
  * `TotalWorkingHrs` is the raw span between the punches (seen up to 16:07).
    `NormalWorkingHours` is that capped at the shift length (never above
    08:00). They answer different questions; both are kept.
  * **`OTHours` is `-` on every single row.** This tenant does not compute
    overtime, so the column is reported as "not tracked" rather than drawn as
    a column of dashes.
  * `InTime` and `OutTime` are blank together or present together -- there
    were no half-blank rows in 7,180.
  * **A row exists for every employee on every date in the range**, including
    dates in the future, which answer 200 with a full set of empty rows. So a
    blank row means "nothing recorded", NOT "absent" -- the feed carries no
    leave, week-off or holiday marker to tell those apart. See `holidays()`.
  * **The range limit is 31 days inclusive, not the documented 30.**
    01/10 -> 01/11 is refused with `Date range cannot exceed 31 days.`;
    02/10 -> 01/11 (31 days) is fine. Longer spans are fetched as windows
    and stitched back together here.
  * There is no location, department or email on these rows, so "Delhi HO"
    cannot be asked of this API. It is resolved from the portal's own
    directory in views.py.
"""
import re
from datetime import date, datetime, timedelta

import requests
from django.conf import settings

ATTENDANCE_PATH = '/api/EmployeeMaster/GetAttendanceAPI'

# Verified by experiment, not read off the document, which says 30.
MAX_SPAN_DAYS = 31

# A window longer than this is refused outright rather than quietly firing
# dozens of requests at the vendor: 31 days is already ~22,000 rows and 6 MB.
MAX_TOTAL_DAYS = 186

# What the API writes where there is no value.
BLANK = {'-', '', 'none', 'null'}

# A punch that was never made. Reported as a word in a field that otherwise
# holds times -- see the module docstring.
UNSWIPED = 'unswiped'

_HHMM = re.compile(r'^(\d{1,3}):([0-5]\d)$')

TIMEOUT = 120


class AttendanceError(RuntimeError):
    """Anything that stopped us getting attendance out of Pocket HRMS."""


def is_configured():
    return bool(getattr(settings, 'POCKET_HRMS_TOKEN', ''))


def _blank(value):
    return str(value or '').strip().lower() in BLANK


def _unswiped(value):
    return str(value or '').strip().lower() == UNSWIPED


def minutes(value):
    """`'08:33'` -> 513. None for `-`, `Unswiped` and anything unparseable.

    Deliberately tolerant: this reads a third party's free-text column, and a
    value nobody anticipated should cost one cell, not the whole fetch.
    """
    text = str(value or '').strip()
    if _blank(text) or _unswiped(text):
        return None
    m = _HHMM.match(text)
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def clock(value):
    """The punch as it should be displayed: a time, or '' for a non-time."""
    text = str(value or '').strip()
    if _blank(text) or _unswiped(text):
        return ''
    return text if _HHMM.match(text) else ''


def _iso(value):
    """`'01/11/2026'` -> `'2026-11-01'`. '' if it is not a date we know."""
    text = str(value or '').strip()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return ''


def windows(start, end, span=MAX_SPAN_DAYS):
    """Split a range into chunks the API will accept, inclusive of both ends."""
    out, cursor = [], start
    while cursor <= end:
        last = min(cursor + timedelta(days=span - 1), end)
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def fetch_window(start, end, employee_code=''):
    """One request. `start`/`end` must already be inside the 31-day limit."""
    token = getattr(settings, 'POCKET_HRMS_TOKEN', '')
    if not token:
        raise AttendanceError(
            'No Pocket HRMS token configured. Set POCKET_HRMS_TOKEN in the '
            'environment (ask Pocket HRMS for the company token).')

    base = getattr(settings, 'POCKET_HRMS_BASE_URL', 'https://api.pockethrms.com').rstrip('/')
    # Going IN the dates are ISO; coming back they are dd/MM/yyyy.
    params = {'FromDate': start.isoformat(), 'ToDate': end.isoformat()}
    if employee_code:
        # Their spelling, with the lower-case 'c'. Confirmed working.
        params['Employeecode'] = employee_code

    try:
        r = requests.get(f'{base}{ATTENDANCE_PATH}',
                         headers={'authorization': token},
                         params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise AttendanceError(f'Could not reach Pocket HRMS: {e}') from e

    if r.status_code == 400:
        # They explain themselves properly on this endpoint, so pass it on.
        try:
            raise AttendanceError(str(r.json().get('message') or r.text)[:300])
        except ValueError:
            raise AttendanceError(r.text[:300] or 'Pocket HRMS rejected the dates.')
    if r.status_code == 500:
        # Same as the employee master: their 500 is what a bad token looks like.
        raise AttendanceError(
            'Pocket HRMS rejected the request (HTTP 500). This is what their API '
            'returns for a missing or invalid company token.')
    if r.status_code != 200:
        raise AttendanceError(f'Pocket HRMS returned HTTP {r.status_code}: {r.text[:300]}')

    try:
        body = r.json()
    except ValueError as e:
        raise AttendanceError(f'Pocket HRMS returned a non-JSON body: {r.text[:300]}') from e
    if isinstance(body, dict):
        raise AttendanceError(str(body.get('message') or body)[:300])
    if not isinstance(body, list):
        raise AttendanceError(f'Expected a JSON array, got {type(body).__name__}.')
    return body


def fetch(start, end, employee_code=''):
    """Every raw row between two dates, stitching the 31-day windows together."""
    if end < start:
        raise AttendanceError('The end date is before the start date.')
    span = (end - start).days + 1
    if span > MAX_TOTAL_DAYS:
        raise AttendanceError(
            f'That is {span} days. Ask for {MAX_TOTAL_DAYS} days or fewer — '
            'a longer range is tens of thousands of rows and several minutes '
            'of waiting.')
    rows = []
    for a, b in windows(start, end):
        rows.extend(fetch_window(a, b, employee_code))
    return rows


def normalise(raw, late_cutoff_mins=None):
    """One API row -> the shape every screen and every count reads.

    Everything derived about a row is derived exactly once, here, so a figure
    on a card, in the table and in a download cannot disagree with each other.
    """
    in_raw = str(raw.get('InTime') or '').strip()
    out_raw = str(raw.get('OutTime') or '').strip()

    in_unswiped, out_unswiped = _unswiped(in_raw), _unswiped(out_raw)
    in_time, out_time = clock(in_raw), clock(out_raw)

    # Blank in AND out is the scaffold row the API returns for every employee
    # on every date. It means nothing was recorded -- not that they were absent.
    no_record = _blank(in_raw) and _blank(out_raw)

    in_mins = minutes(in_raw)
    worked = minutes(raw.get('TotalWorkingHrs'))
    normal = minutes(raw.get('NormalWorkingHours'))

    row = {
        'code':        str(raw.get('EmployeeCode') or '').strip(),
        'name':        ' '.join(str(raw.get('EmployeeName') or '').split()),
        'designation': '' if _blank(raw.get('Designation')) else str(raw.get('Designation')).strip(),
        'grade':       '' if _blank(raw.get('Grade')) else str(raw.get('Grade')).strip(),
        'date':        _iso(raw.get('Date')),

        'inTime':   in_time,
        'outTime':  out_time,
        'inUnswiped':  in_unswiped,
        'outUnswiped': out_unswiped,

        # Durations, in minutes. HRMS measures these against the employee's
        # OWN shift, which is why they can disagree with the cutoff below.
        'earlyMins': minutes(raw.get('EarlyTime')),
        'lateMins':  minutes(raw.get('LateTime')),
        'workedMins': worked,
        'normalMins': normal,

        'noRecord': no_record,
    }

    flags = []
    if no_record:
        flags.append('noRecord')
    else:
        if in_time or out_time or in_unswiped or out_unswiped:
            flags.append('present')
        if in_unswiped or out_unswiped:
            # The day never closed. No duration can be trusted from it, which
            # is why workedMins is left as None rather than guessed at.
            flags.append('unswiped')
        if row['lateMins']:
            flags.append('late')
        if row['earlyMins']:
            flags.append('earlyOut')
        # The company's own Delhi HO rule, which is about the clock rather
        # than about anybody's shift. Strict `>`: 11:00 sharp is on time.
        if late_cutoff_mins is not None and in_mins is not None and in_mins > late_cutoff_mins:
            flags.append('afterCutoff')

    row['flags'] = flags
    return row


def normalise_all(rows, late_cutoff_mins=None):
    return [normalise(r, late_cutoff_mins) for r in rows]


# ── Holidays ────────────────────────────────────────────────────────────────
# The attendance feed carries no leave, week-off or holiday marker, so a blank
# row cannot be told apart from an absence by itself. The holiday calendar is
# the one piece of that puzzle the API will give us.

HOLIDAY_PATH = '/api/EmployeeMaster/GetHolidays'


def holidays(year=None):
    """`{iso date: {'optional': bool, 'locations': [...]}}` for one year.

    Grouped by a "location" whose meaning depends on how the company set
    holidays up -- it may be the word Company, a branch, or an employee code.
    They are collected per date rather than filtered, because which grouping
    this tenant uses is not something to hard-code.
    """
    token = getattr(settings, 'POCKET_HRMS_TOKEN', '')
    if not token:
        raise AttendanceError('No Pocket HRMS token configured.')
    base = getattr(settings, 'POCKET_HRMS_BASE_URL', 'https://api.pockethrms.com').rstrip('/')
    params = {'year': year} if year else {}
    try:
        r = requests.get(f'{base}{HOLIDAY_PATH}', headers={'authorization': token},
                         params=params, timeout=60)
    except requests.RequestException as e:
        raise AttendanceError(f'Could not reach Pocket HRMS: {e}') from e
    if r.status_code != 200:
        raise AttendanceError(f'Pocket HRMS returned HTTP {r.status_code} for holidays.')
    try:
        body = r.json()
    except ValueError as e:
        raise AttendanceError('Pocket HRMS returned a non-JSON holiday list.') from e
    if not isinstance(body, list):
        return {}

    out = {}
    for item in body:
        if not isinstance(item, dict):
            continue
        iso = _iso(str(item.get('holidayDate') or '')[:10])
        if not iso:
            continue
        entry = out.setdefault(iso, {'optional': True, 'locations': []})
        # A date is only "optional" if EVERY entry for it is optional: one
        # compulsory listing makes the day a holiday proper.
        if not item.get('isOptional'):
            entry['optional'] = False
        loc = str(item.get('location') or '').strip()
        if loc and loc not in entry['locations']:
            entry['locations'].append(loc)
    return out
