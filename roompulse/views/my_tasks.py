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

from ..models import BookingRequest, ResourceRequest, SupportTicket
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

        tickets = SupportTicket.objects.filter(
            assigned_to_email__iexact=who).exclude(origin='logged')
        items = ResourceRequest.objects.filter(assigned_to_email__iexact=who)
        rooms = (BookingRequest.objects
                 .filter(assigned_to_email__iexact=who).select_related('room'))
        if not everything:
            tickets = tickets.filter(status__in=OPEN_TICKET)
            items = items.filter(status__in=OPEN_ITEM)
            rooms = rooms.filter(status='pending')

        rows = []
        for t in tickets:
            rows.append({
                'id': t.id, 'kind': 'ticket', 'what': t.subject,
                'detail': t.get_category_display(),
                'from': t.requested_by_name or t.requested_by_email,
                'status': t.status, 'status_label': t.get_status_display(),
                'urgency': t.get_priority_display(),
                'raised_at': t.created_at.isoformat(),
            })
        for r in items:
            rows.append({
                'id': r.id, 'kind': 'item',
                'what': f'{r.item_name} ×{r.quantity}' if r.quantity != 1 else r.item_name,
                'detail': r.get_category_display(),
                'from': r.requested_by_name or r.requested_by_email,
                'status': r.status, 'status_label': r.get_status_display(),
                'urgency': r.get_urgency_display(),
                'raised_at': r.created_at.isoformat(),
            })
        for b in rooms:
            rows.append({
                'id': b.id, 'kind': 'room',
                'what': f'{b.room} — {b.date:%d %b}, '
                        f'{b.start_time:%H:%M}–{b.end_time:%H:%M}',
                'detail': b.get_purpose_display(),
                'from': b.requested_by_name or b.requested_by_email,
                'status': b.status, 'status_label': b.get_status_display(),
                'urgency': '',
                'raised_at': b.created_at.isoformat(),
            })

        # Oldest first: the one that has been waiting longest is the one to
        # do next, which is the opposite of how a feed is usually sorted.
        rows.sort(key=lambda r: r['raised_at'])
        return Response({
            'person': who,
            'waiting': len(rows),
            'by_kind': {k: sum(1 for r in rows if r['kind'] == k)
                        for k in ('ticket', 'item', 'room')},
            'results': rows,
        })
