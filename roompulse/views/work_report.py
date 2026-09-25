"""What the IT and admin teams actually did in a month.

Two things feed it and they have to be counted together, or the number is
wrong in a way nobody notices: tickets people raised and the team closed, and
jobs the team did that nobody raised a ticket for. The second kind is a large
part of the work -- a server restarted, a laptop rebuilt for a joiner, a
printer fixed because somebody walked over and asked -- and until it could be
logged, a monthly report measured how often people happened to use the ticket
form rather than how much work was done.

Both kinds come from work_items.collect, which reads all three queues -- IT
tickets, item requests and room bookings -- and flattens them to one shape,
so an Admin's month is counted as fully as an IT engineer's. The split is
reported rather than hidden: "sixty jobs, of which thirty-eight came through
a queue" says something about the team AND something about whether people are
using the queues.
"""
from calendar import monthrange
from datetime import date

from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import AdminUser
from ..work_items import collect, still_open
from ..worktime import OFFICE_END, OFFICE_START
from .perms import actor_role, require_role


# Which team's month a caller is looking at. IT and Admin are different
# people doing different work, so each gets their own report rather than one
# merged figure neither of them recognises. The super admin oversees both and
# can ask for either, or for everything.
def _desk_for(request):
    role, _ = actor_role(request)
    if role == 'admin':
        return 'admin'
    if role == 'it_support':
        return 'it'
    asked = (request.query_params.get('desk') or '').strip().lower()
    return asked if asked in ('it', 'admin') else None


DESK_LABEL = {'it': 'IT', 'admin': 'Admin', None: 'IT and Admin'}


def _month_bounds(raw, today):
    """-> (first, last, label) for a 'YYYY-MM' string, or this month."""
    year, month = today.year, today.month
    if raw:
        try:
            year, month = (int(p) for p in str(raw).split('-')[:2])
        except (TypeError, ValueError):
            pass
    if not (1 <= month <= 12 and 2000 <= year <= 2100):
        year, month = today.year, today.month
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    return first, last, f'{first:%B %Y}'


class WorkReportView(APIView):
    """GET ?month=YYYY-MM — the month's work, per person and per category.
    With ?person=<email>, also every job that person did."""

    def get(self, request):
        # The team's own record of its output. An employee sees their own
        # tickets elsewhere; this is not that.
        if (err := require_role(request, 'it_support', 'admin', 'super_admin')):
            return err

        from django.utils import timezone
        first, last, label = _month_bounds(request.query_params.get('month'),
                                           timezone.localdate())

        # Work that HAPPENED in the month, by the day it was done -- not by
        # the day it was asked for. A ticket opened in March and closed in
        # April is April's work, and counting it in March would credit a
        # month in which nothing was finished.
        #
        # Collected across all three queues (see work_items.collect), so an
        # Admin's month counts the requests they fulfilled and the bookings
        # they approved, the same way IT's counts the tickets they closed.
        desk = _desk_for(request)
        rows = collect(first, last, desk)

        names = {a.email.lower(): a.name for a in AdminUser.objects.all()}

        def who(r):
            return (names.get(r['email']) or r['name']
                    or (r['email'].split('@')[0] if r['email'] else 'Unattributed'))

        people, by_category, by_origin, by_source = {}, {}, {}, {}
        minutes = after_total = 0
        for r in rows:
            by_origin[r['origin']] = by_origin.get(r['origin'], 0) + 1
            by_source[r['source']] = by_source.get(r['source'], 0) + 1
            minutes += r['minutes'] or 0
            after_total += r['after_hours_minutes'] or 0

            c = by_category.setdefault(r['category'],
                                       {'category': r['category'],
                                        'label': r['category_label'], 'count': 0})
            c['count'] += 1

            p = people.setdefault(r['email'], {
                'email': r['email'], 'name': who(r),
                'closed': 0, 'logged': 0, 'total': 0, 'minutes': 0,
                'after_hours_minutes': 0,
            })
            p['logged' if r['origin'] == 'logged' else 'closed'] += 1
            p['total'] += 1
            p['minutes'] += r['minutes'] or 0
            p['after_hours_minutes'] += r['after_hours_minutes'] or 0

        # One person's actual jobs, for showing a manager what the number is
        # made of. A count on its own invites the question straight back.
        person = (request.query_params.get('person') or '').strip().lower()
        items = [{
            'id': r['id'],
            'date': r['date'].isoformat() if r['date'] else None,
            'subject': r['subject'],
            'category': r['category_label'],
            'origin': r['origin'],
            'source': r['source'],
            'how': {'ticket': 'From a ticket', 'item': 'Item request',
                    'room': 'Room booking'}[r['source']]
                   if r['origin'] != 'logged' else 'Logged',
            'for': r['for'],
            'minutes': r['minutes'],
            'worked_from': r['worked_from'].strftime('%H:%M') if r['worked_from'] else None,
            'worked_to': r['worked_to'].strftime('%H:%M') if r['worked_to'] else None,
            'after_hours_minutes': r['after_hours_minutes'],
            'status': r['status'],
        } for r in rows if person and r['email'] == person]

        return Response({
            'month': f'{first:%Y-%m}',
            'label': label,
            'desk': desk or '',
            'desk_label': DESK_LABEL[desk],
            'from': first.isoformat(),
            'to': last.isoformat(),
            'total': len(rows),
            'from_tickets': by_origin.get('requested', 0),
            'logged_directly': by_origin.get('logged', 0),
            # What the queue half is actually made of, so Admin can see their
            # own work in it rather than a number labelled "tickets".
            'from_queue': {
                'tickets': by_source.get('ticket', 0) - by_origin.get('logged', 0),
                'item_requests': by_source.get('item', 0),
                'room_bookings': by_source.get('room', 0),
            },
            'minutes_recorded': minutes,
            'after_hours_minutes': after_total,
            'office_hours': f'{OFFICE_START:%H:%M}-{OFFICE_END:%H:%M}',
            'people': sorted(people.values(), key=lambda p: -p['total']),
            'by_category': sorted(by_category.values(), key=lambda c: -c['count']),
            # Still waiting on somebody at the end of the month: not output,
            # but the other half of the picture, and the number a manager
            # asks about next.
            'still_open': still_open(desk),
            'person': person,
            'items': items,
        })


class WorkReportExportView(APIView):
    """GET ?month=YYYY-MM — the same month as a workbook.

    Separate from the JSON because this one leaves the building: it is what
    gets attached to an email to a manager, so it carries the sentence about
    how the counting works rather than assuming whoever opens it was told.
    """

    def get(self, request):
        if (err := require_role(request, 'it_support', 'admin', 'super_admin')):
            return err

        from django.http import HttpResponse
        from django.utils import timezone

        from ..work_export import build_work_report

        first, last, label = _month_bounds(request.query_params.get('month'),
                                           timezone.localdate())
        rows = collect(first, last, _desk_for(request))
        rows.reverse()      # oldest first reads better down a spreadsheet

        # The same numbers the screen shows, built the same way -- a file that
        # disagrees with the page it was downloaded from is worse than no file.
        inner = WorkReportView()
        inner.request = request
        report = inner.get(request).data

        r = HttpResponse(build_work_report(report, rows),
                         content_type='application/vnd.openxmlformats-officedocument'
                                      '.spreadsheetml.sheet')
        r['Content-Disposition'] = (
            f'attachment; filename="work-done-{first:%Y-%m}.xlsx"')
        return r
