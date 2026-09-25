"""A month's work, from every queue that produces it.

The monthly report began as an IT report, so it read SupportTicket and
nothing else. That made an Admin's month look empty: their queues are
BookingRequest and ResourceRequest, and neither was counted. An admin who
approved forty room bookings and handed out thirty items showed as having
done whatever they had also remembered to type into "Log work you did" --
which is the same understatement the logged-work feature was built to fix,
one table over.

So the month is collected here, once, from all three, and flattened to one
shape. The view aggregates it and the workbook prints it, which is the point:
a spreadsheet that disagrees with the screen it was exported from is worse
than no spreadsheet.

What counts as a job done, and why:

- a ticket, on the day it was performed -- unchanged;
- an item request, on the day it was FULFILLED, not approved. Approving only
  says "yes, get them one"; the work is handing it over, and the model keeps
  those as separate steps precisely because they are;
- a room booking, on the day it was approved -- here approving IS the
  outcome, per BookingRequest's own docstring. There is no later step.

Rejections are not work done. Neither is approving your own request, which
the queue does automatically for staff: crediting that would let anyone raise
their own month's figures by booking rooms.
"""
from datetime import date, datetime, time

from django.utils import timezone

from .models import BookingRequest, ResourceRequest, SupportTicket
from .worktime import after_hours_minutes


def _day(value):
    """A date from a date or a datetime, read in local time.

    Not `.date()`: these timestamps are stored in UTC, so a request fulfilled
    at half past nine on a Monday evening would otherwise be filed as Monday
    afternoon -- and one fulfilled just before midnight, as the wrong month.
    """
    if value is None:
        return None
    if hasattr(value, 'hour'):
        return timezone.localtime(value).date()
    return value


def _aware(day, at):
    """A local datetime for a date, made aware when the project uses zones."""
    stamp = datetime.combine(day, at)
    if timezone.is_naive(stamp) and getattr(timezone, 'get_current_timezone', None):
        try:
            return timezone.make_aware(stamp)
        except Exception:          # USE_TZ off: a naive datetime is correct
            return stamp
    return stamp


def _item(**kw):
    kw.setdefault('minutes', 0)
    kw.setdefault('worked_from', None)
    kw.setdefault('worked_to', None)
    kw.setdefault('after_hours_minutes', 0)
    return kw


def collect(first, last, desk=None):
    """-> a flat list of every job finished between two dates, oldest last.

    `desk` narrows it to one team: 'it' is IT's own logged work and the
    tickets they closed; 'admin' is Admin's logged work plus the requests
    they fulfilled and the rooms they approved, which are Admin's queues and
    nobody else's. None is both, which only the super admin sees.

    One dict per job, whichever queue it came from:
      date, email, name, subject, category, category_label, for, status,
      origin ('requested' | 'logged'), source ('ticket' | 'item' | 'room'),
      minutes, worked_from, worked_to, after_hours_minutes, id
    """
    # The two timestamp columns below are datetimes, so they need datetime
    # bounds. Given plain dates, Django reads the end as midnight AT THE
    # START of the last day, and everything fulfilled or approved on the last
    # day of the month drops out of the month -- silently, and only ever for
    # the day nobody re-checks.
    span = (_aware(first, time.min), _aware(last, time.max))
    out = []

    tickets = SupportTicket.objects.filter(performed_on__range=(first, last))
    if desk:
        tickets = tickets.filter(desk=desk)
    for t in tickets:
        out.append(_item(
            id=f'ticket-{t.id}', source='ticket', origin=t.origin,
            date=t.performed_on,
            email=(t.performed_by_email or '').lower(),
            name=t.performed_by_name or '',
            subject=t.subject,
            category=t.category, category_label=t.get_category_display(),
            **{'for': t.logged_for or t.requested_by_name},
            status=t.get_status_display(),
            minutes=t.time_spent_minutes or 0,
            worked_from=t.worked_from, worked_to=t.worked_to,
            after_hours_minutes=after_hours_minutes(
                t.performed_on, t.worked_from, t.worked_to),
        ))

    # Admin's two queues. IT does not work them, so under desk='it' they are
    # not IT's month -- and counting them there would put an admin's work in
    # an IT engineer's total.
    if desk == 'it':
        out.sort(key=lambda i: (i['date'] or first), reverse=True)
        return out

    # Handed over, not merely agreed to.
    items = (ResourceRequest.objects
             .filter(status='fulfilled', fulfilled_at__range=span)
             .exclude(fulfilled_by=''))
    for r in items:
        doer = (r.fulfilled_by or r.reviewed_by or '').lower()
        if doer == (r.requested_by_email or '').lower():
            continue
        out.append(_item(
            id=f'item-{r.id}', source='item', origin='requested',
            date=_day(r.fulfilled_at),
            email=doer, name='',
            subject=f'{r.item_name} ×{r.quantity}' if r.quantity and r.quantity != 1
                    else r.item_name,
            category=r.category, category_label=r.get_category_display(),
            **{'for': r.requested_by_name},
            status='Fulfilled',
        ))

    rooms = (BookingRequest.objects
             .filter(status='approved', reviewed_at__range=span)
             .exclude(reviewed_by='')
             .select_related('room'))
    for b in rooms:
        doer = (b.reviewed_by or '').lower()
        if doer == (b.requested_by_email or '').lower():
            continue
        out.append(_item(
            id=f'room-{b.id}', source='room', origin='requested',
            date=_day(b.reviewed_at),
            email=doer, name='',
            # The room and the day it was booked for, because "Approved a
            # booking" forty times over is a list that says nothing.
            subject=f'{b.room.name} — {b.date:%d %b}, '
                    f'{b.start_time:%H:%M}–{b.end_time:%H:%M}',
            category='meeting_room', category_label='Meeting Room',
            **{'for': b.requested_by_name},
            status='Approved',
        ))

    out.sort(key=lambda i: (i['date'] or first), reverse=True)
    return out


def still_open(desk=None):
    """Waiting on somebody, in the queues this desk actually works."""
    tickets = SupportTicket.objects.filter(
        status__in=('pending', 'approved', 'in_progress')).count()
    if desk == 'it':
        return tickets
    admin_side = (ResourceRequest.objects.filter(
                      status__in=('pending', 'approved')).count()
                  + BookingRequest.objects.filter(status='pending').count())
    return admin_side if desk == 'admin' else tickets + admin_side
