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
    blank row means "nothing recorded", NOT "absent" -- THIS feed carries no
    leave, week-off or holiday marker to tell those apart. The day-detail
    endpoint further down does, and is merged on by (code, date).
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

# Minutes are matched as one OR two digits because the feed really does emit
# both: four cells in a 9,732-row sample came back as `18:0` rather than
# `18:00`. A strict two-digit pattern drops those silently -- the punch is
# simply gone from the day -- so the minute is range-checked below instead.
_HHMM = re.compile(r'^(\d{1,3}):(\d{1,2})$')

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
    mins = int(m.group(2))
    if mins > 59:
        return None
    return int(m.group(1)) * 60 + mins


def clock(value):
    """The punch as it should be displayed: a time, or '' for a non-time.

    Zero-padded, because the feed emits both `18:00` and `18:0` for the same
    instant and a column showing each spelling reads as two different times.
    """
    text = str(value or '').strip()
    if _blank(text) or _unswiped(text):
        return ''
    m = _HHMM.match(text)
    if not m or int(m.group(2)) > 59:
        return ''
    return f'{int(m.group(1)):02d}:{int(m.group(2)):02d}'


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




# ── The day-detail feed (GetAttendance, the "legacy" endpoint) ──────────────
#
# A SECOND endpoint, fetched alongside the one above and merged onto it by
# (employee code, date). Neither is a superset of the other, which is the
# whole reason both are read:
#
#   GetAttendanceAPI  name, designation, grade, EarlyTime, LateTime,
#                     NormalWorkingHours  -- and no idea what the day WAS
#   GetAttendance     Status, ApplyType, shift, remarks, leave balance,
#                     reporting manager, check-in place -- and no name,
#                     designation, grade or lateness
#
# What this buys, and it is the big one: **`Status` ends the guessing.** Until
# now a blank row was sorted into absent / week off / holiday here, from the
# weekday and a state-wise holiday calendar, with a table of city-to-state
# aliases propping it up. HRMS already knows, per employee, and says so.
#
# Confirmed against the live tenant on 26-11-2026 over 9,732 rows (20-25/11
# and 05-12/11, the second window chosen because it holds three different
# holidays):
#
#   * **`Status` is a SET, slash-joined** -- `Present/Week Off/Holiday` is one
#     value, not three. Exactly five tokens appear: Present, Absent, Holiday,
#     Week Off, Punch Invalid. Parse it, never compare it whole.
#   * **`Punch Invalid` is the same thing as `Unswiped`** on the other feed:
#     872 rows, all of them an in-punch with `OutTime: Unswiped`. The day
#     never closed.
#   * **Holidays are already resolved per employee.** 09/11 was a holiday for
#     482 people and a working day for 102; 11/11 (Assam and West Bengal only)
#     for 36. That is the state-wise split `_holiday_here()` has been
#     approximating -- from HRMS itself, for each person, with no alias table.
#   * **`Status` NEVER says "Leave"**, whatever the vendor's own summary says.
#     Somebody on approved full-day leave reads `Absent`, and the ONLY thing
#     marking it as leave is `ApplyType`. An absence with an application
#     against it is explained; one without is not, and they must not be
#     counted together.
#   * **`ApplyType` is nearly always empty** -- 107 of 9,732 rows. It records
#     what was applied for THROUGH Pocket HRMS, which is a small fraction of
#     the leave actually taken, so it narrows the unexplained absences by a
#     few percent and does not come close to emptying them. The warning on
#     the screen stays.
#   * `-FD` / `-FH` / `-SH` are full day, first half and second half, and the
#     punches prove it: a `PL-FH` arrives at 12:39, a `PL-SH` leaves at 14:20.
#   * **`CheckInLocation` is `2-2-2` on 4,669 rows.** That is a sentinel from
#     the punch device, not a place. Treated as blank.
#   * **The range limit is 31 days here too**, not the documented 30, and it
#     refuses with the same message. Same windowing as above.
#   * `(EmployeeCode, AttendanceDate)` is unique -- no duplicate pairs in
#     9,732 rows -- so it is a safe merge key.
#   * `LeaveBalance` goes NEGATIVE (112 rows, down to -17.0). It is a running
#     balance that can be overdrawn, not a count of days left.

DETAIL_PATH = '/api/EmployeeMaster/GetAttendance'

# The five tokens `Status` is built from. Anything outside this set is kept
# and shown raw rather than dropped: a vocabulary that grew once can grow
# again, and a status nobody has seen before is worth noticing, not hiding.
STATUS_SLUGS = {
    'present':       'present',
    'absent':        'absent',
    'holiday':       'holiday',
    'week off':      'weekOff',
    'weekoff':       'weekOff',
    'punch invalid': 'punchInvalid',
}

# What an ApplyType prefix means. PL/CL/SL/COFF/OD are standard and confirmed
# by the rows themselves; LD and AR are NOT confirmed -- they are rare, their
# expansions are a guess, and a wrong label on somebody's leave record is
# worse than no label, so they fall through to the raw code until HR says.
APPLY_LABELS = {
    'PL':   'Privilege leave',
    'CL':   'Casual leave',
    'SL':   'Sick leave',
    'COFF': 'Compensatory off',
    'OD':   'On duty',
}

# The half-day suffix. Proven by the punches, not read off the document.
APPLY_SPANS = {
    'FD': ('full', 'Full day'),
    'FH': ('firstHalf', 'First half'),
    'SH': ('secondHalf', 'Second half'),
}

# An ApplyType that is not an absence from work. On duty means out on company
# business and regularisation is a correction to a punch -- neither is leave,
# and counting them as such would turn a working day into a day off.
AT_WORK_APPLY = {'OD', 'AR'}

# The punch device writes this where a place should be.
PLACE_SENTINEL = {'2-2-2'}


def status_tokens(value):
    """`'Present/Week Off/Holiday'` -> `['present', 'weekOff', 'holiday']`.

    Unknown tokens come back slugified rather than dropped, so a sixth
    status appearing one day shows up on screen instead of vanishing.
    """
    out = []
    for part in str(value or '').split('/'):
        part = ' '.join(part.split())
        # `-` is this API's empty value everywhere, and it is also the one
        # input that leaves nothing behind once punctuation is stripped --
        # which crashed the parser, and with it the whole fetch.
        if not part or _blank(part):
            continue
        slug = STATUS_SLUGS.get(part.lower())
        if not slug:
            # Unknown: camel-case it so it is at least a usable flag name.
            bits = part.replace('-', ' ').split()
            if not bits:
                continue
            slug = bits[0].lower() + ''.join(b.capitalize() for b in bits[1:])
        if slug not in out:
            out.append(slug)
    return out


def parse_apply(value):
    """`'PL-FH'` -> code, label, span slug, span label. `'OD - FD'` too.

    Returns `('', '', '', '')` for the blank that 99% of rows carry.
    """
    text = ' '.join(str(value or '').split())
    if _blank(text):
        return '', '', '', ''
    code, _, span = text.partition('-')
    code = code.strip().upper()
    span = span.strip().upper()
    label = APPLY_LABELS.get(code, code)
    span_slug, span_label = APPLY_SPANS.get(span, ('', ''))
    return code, label, span_slug, span_label


def place(value):
    """A check-in address, or '' for the blank and for the device sentinel."""
    text = ' '.join(str(value or '').split()).rstrip(',')
    if _blank(text) or text in PLACE_SENTINEL:
        return ''
    return text


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_detail_window(start, end, employee_code=''):
    """One request to the day-detail endpoint, inside its 31-day limit."""
    token = getattr(settings, 'POCKET_HRMS_TOKEN', '')
    if not token:
        raise AttendanceError('No Pocket HRMS token configured.')

    base = getattr(settings, 'POCKET_HRMS_BASE_URL', 'https://api.pockethrms.com').rstrip('/')
    params = {'FromDate': start.isoformat(), 'ToDate': end.isoformat()}
    if employee_code:
        params['Employeecode'] = employee_code

    try:
        r = requests.get(f'{base}{DETAIL_PATH}', headers={'authorization': token},
                         params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise AttendanceError(f'Could not reach Pocket HRMS: {e}') from e

    if r.status_code == 400:
        try:
            raise AttendanceError(str(r.json().get('message') or r.text)[:300])
        except ValueError:
            raise AttendanceError(r.text[:300] or 'Pocket HRMS rejected the dates.')
    if r.status_code == 500:
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


def fetch_detail(start, end, employee_code=''):
    """Every day-detail row between two dates, windows stitched together."""
    if end < start:
        raise AttendanceError('The end date is before the start date.')
    rows = []
    for a, b in windows(start, end):
        rows.extend(fetch_detail_window(a, b, employee_code))
    return rows


def index_detail(raw_rows):
    """`{(CODE, iso date): parsed detail}` -- the merge key, proven unique."""
    out = {}
    for raw in raw_rows:
        code = str(raw.get('EmployeeCode') or '').strip().upper()
        iso = _iso(raw.get('AttendanceDate'))
        if not code or not iso:
            continue
        out[(code, iso)] = parse_detail(raw)
    return out


def parse_detail(raw):
    """One day-detail row -> the fields a screen reads. See the notes above."""
    apply_code, apply_label, span_slug, span_label = parse_apply(raw.get('ApplyType'))
    status_raw = ' '.join(str(raw.get('Status') or '').split())

    return {
        'status':       '' if _blank(status_raw) else status_raw,
        'statusTokens': status_tokens(status_raw),

        'shift':     '' if _blank(raw.get('PlannedShift')) else
                     ' '.join(str(raw.get('PlannedShift')).split()),
        'shiftTime': '' if _blank(raw.get('PlannedShiftTime')) else
                     ' '.join(str(raw.get('PlannedShiftTime')).split()),

        'applyCode':      apply_code,
        'applyLabel':     apply_label,
        'applySpan':      span_slug,
        'applySpanLabel': span_label,
        'applyRemarks':   '' if _blank(raw.get('Remarks')) else
                          ' '.join(str(raw.get('Remarks')).split()),
        # An application HRMS has recorded an acceptance against. Absence of
        # a date is not a rejection -- it is "nothing recorded here".
        'applyApproved':  not _blank(raw.get('AcceptedDate')),
        'applyApprover':  '' if _blank(raw.get('AcceptedManagerName')) else
                          ' '.join(str(raw.get('AcceptedManagerName')).split()),

        # HRMS's own reporting manager, which is NOT the one the portal
        # directory holds. Both are carried; they disagree for some people
        # and neither of us gets to declare the other wrong.
        'hrmsManager':     '' if _blank(raw.get('ReportingManagerName')) else
                           ' '.join(str(raw.get('ReportingManagerName')).split()),
        'hrmsManagerCode': '' if _blank(raw.get('ReportingManagerCode')) else
                           str(raw.get('ReportingManagerCode')).strip().upper(),

        'checkInPlace':  place(raw.get('CheckInLocation')),
        'checkOutPlace': place(raw.get('CheckOutLocation')),

        # A running balance that can be overdrawn, so None and 0.0 and -1.5
        # are three different things and none of them is "no data".
        'leaveBalance': _number(raw.get('LeaveBalance')),
        'countedDay':   _number(raw.get('TotalWorkingDays')) == 1,
    }


def merge_detail(row, detail):
    """Fold a parsed detail row into a normalised row, in place.

    `detail` is None when the second feed had nothing for that person-day --
    a failed fetch, or a code one endpoint knows and the other does not. The
    row keeps working; `statusSource` says which way it was decided.
    """
    if not detail:
        row['statusSource'] = 'derived'
        return row

    row.update(detail)
    row['statusSource'] = 'hrms' if detail['statusTokens'] else 'derived'

    tokens = detail['statusTokens']
    flags = list(row['flags'])

    def add(flag):
        if flag not in flags:
            flags.append(flag)

    # HRMS's own verdict on the day. These are the same flag names the
    # calendar guess produces, so every screen reading them carries on
    # working -- what changes is that they are now facts, not inferences.
    for token in tokens:
        if token == 'punchInvalid':
            add('unswiped')
        elif token in ('present', 'absent', 'holiday', 'weekOff'):
            add(token)
        else:
            add(token)

    if detail['applyCode']:
        add('applied')
        if detail['applyCode'] in AT_WORK_APPLY:
            # On duty and a regularisation are working days, not days off.
            add('onDuty' if detail['applyCode'] == 'OD' else 'regularised')
        else:
            add('leave')
            if detail['applySpan'] in ('firstHalf', 'secondHalf'):
                add('halfDay')

    # The distinction the absence report lives or dies on. An absence with a
    # leave application against it is accounted for; one without is the only
    # kind worth anybody's morning. Note this is still not proof -- most
    # leave at this company is not applied for in Pocket HRMS at all.
    if 'absent' in flags and 'leave' not in flags:
        add('absentUnexplained')

    # Somebody who worked a week off or a holiday. Invisible before: the day
    # was filed under "nobody was meant to be here" and the punch with it.
    if 'present' in flags and ('weekOff' in flags or 'holiday' in flags):
        add('workedOnOff')

    row['flags'] = flags
    return row


# ── Holidays ────────────────────────────────────────────────────────────────
# Kept, but demoted. The day-detail feed above now says, per employee, whether
# a day was a holiday for THEM, which is better than anything derivable here.
# This calendar remains for two jobs the feed cannot do: naming the dates for
# a screen that wants to show them, and standing in as the old guess when the
# detail fetch fails. See `_classify()` in views.py for that fallback.

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
