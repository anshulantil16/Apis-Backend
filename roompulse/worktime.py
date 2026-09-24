"""How long a job took, and how much of it was outside office hours.

Kept apart from the views because three places need the same answer -- logging
a job, closing a ticket, and the monthly report -- and a rule about what counts
as "after hours" that lives in a view is a rule that holds only for the screens
that remembered to call it.
"""
from datetime import datetime, time, timedelta

# The normal working day. Anything outside it, and anything at all on a
# Saturday or Sunday, is time somebody gave up that they did not owe.
OFFICE_START = time(9, 30)
OFFICE_END = time(18, 30)

DAY = 24 * 60


def _mins(t):
    return t.hour * 60 + t.minute


def duration_minutes(start, end):
    """Minutes between two clock times, or None.

    An end earlier than the start means the work ran past midnight -- which
    is exactly the case this exists to measure, so it is read as the next day
    rather than as a mistake.
    """
    if not start or not end:
        return None
    span = _mins(end) - _mins(start)
    if span < 0:
        span += DAY
    return span or None


def after_hours_minutes(on_date, start, end):
    """How much of a worked window fell outside office hours.

    Computed from the window rather than flagged by the person, because "I
    worked late" is a claim and 22:00-01:30 is a fact. Returns None when
    there is no window to measure -- which is not the same as zero.
    """
    if not start or not end or not on_date:
        return None

    total = duration_minutes(start, end)
    if not total:
        return None

    # A weekend day is entirely outside office hours; there are no office
    # hours on a Saturday.
    if on_date.weekday() >= 5:
        return total

    begin = _mins(start)
    finish = begin + total                      # may run past 24:00
    office_open, office_close = _mins(OFFICE_START), _mins(OFFICE_END)

    inside = 0
    # The window can cross midnight into the next day, whose office hours
    # count too unless that next day is a weekend.
    for day_offset in (0, 1):
        day = on_date + timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        lo = office_open + day_offset * DAY
        hi = office_close + day_offset * DAY
        inside += max(0, min(finish, hi) - max(begin, lo))

    return max(0, total - inside)


def resolve_time(on_date, raw_from, raw_to, raw_minutes):
    """-> (minutes, worked_from, worked_to, after_hours) or an error string.

    Minutes can be typed directly, or derived from the window. When both are
    given the window wins for the after-hours figure but the typed number is
    kept as the duration: people round, and a job with a break in the middle
    is honestly two hours of work inside a four-hour window.
    """
    def parse(raw):
        raw = str(raw or '').strip()
        if not raw:
            return None, None
        for fmt in ('%H:%M', '%H:%M:%S'):
            try:
                return datetime.strptime(raw, fmt).time(), None
            except ValueError:
                continue
        return None, f'Give times as HH:MM — "{raw}" is not one.'

    worked_from, err = parse(raw_from)
    if err:
        return err
    worked_to, err = parse(raw_to)
    if err:
        return err
    if bool(worked_from) != bool(worked_to):
        return 'Give both a start and an end time, or neither.'

    minutes = None
    if raw_minutes not in (None, ''):
        try:
            minutes = max(0, min(DAY, int(raw_minutes)))
        except (TypeError, ValueError):
            return 'Time spent must be a number of minutes.'
    if minutes is None:
        minutes = duration_minutes(worked_from, worked_to)

    return minutes, worked_from, worked_to, after_hours_minutes(on_date, worked_from, worked_to)
