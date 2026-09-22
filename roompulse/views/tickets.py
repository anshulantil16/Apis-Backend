"""IT support tickets — technical/access issues, reviewed by the IT Support
role rather than Admin (see AdminUser.scope). Mirrors resource_requests.py's
approve/reject shape, plus the two steps that move an approved ticket to
done: 'start' (approved -> in_progress) and 'close' (in_progress -> closed).
"""
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from ..models import SupportTicket, TicketAttachment
from .perms import actor_role
from .auth import resolve_role

MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024  # matches the "Max 5MB per file" the upload UI states


def _brief(t, request=None):
    def _url(a):
        u = a.file.url
        return request.build_absolute_uri(u) if request else u
    return {
        'id': t.id, 'kind': 'ticket',
        'requested_by_name': t.requested_by_name, 'requested_by_email': t.requested_by_email,
        'department': t.department,
        'category': t.category, 'category_label': t.get_category_display(),
        'priority': t.priority, 'priority_label': t.get_priority_display(),
        'subject': t.subject, 'description': t.description, 'related_to': t.related_to,
        'attachments': [{'id': a.id, 'name': a.original_name or a.file.name, 'url': _url(a)}
                        for a in t.attachments.all()],
        'status': t.status, 'status_label': t.get_status_display(),
        'reviewed_by': t.reviewed_by,
        'reviewed_at': t.reviewed_at.isoformat() if t.reviewed_at else None,
        'admin_remarks': t.admin_remarks,
        'created_at': t.created_at.isoformat(),
        'updated_at': t.updated_at.isoformat(),
    }


class TicketListView(APIView):
    """GET: tickets, filtered by category / status / mine=<email>.
    POST: create a ticket.
       - Employee → always Pending, awaiting IT Support triage.
       - IT Support/Super Admin → auto-approved (they're recording something
         they're already handling themselves — same convention as rooms and
         item requests).
    """

    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        qs = SupportTicket.objects.all()
        category = request.query_params.get('category')
        if category:
            qs = qs.filter(category=category)
        status = request.query_params.get('status')
        if status:
            qs = qs.filter(status=status)
        mine = request.query_params.get('mine')
        if mine:
            qs = qs.filter(requested_by_email=mine.strip().lower())
        try:
            limit = max(1, min(500, int(request.query_params.get('limit', 200))))
        except (TypeError, ValueError):
            limit = 200
        return Response({'results': [_brief(t, request) for t in qs[:limit]], 'count': qs.count()})

    def post(self, request):
        d = request.data
        email = str(d.get('email') or d.get('requested_by_email') or '').strip().lower()
        role = resolve_role(email)
        if not role:
            return Response({'error': 'Please use your @apisindia.com email address.'}, status=403)

        subject = str(d.get('subject') or '').strip()
        if not subject:
            return Response({'error': 'A subject is required.'}, status=400)
        description = str(d.get('description') or '').strip()
        if not description:
            return Response({'error': 'Please describe the issue.'}, status=400)

        category = str(d.get('category') or 'other').strip()
        if category not in {c[0] for c in SupportTicket.CATEGORY_CHOICES}:
            category = 'other'
        priority = str(d.get('priority') or 'medium').strip()
        if priority not in {c[0] for c in SupportTicket.PRIORITY_CHOICES}:
            priority = 'medium'

        files = request.FILES.getlist('attachments')[:MAX_ATTACHMENTS]
        oversized = [f.name for f in files if f.size > MAX_ATTACHMENT_BYTES]
        if oversized:
            return Response({'error': f'These files exceed the 5MB limit: {", ".join(oversized)}.'}, status=400)

        auto_approve = role in ('it_support', 'super_admin')
        ticket = SupportTicket.objects.create(
            requested_by_name=str(d.get('requested_by_name') or d.get('name') or '').strip()[:200],
            requested_by_email=email,
            department=str(d.get('department') or '').strip()[:150],
            category=category, priority=priority, subject=subject[:200], description=description,
            related_to=str(d.get('related_to') or '').strip()[:100],
            status='approved' if auto_approve else 'pending',
            reviewed_by=email if auto_approve else '',
            reviewed_at=timezone.now() if auto_approve else None,
        )
        for f in files:
            TicketAttachment.objects.create(ticket=ticket, file=f, original_name=f.name[:255])

        return Response({
            'id': ticket.id, 'status': ticket.status,
            'message': ('Recorded and approved.' if auto_approve
                       else 'Ticket submitted — IT Support will review it shortly.'),
            'ticket': _brief(ticket, request),
        }, status=201)


class TicketActionView(APIView):
    """PATCH { action: 'approve'|'reject'|'start'|'close'|'cancel', email, remarks? }

    - approve/reject: IT Support or Super Admin only, from 'pending'.
    - start: IT Support or Super Admin only, from 'approved' — work begins.
    - close: IT Support or Super Admin only, from 'in_progress' — resolved.
    - cancel: the requester themself, or IT Support/Super Admin, while still
      'pending' (once triaged, only IT Support drives it forward).
    """

    def patch(self, request, ticket_id):
        try:
            ticket = SupportTicket.objects.get(id=ticket_id)
        except SupportTicket.DoesNotExist:
            return Response({'error': 'Ticket not found.'}, status=404)

        action = str(request.data.get('action') or '').strip()
        role, email = actor_role(request)
        is_it_staff = role in ('it_support', 'super_admin')

        if action in ('approve', 'reject'):
            if not is_it_staff:
                return Response({'error': 'Only IT Support can approve or reject tickets.'}, status=403)
            if ticket.status != 'pending':
                return Response({'error': f'This ticket is already {ticket.status}.'}, status=400)
            ticket.status = 'approved' if action == 'approve' else 'rejected'
            ticket.reviewed_by = email
            ticket.reviewed_at = timezone.now()
            ticket.admin_remarks = str(request.data.get('remarks') or '').strip()[:300]
            ticket.save()
            return Response({'message': f'Ticket {ticket.status}.', 'ticket': _brief(ticket, request)})

        if action == 'start':
            if not is_it_staff:
                return Response({'error': 'Only IT Support can start work on a ticket.'}, status=403)
            if ticket.status != 'approved':
                return Response({'error': 'Only an approved ticket can move to In Progress.'}, status=400)
            ticket.status = 'in_progress'
            ticket.save()
            return Response({'message': 'Ticket marked In Progress.', 'ticket': _brief(ticket, request)})

        if action == 'close':
            if not is_it_staff:
                return Response({'error': 'Only IT Support can close a ticket.'}, status=403)
            if ticket.status != 'in_progress':
                return Response({'error': 'Only a ticket In Progress can be closed.'}, status=400)
            ticket.status = 'closed'
            ticket.save()
            return Response({'message': 'Ticket closed.', 'ticket': _brief(ticket, request)})

        if action == 'cancel':
            is_owner = email == ticket.requested_by_email.lower()
            if not (is_owner or is_it_staff):
                return Response({'error': 'You can only cancel your own tickets.'}, status=403)
            if ticket.status != 'pending':
                return Response({'error': 'Only a pending ticket can be cancelled.'}, status=400)
            ticket.status = 'rejected'
            ticket.reviewed_by = email
            ticket.reviewed_at = timezone.now()
            ticket.admin_remarks = 'Cancelled by requester.' if is_owner else str(request.data.get('remarks') or '').strip()[:300]
            ticket.save()
            return Response({'message': 'Ticket cancelled.', 'ticket': _brief(ticket, request)})

        return Response({'error': 'Invalid action.'}, status=400)
