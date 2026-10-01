"""Live room-status engine.

Pure functions, no view/request coupling, so they can be unit-tested and
reused wherever a room's current state is needed (grid, calendar, exports).
"""
from datetime import datetime, timedelta

from django.db import models
from django.utils import timezone

UPCOMING_WINDOW_MIN = 60  # a room shows "Upcoming" once its next booking starts within this


def effective_end(b):
    """When the room actually became free — the booked end, or the moment it
    was released if the meeting finished early.

    Everything that asks "is this room in use?" goes through here, so a
    release frees the room both on the live grid and for anyone trying to
    book the remainder of the slot. The booked end_time is left alone: a
    meeting that ran 10:00-10:30 of a 10:00-11:00 booking still happened.
    """
    released = getattr(b, 'released_at', None)
    if not released:
        return b.end_time
    # released_at is a moment; only the part of it on the booking's own day
    # can shorten it, and it can never extend past what was booked.
    if released.date() != b.date:
        return b.end_time
    return min(b.end_time, released.time())


def room_status(room, bookings, now=None):
    """`bookings` = approved BookingRequest rows for this room, ANY date (the
    caller decides the query window). Returns a dict describing right now.

    Deliberately takes plain data rather than querying inside — the caller
    already has the approved bookings loaded for a whole room list in one
    query, so this stays a pure, fast, testable function instead of doing
    N+1 lookups per room.
    """
    now = now or timezone.localtime().replace(tzinfo=None)
    today = now.date()

    current = None
    upcoming = None
    for b in bookings:
        if b.date != today:
            continue
        start = datetime.combine(b.date, b.start_time)
        end = datetime.combine(b.date, effective_end(b))
        if start <= now < end:
            current = b
            break  # a room can only be in exactly one approved meeting at once
        if start > now:
            if upcoming is None or start < datetime.combine(upcoming.date, upcoming.start_time):
                upcoming = b

    if current is not None:
        return {
            'status': 'occupied',
            'until': current.end_time.strftime('%H:%M'),
            'current_booking': _brief(current),
            'next_booking': _brief(upcoming) if upcoming else None,
        }

    if upcoming is not None:
        start_dt = datetime.combine(upcoming.date, upcoming.start_time)
        minutes_away = (start_dt - now).total_seconds() / 60
        if minutes_away <= UPCOMING_WINDOW_MIN:
            return {
                'status': 'upcoming',
                'starts_in_min': round(minutes_away),
                'current_booking': None,
                'next_booking': _brief(upcoming),
            }
        return {
            'status': 'free',
            'current_booking': None,
            'next_booking': _brief(upcoming),
        }

    return {'status': 'free', 'current_booking': None, 'next_booking': None}


def _brief(b):
    return {
        'id': b.id,
        'requested_by_name': b.requested_by_name,
        'department': b.department,
        'purpose': b.purpose,
        'purpose_detail': b.purpose_detail,
        'start_time': b.start_time.strftime('%H:%M'),
        'end_time': b.end_time.strftime('%H:%M'),
        'attendees': b.attendees,
    }


def overlaps(a_start, a_end, b_start, b_end):
    """True if two [start, end) time ranges on the same date intersect."""
    return a_start < b_end and b_start < a_end


def find_conflicts(qs, date, start_time, end_time, exclude_id=None):
    """Approved bookings for the same room/date that overlap the given
    window. `qs` should already be filtered to one room and status='approved'."""
    qs = qs.filter(date=date)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return [b for b in qs if overlaps(start_time, end_time, b.start_time, effective_end(b))]


def expire_stale_bookings():
    """Close off requests whose slot came and went unanswered.

    A pending booking for 11:00-12:00 still reading "waiting for approval" at
    half past two is telling the requester something untrue: nobody is going
    to approve it, and approving it would grant a room for a meeting that has
    already not happened.

    Swept on read rather than by a cron, because the answer has to be right
    the moment somebody looks, and a cron that has not run yet is the same
    bug with a delay. The write only happens when there is something to
    write, so the usual page costs one extra SELECT and no UPDATE.

    -> how many were expired.
    """
    from .models import BookingRequest

    now = timezone.localtime()
    stale = BookingRequest.objects.filter(status='pending').filter(
        models.Q(date__lt=now.date())
        | models.Q(date=now.date(), end_time__lte=now.time()))
    if not stale.exists():
        return 0
    return stale.update(status='expired', updated_at=timezone.now())
