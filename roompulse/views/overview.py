"""The Super Admin's one screen: what needs attention, and is anything wrong.

Everything here was already visible somewhere, in five places, each behind a
tab. That is fine for doing a job and useless for noticing one -- a ticket
nobody has touched in nine days does not announce itself from the queue it is
sitting quietly at the bottom of.

So this answers three questions and nothing else: what is waiting on somebody,
what is happening right now, and is the setup itself healthy. Anything a
person would act on is here with the number that makes it actionable -- how
long it has been waiting, not just that it exists.
"""
from datetime import timedelta

from django.db.models import Count, Min
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import (AdminUser, BookingRequest, Employee, ResourceRequest,
                      Room, SupportTicket, TicketEvent)
from ..status import room_status
from .perms import require_role

# A request nobody has answered for this long is not "in the queue", it is
# forgotten. Two working days is the point at which somebody should be told.
STALE_DAYS = 2


def _oldest_age(qs, field='created_at'):
    """Days since the oldest row in a queue, or None if the queue is empty."""
    oldest = qs.aggregate(o=Min(field))['o']
    if not oldest:
        return None
    return (timezone.now() - oldest).days


class OverviewView(APIView):
    """GET — the whole system on one screen. Super Admin only."""

    def get(self, request):
        if (err := require_role(request, 'super_admin')):
            return err

        now = timezone.localtime().replace(tzinfo=None)
        today = timezone.localdate()
        stale_before = timezone.now() - timedelta(days=STALE_DAYS)

        # ── waiting on somebody ──────────────────────────────────────────
        tickets = SupportTicket.objects.filter(status='pending').exclude(origin='logged')
        bookings = BookingRequest.objects.filter(status='pending')
        items = ResourceRequest.objects.filter(status='pending')

        in_progress = SupportTicket.objects.filter(status='in_progress')

        waiting = [
            {'what': 'IT tickets awaiting triage', 'count': tickets.count(),
             'oldest_days': _oldest_age(tickets),
             'stale': tickets.filter(created_at__lt=stale_before).count(),
             'where': 'approvals'},
            {'what': 'Room bookings awaiting approval', 'count': bookings.count(),
             'oldest_days': _oldest_age(bookings),
             'stale': bookings.filter(created_at__lt=stale_before).count(),
             'where': 'approvals'},
            {'what': 'Item requests awaiting approval', 'count': items.count(),
             'oldest_days': _oldest_age(items),
             'stale': items.filter(created_at__lt=stale_before).count(),
             'where': 'approvals'},
            {'what': 'Tickets being worked on', 'count': in_progress.count(),
             'oldest_days': _oldest_age(in_progress),
             'stale': in_progress.filter(created_at__lt=stale_before).count(),
             'where': 'approvals'},
        ]

        # ── right now ────────────────────────────────────────────────────
        rooms = list(Room.objects.filter(is_active=True))
        todays = BookingRequest.objects.filter(status='approved', date=today)
        by_room = {}
        for b in todays:
            by_room.setdefault(b.room_id, []).append(b)

        live = []
        for room in rooms:
            st = room_status(room, by_room.get(room.id, []), now=now)
            live.append({
                'id': room.id, 'name': str(room), 'status': st['status'],
                'until': st.get('until'),
                'who': (st.get('current_booking') or {}).get('requested_by_name', ''),
                'next': (st.get('next_booking') or {}).get('start_time'),
            })

        # ── is the setup healthy ─────────────────────────────────────────
        #
        # The questions that only bite later: is anybody actually assigned to
        # answer each queue, and is the employee list current? A helpdesk with
        # no IT Support on it accepts tickets and never answers them.
        admins = AdminUser.objects.values('scope').annotate(n=Count('id'))
        by_scope = {row['scope']: row['n'] for row in admins}
        directory = Employee.objects.filter(source='directory')
        last_sync = directory.order_by('-synced_at').values_list('synced_at', flat=True).first()

        problems = []
        if not by_scope.get('it_support'):
            problems.append('Nobody is assigned to IT Support, so IT tickets have no one to '
                            'triage them.')
        if not by_scope.get('admin'):
            problems.append('Nobody is assigned to Admin, so room bookings and item requests '
                            'have no one to approve them.')
        if not rooms:
            problems.append('No rooms are set up, so nobody can book one.')
        if not Employee.objects.filter(is_active=True).exists():
            problems.append('The employee list is empty — sync the company directory.')
        elif last_sync and (timezone.now() - last_sync).days > 30:
            problems.append(f'The directory was last synced {(timezone.now() - last_sync).days} '
                            f'days ago — joiners and leavers since then are not reflected.')
        stale_total = sum(w['stale'] for w in waiting)
        if stale_total:
            problems.append(f'{stale_total} request(s) have been waiting more than '
                            f'{STALE_DAYS} days.')

        # ── who did what, lately ─────────────────────────────────────────
        #
        # One feed rather than per-module histories: oversight means noticing
        # something odd, and you cannot notice it in a list you did not think
        # to open.
        recent = []
        for e in (TicketEvent.objects.select_related('ticket')
                  .order_by('-created_at')[:25]):
            recent.append({
                'at': e.created_at.isoformat(),
                'who': e.actor_email or 'someone',
                'role': e.actor_role,
                'action': e.get_action_display(),
                'subject': e.ticket.subject,
                'kind': 'Work logged' if e.action == 'logged' else 'Ticket',
            })

        return Response({
            'waiting': waiting,
            'waiting_total': sum(w['count'] for w in waiting),
            'stale_total': stale_total,
            'stale_after_days': STALE_DAYS,
            'rooms': live,
            'rooms_in_use': sum(1 for r in live if r['status'] == 'occupied'),
            'rooms_total': len(live),
            'problems': problems,
            'people': {
                'total': Employee.objects.filter(is_active=True).count(),
                'admins': by_scope.get('admin', 0),
                'it_support': by_scope.get('it_support', 0),
                'added_here': Employee.objects.filter(source='manual', is_active=True).count(),
                'inactive': Employee.objects.filter(is_active=False).count(),
                'last_synced_at': last_sync.isoformat() if last_sync else None,
            },
            'today': {
                'tickets_raised': SupportTicket.objects.filter(
                    created_at__date=today).exclude(origin='logged').count(),
                'jobs_done': SupportTicket.objects.filter(performed_on=today).count(),
                'bookings': todays.count(),
            },
            'recent': recent,
        })
