"""What is on one person's desk, across all three queues.

"My Requests" answers what I asked for. This answers the other half -- what
has been given to me -- which is the screen a person on a desk actually works
from, and the number they are measured by at the end of the month.

One call rather than three, because the question is "what do I have", not
"what do I have in each table", and three lists that arrive separately show
the person a total that is briefly wrong.
"""
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db.models import Q

from ..assignment import desk_of
from ..models import BookingRequest, ResourceRequest, SupportTicket
from ..people import name_map
from .perms import actor_role, require_role

OPEN_TICKET = ('pending', 'approved', 'in_progress')
OPEN_ITEM = ('pending', 'approved')


class MyTasksView(APIView):
    """GET -> everything addressed to the caller, open first.

    ?all=1 includes what is already finished; by default this is the work
    still to do, which is what the screen is for.
    """

    def get(self, request):
        if (err := require_role(request, 'admin', 'it_support', 'super_admin')):
            return err
        role, email = actor_role(request)
        # The super admin has no desk of their own; asking for "mine" as
        # them would silently answer with somebody else's roster address.
        who = (request.query_params.get('person') or email).strip().lower()
        if role != 'super_admin':
            who = email
        everything = request.query_params.get('all') in ('1', 'true', 'yes')

        # Yours, plus anything addressed to nobody on your desk.
        #
        # Work raised before assignment existed has no assignee, and a row
        # belonging to nobody shows on nobody's screen -- which is not a
        # transitional glitch, it is a queue of real requests that silently
        # stopped being anyone's job. Unassigned work is nobody's, so showing
        # it to the whole desk takes nothing from anybody, and whoever picks
        # it up makes it theirs.
        desk = desk_of(who) or 'it'
        mine_or_loose = Q(assigned_to_email__iexact=who) | Q(assigned_to_email='')

        tickets = SupportTicket.objects.filter(mine_or_loose).exclude(origin='logged')
        items = ResourceRequest.objects.filter(mine_or_loose)
        rooms = BookingRequest.objects.filter(mine_or_loose).select_related('room')
        # An unassigned row still belongs to one desk or the other: tickets
        # are IT's, rooms and items are Admin's. Without this the two desks
        # would see each other's loose work.
        if desk == 'admin':
            tickets = tickets.none()
        else:
            items, rooms = items.none(), rooms.none()
        if not everything:
            tickets = tickets.filter(status__in=OPEN_TICKET)
            items = items.filter(status__in=OPEN_ITEM)
            rooms = rooms.filter(status='pending')

        rows = []
        for t in tickets:
            rows.append({
                'id': t.id, 'kind': 'ticket', 'what': t.subject,
                'mine': bool(t.assigned_to_email),
                'detail': t.get_category_display(),
                'from_email': t.requested_by_email,
                'from': t.requested_by_name or t.requested_by_email,
                'status': t.status, 'status_label': t.get_status_display(),
                'urgency': t.get_priority_display(),
                'raised_at': t.created_at.isoformat(),
            })
        for r in items:
            rows.append({
                'id': r.id, 'kind': 'item',
                'mine': bool(r.assigned_to_email),
                'what': f'{r.item_name} ×{r.quantity}' if r.quantity != 1 else r.item_name,
                'detail': r.get_category_display(),
                'from_email': r.requested_by_email,
                'from': r.requested_by_name or r.requested_by_email,
                'status': r.status, 'status_label': r.get_status_display(),
                'urgency': r.get_urgency_display(),
                'raised_at': r.created_at.isoformat(),
            })
        for b in rooms:
            rows.append({
                'id': b.id, 'kind': 'room',
                'mine': bool(b.assigned_to_email),
                'what': f'{b.room} — {b.date:%d %b}, '
                        f'{b.start_time:%H:%M}–{b.end_time:%H:%M}',
                'detail': b.get_purpose_display(),
                'from_email': b.requested_by_email,
                'from': b.requested_by_name or b.requested_by_email,
                'status': b.status, 'status_label': b.get_status_display(),
                'urgency': '',
                'raised_at': b.created_at.isoformat(),
            })

        # Oldest first: the one that has been waiting longest is the one to
        # do next, which is the opposite of how a feed is usually sorted.
        # Whoever asked, by the name the company knows them by.
        proper = name_map([r['from_email'] for r in rows])
        for r in rows:
            r['from'] = proper.get((r['from_email'] or '').lower()) or r['from']

        rows.sort(key=lambda r: r['raised_at'])
        return Response({
            'person': who,
            'desk': desk,
            'waiting': len(rows),
            # Called out separately so "nobody has picked this up" reads as
            # what it is rather than as more of your own work.
            'unassigned': sum(1 for r in rows if not r['mine']),
            'by_kind': {k: sum(1 for r in rows if r['kind'] == k)
                        for k in ('ticket', 'item', 'room')},
            'results': rows,
        })
