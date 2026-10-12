"""Run rate: the month's target expressed as a daily number.

The sheet reports a month as two figures, a target and what has been billed,
and leaves the reader to do the division. That division is the question
everybody actually asks -- "am I going fast enough?" -- and it has three
different answers that are easy to confuse:

* what was needed each day from the first of the month (target / days),
* what has been managed each day so far (billed / days gone),
* **what is needed each day from today** (still to bill / days left).

Only the third one can be acted on, and it is the one nobody computes,
because it moves every day and the sheet never prints it. A head who is
behind on the first week sees a required rate barely above plan; the same
head in the last week sees one they cannot possibly hit. That is the point:
it says when a month has stopped being recoverable, while there is still
time to say so.

Divided again by the field force, it answers the other question a head asks:
with 25 officers, what does each of them have to bill a day.

**Days are calendar days, not working days.** The sheet carries no holiday
calendar and no week-off pattern, so anything else would be a guess dressed
up as arithmetic. A six-day selling week makes the real daily requirement
roughly a sixth higher than the figure here, so this is the floor rather
than the whole truth -- and every number stays comparable month to month,
which a guessed working calendar would not.
"""
from calendar import monthrange


def _per(value, n, places=2):
    """-> value / n, or None when n is nought. Never raises, never infinite."""
    try:
        n = float(n or 0)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return round(float(value or 0) / n, places)


def compute(as_of_date, target, billed, backlog, yesterday=None, sfo_count=0):
    """-> the daily picture of one month, for one head or for a group.

    Every figure is in the unit it was given in; nothing is scaled here.
    Returns None when the day is unknown, because every number below is a
    division by a count of days and inventing the day would invent the
    answer along with it.
    """
    if not as_of_date:
        return None

    days_in_month = monthrange(as_of_date.year, as_of_date.month)[1]
    # The sheet closes on its own date: "till 08th Oct" covers the 1st to the
    # 8th, which is eight days of selling, not seven.
    done = min(max(as_of_date.day, 1), days_in_month)
    left = days_in_month - done

    target = float(target or 0)
    billed = float(billed or 0)
    backlog = float(backlog or 0)

    plan_per_day = _per(target, days_in_month)
    actual_per_day = _per(billed, done)
    # Nothing is owed when the month is already past plan, and a negative
    # requirement is not a smaller number -- it is a different statement.
    required_per_day = _per(backlog, left) if backlog > 0 and left > 0 else None

    # Where the month lands if the rest of it runs at the rate so far. Not a
    # forecast of the business, which knows about months ending heavy -- it
    # is what today's pace is worth if nothing changes, which is the thing
    # worth arguing with.
    projected = billed + (actual_per_day or 0) * left
    on_track = None
    if required_per_day is not None and actual_per_day:
        on_track = round(actual_per_day / required_per_day * 100, 1)

    yest = float(yesterday or 0)
    yesterday_vs_required = None
    if required_per_day and yest > 0:
        yesterday_vs_required = round(yest / required_per_day * 100, 1)

    return {
        # Carried through so the report can tell the two zeroes apart: a
        # month with nothing left to bill, and a month with no days left to
        # bill it in. They print the same figure and mean opposite things.
        'still_to_bill': round(backlog, 2),
        'days_in_month': days_in_month,
        'days_done': done,
        'days_left': left,

        'plan_per_day': plan_per_day,
        'actual_per_day': actual_per_day,
        'required_per_day': required_per_day,

        # The same three divided by the field force. "With 25 officers, each
        # of them has to bill this much a day" is the form a head can hand
        # to the team on a morning call.
        'sfo_count': sfo_count or 0,
        'plan_per_day_per_sfo': _per(plan_per_day, sfo_count),
        'actual_per_day_per_sfo': _per(actual_per_day, sfo_count),
        'required_per_day_per_sfo': _per(required_per_day, sfo_count)
                                    if required_per_day is not None else None,

        # How much harder the rest of the month has to be run than the days
        # so far. 1.0 is "carry on"; 2.5 is "this will not happen by itself".
        'lift_needed': (round(required_per_day / actual_per_day, 2)
                        if required_per_day and actual_per_day else None),
        'on_track_pct': on_track,

        'projected_close': round(projected, 2),
        'projected_pct': (round(projected / target * 100, 1) if target else None),
        'projected_gap': round(target - projected, 2) if target else None,

        'yesterday_vs_required_pct': yesterday_vs_required,
        'month_is_closed': left <= 0,
    }


# -- saying which day, rather than "yesterday" -----------------------------
#
# These reports are sent every morning and then kept. "Yesterday's billing"
# is clear for about a day; in a thread of fifteen of them, or opened a week
# later, it says nothing at all. Every relative word the report used to use
# has a real date behind it, and the sheet's own closing date supplies it.

MON = ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
       'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec')


def day_label(d):
    """-> "08 Oct 2026"."""
    return f'{d.day:02d} {MON[d.month - 1]} {d.year}' if d else ''


def short_day(d):
    """-> "08 Oct", for inside a sentence that already names the year."""
    return f'{d.day:02d} {MON[d.month - 1]}' if d else ''


def span_label(d):
    """-> "1-8 Oct 2026": the days this month's figures actually cover."""
    if not d:
        return ''
    return (f'1 {MON[d.month - 1]}' if d.day == 1
            else f'1–{d.day} {MON[d.month - 1]}') + f' {d.year}'


def last_month_span(d):
    """-> "1-8 Sep 2026", the same days of the month before.

    What LMTD means, said out loud. The comparison is only fair because it
    stops on the same day, and nothing on the sheet says so.
    """
    if not d:
        return ''
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    last = monthrange(y, m)[1]
    day = min(d.day, last)
    return (f'1 {MON[m - 1]}' if day == 1
            else f'1–{day} {MON[m - 1]}') + f' {y}'


def year_span(d):
    """-> "1 Apr - 8 Oct 2026". The financial year opens in April."""
    if not d:
        return ''
    start_year = d.year if d.month >= 4 else d.year - 1
    return f'1 Apr {start_year} – {d.day} {MON[d.month - 1]} {d.year}'
