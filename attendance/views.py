"""Attendance, read live from Pocket HRMS for a date or a date range.

  GET /api/attendance/meta/      what the tool can offer, and the filter lists
  GET /api/attendance/records/   the rows themselves

This is a separate tool from the Data Extractor on purpose. The Extractor's
two attendance dashboards read files somebody uploaded; this one reads the
HRMS directly, so it carries every employee's working hours for any day
without anybody exporting anything -- which is exactly why it is gated
separately (`AppKey.ATTENDANCE`) rather than riding on the Extractor's grant.

The feed itself has no location, department or manager on it, so those are
joined on from the portal's own directory (`accounts.PortalUser`) by employee
code. That join is what makes "Delhi HO only" possible at all.
"""
from datetime import date, datetime, timedelta

from accounts.auth import SessionRequiredAPIView, require_app
from accounts.models import AppKey, PortalUser
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from rest_framework.response import Response

from . import hrms_attendance as hrms

# A fetch is reused for this long. Attendance for a past day does not change;
# today's does, so the window is short enough that "Refresh" is rarely needed
# and long enough that five people opening the same day cost one request.
CACHE_TTL = 10 * 60

# Hard ceiling on what one response will carry. 31 days of the whole company
# is ~22,000 rows; past that the browser is the bottleneck, not the API.
MAX_ROWS = 40000


def _cutoff_mins():
    """The Delhi HO late rule, as minutes past midnight.

    Separate from HRMS's own `LateTime`, which is measured against each
    employee's shift. Both are reported: a shift-based lateness and a
    clock-based one answer different questions and legitimately disagree.
    """
    raw = str(getattr(settings, 'ATTENDANCE_LATE_CUTOFF', '11:00')).strip()
    mins = hrms.minutes(raw)
    return 11 * 60 if mins is None else mins


def _parse_date(value, fallback=None):
    text = str(value or '').strip()
    if not text:
        return fallback
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _directory():
    """`{employee_code: {...}}` for everyone in the portal's directory.

    Cached briefly: it is one query for ~700 rows and it is read on every
    attendance fetch, but it changes only when an HRMS sync runs.
    """
    cached = cache.get('attendance:directory')
    if cached is not None:
        return cached
    people = {}
    for u in PortalUser.objects.all().only(
            'employee_code', 'department', 'location', 'designation',
            'reporting_manager_name', 'is_active'):
        if not u.employee_code:
            continue
        people[u.employee_code.strip().upper()] = {
            'department': u.department or '',
            'location':   u.location or '',
            'manager':    u.reporting_manager_name or '',
            'onDirectory': True,
            'active':     bool(u.is_active),
        }
    cache.set('attendance:directory', people, 10 * 60)
    return people


def _home_office():
    """Who counts as head office, as a LIST of code prefixes.

    One prefix was not enough: NA0001-NA0007 appear in the attendance feed,
    have never been in the portal directory, and were silently dropped from
    the default view. `codePrefixes` is the list; `codePrefix` is kept
    alongside it so an older frontend bundle keeps working.
    """
    raw = str(getattr(settings, 'ATTENDANCE_HO_CODE_PREFIX', 'HO,NA'))
    prefixes = [p.strip().upper() for p in raw.split(',') if p.strip()]
    return {
        'label':        str(getattr(settings, 'ATTENDANCE_HO_LABEL', 'Delhi HO')),
        'codePrefixes': prefixes,
        'codePrefix':   prefixes[0] if prefixes else 'HO',
    }


def _norm_place(value):
    """Letters only, upper case -- for comparing a holiday's location with
    an employee's. The two lists are typed by different people: the holiday
    calendar says 'Delhi', 'UTTAR PRADESH' and 'Utrtar Pradesh' (sic), while
    the directory says 'DELHI-HO' and 'UTTAR PRADESH'."""
    return ''.join(ch for ch in str(value or '').upper() if ch.isalpha())


# A holiday row whose location is one of these applies to the whole company
# rather than to one state.
COMPANY_WIDE = {'COMPANY', 'ALL', 'ALLLOCATION', 'ALLLOCATIONS', 'HEADOFFICE'}

# Places the directory names by city while the holiday calendar names them by
# state. Containment cannot bridge these -- "ROORKEE" shares no letters with
# "UTTARAKHAND" -- and Roorkee alone is 88 people in the feed, so without
# this the plant reads as absent on every state holiday it actually took.
PLACE_ALIASES = {
    'ROORKEE': 'UTTARAKHAND',
    'NOIDA': 'UTTARPRADESH',
    'GURGAON': 'HARYANA',
    'GURUGRAM': 'HARYANA',
    'KOLKATA': 'WESTBENGAL',
    'HYDERABAD': 'TELANGANA',
    'BANGALORE': 'KARNATAKA',
    'BENGALURU': 'KARNATAKA',
    'CHENNAI': 'TAMILNADU',
    'MUMBAI': 'MAHARASHTRA',
    'PUNE': 'MAHARASHTRA',
    'AHMEDABAD': 'GUJARAT',
    'JAIPUR': 'RAJASTHAN',
    'LUCKNOW': 'UTTARPRADESH',
    'PATNA': 'BIHAR',
}


def _holiday_here(mark, location):
    """Does this holiday apply to somebody working at `location`?

    **Holidays are state-wise and must not be applied globally.** The 2026
    calendar has 08-11 across most of India, 11-11 in Assam and West Bengal
    only, and 09/10-11 across the north. Marking everybody "on holiday"
    because one state was would hide a day of real absence.

    An employee with no location on record only gets the company-wide ones:
    guessing a state for them would be inventing the answer.
    """
    if not mark:
        return False
    places = mark.get('locations') or []
    if any(_norm_place(p) in COMPANY_WIDE for p in places):
        return True
    mine = _norm_place(location)
    if not mine:
        return False
    # A city in the directory, a state in the calendar.
    mine_alias = PLACE_ALIASES.get(mine, '')
    for p in places:
        theirs = _norm_place(p)
        if not theirs:
            continue
        # Containment either way: 'DELHI-HO' holds 'DELHI'.
        if theirs in mine or mine in theirs:
            return True
        if mine_alias and (theirs in mine_alias or mine_alias in theirs):
            return True
    return False


def _classify(row, marks, today):
    """Turn a blank row into something a human can act on.

    The feed returns a row for every employee on every date and carries no
    leave, week-off or holiday column, so a blank row alone says nothing.
    Between the calendar, the weekday and the date we can still separate the
    four cases, and only one of them is worth anybody's attention:

      notYet   the date has not happened yet
      holiday  a company holiday AT THAT PERSON'S LOCATION
      weekOff  a Sunday
      absent   a working day with nothing recorded -- the one that matters

    `absent` is deliberately the fallback, and it is NOT proof of absence:
    an approved leave arrives looking exactly the same, because the feed has
    no column to tell them apart. Every screen that shows it has to say so.
    """
    iso = row.get('date') or ''
    if not iso:
        return
    mark = marks.get(iso)
    optional = bool(mark and mark.get('optional'))

    # Head office staff with no location on record still sit in Delhi. 29 of
    # the 83 HO-coded people have a blank location, and without this they
    # match no state holiday and fall through to "absent" -- 30 false
    # absences on every Delhi holiday.
    place = row.get('location') or ''
    if not place:
        prefixes = _home_office()['codePrefixes']
        if any(str(row.get('code', '')).upper().startswith(p) for p in prefixes):
            place = str(getattr(settings, 'ATTENDANCE_HO_LOCATION', 'Delhi'))
    here = _holiday_here(mark, place)

    extra = []
    if here:
        extra.append('optionalHoliday' if optional else 'holiday')
    try:
        if date.fromisoformat(iso).weekday() == 6:
            # Sunday is the week off; Saturday is a working day here.
            extra.append('sunday')
    except ValueError:
        pass

    if row.get('noRecord'):
        if iso > today.isoformat():
            extra.append('notYet')
        elif here and not optional:
            extra.append('holiday') if 'holiday' not in extra else None
        elif 'sunday' in extra:
            extra.append('weekOff')
        else:
            extra.append('absent')

    row['flags'] = row['flags'] + [f for f in extra if f and f not in row['flags']]


class MetaView(SessionRequiredAPIView):
    """What this tool can do, before anybody asks it for a day's data."""

    def get(self, request):
        user, err = require_app(request, AppKey.ATTENDANCE)
        if err:
            return err

        people = _directory()
        locations = sorted({p['location'] for p in people.values() if p['location']})
        departments = sorted({p['department'] for p in people.values() if p['department']})

        return Response({
            'ready':       hrms.is_configured(),
            'reason':      '' if hrms.is_configured() else
                           'Pocket HRMS is not connected. Ask IT to set the company token.',
            'today':       timezone.localdate().isoformat(),
            'lateCutoff':  str(getattr(settings, 'ATTENDANCE_LATE_CUTOFF', '11:00')),
            'maxSpanDays': hrms.MAX_SPAN_DAYS,
            'maxTotalDays': hrms.MAX_TOTAL_DAYS,
            'locations':   locations,
            'departments': departments,
            'directorySize': len(people),
            # Which employees count as head office. Matched on the employee
            # CODE rather than the directory's location column -- see the
            # setting's comment for the counts that forced that.
            'homeOffice': _home_office(),
            # Said out loud so no screen draws a column of dashes: this
            # tenant does not compute overtime at all.
            'overtimeTracked': False,
        })


class RecordsView(SessionRequiredAPIView):
    """The attendance rows for a date or a range.

        ?from=2026-11-01&to=2026-11-19   both inclusive; `to` defaults to `from`
        ?code=HO0014                     one employee
        ?refresh=1                       bypass the ten-minute cache
    """

    def get(self, request):
        user, err = require_app(request, AppKey.ATTENDANCE)
        if err:
            return err

        today = timezone.localdate()
        start = _parse_date(request.query_params.get('from'), today)
        if start is None:
            return Response({'error': 'That start date could not be read. Use YYYY-MM-DD.'},
                            status=400)
        end = _parse_date(request.query_params.get('to'), start)
        if end is None:
            return Response({'error': 'That end date could not be read. Use YYYY-MM-DD.'},
                            status=400)
        if end < start:
            return Response({'error': 'The end date is before the start date.'}, status=400)

        code = str(request.query_params.get('code') or '').strip()
        cutoff = _cutoff_mins()
        key = f'attendance:rows:{start}:{end}:{code.upper()}'

        rows = None if request.query_params.get('refresh') else cache.get(key)
        if rows is None:
            try:
                raw = hrms.fetch(start, end, code)
            except hrms.AttendanceError as e:
                # Their message is usually the useful one ("Date range cannot
                # exceed 31 days."), so it is passed through rather than
                # replaced with something vaguer of our own.
                return Response({'error': str(e)}, status=502)
            rows = hrms.normalise_all(raw, cutoff)
            cache.set(key, rows, CACHE_TTL)

        truncated = len(rows) > MAX_ROWS
        if truncated:
            rows = rows[:MAX_ROWS]

        # The holiday calendar is the only thing distinguishing "nothing
        # recorded" from "nobody was meant to be here". A failure to fetch it
        # is not a failure of the request -- the rows are still worth having.
        marks = {}
        try:
            for year in sorted({start.year, end.year}):
                marks.update(hrms.holidays(year))
        except hrms.AttendanceError:
            marks = {}

        people = _directory()
        for row in rows:
            who = people.get(row['code'].upper())
            row['department'] = who['department'] if who else ''
            row['location'] = who['location'] if who else ''
            row['manager'] = who['manager'] if who else ''
            # Somebody in the attendance feed but not in our directory --
            # a candidate code, a joiner the last sync has not seen, or one
            # of the NA-coded staff the directory has never held.
            row['offDirectory'] = who is None
            _classify(row, marks, today)

        return Response({
            'from':   start.isoformat(),
            'to':     end.isoformat(),
            'days':   (end - start).days + 1,
            'rows':   rows,
            'count':  len(rows),
            'truncated': truncated,
            'lateCutoff': str(getattr(settings, 'ATTENDANCE_LATE_CUTOFF', '11:00')),
            'holidays': marks,
            'overtimeTracked': False,
            'homeOffice': _home_office(),
            'fetchedAt': timezone.localtime().isoformat(timespec='seconds'),
        })
