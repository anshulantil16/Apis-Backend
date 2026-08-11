"""Item/resource requests — anything Admin provides that isn't a room
(stationery, IT equipment, furniture, pantry, printing, ...).

Mirrors bookings.py's approve/reject/cancel shape, plus one extra action
rooms don't need: 'fulfil' — approving a stationery request only means "yes,
get them one", it isn't done until someone hands it over.
"""
from datetime import datetime
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import ResourceRequest
from .perms import actor_role
from .auth import resolve_role


def _parse_date(s):
    try:
        return datetime.strptime(str(s).strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError):
        return None


def _brief(r):
    return {
        'id': r.id, 'kind': 'resource',
        'requested_by_name': r.requested_by_name, 'requested_by_email': r.requested_by_email,
        'department': r.department,
        'category': r.category, 'category_label': r.get_category_display(),
        'item_name': r.item_name, 'quantity': r.quantity,
        'urgency': r.urgency, 'urgency_label': r.get_urgency_display(),
        'reason': r.reason, 'needed_by': r.needed_by.isoformat() if r.needed_by else None,
        'status': r.status, 'reviewed_by': r.reviewed_by,
        'reviewed_at': r.reviewed_at.isoformat() if r.reviewed_at else None,
        'admin_remarks': r.admin_remarks,
        'fulfilled_by': r.fulfilled_by,
        'fulfilled_at': r.fulfilled_at.isoformat() if r.fulfilled_at else None,
        'created_at': r.created_at.isoformat(),
    }


class ResourceRequestListView(APIView):
    """GET: requests, filtered by category / status / mine=<email>.
    POST: create a request.
       - Employee → always Pending.
       - Admin/Super Admin → auto-approved (they're recording something
         they're already arranging themselves — same convention as rooms).
    """

    def get(self, request):
        qs = ResourceRequest.objects.all()
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
        return Response({'results': [_brief(r) for r in qs[:limit]], 'count': qs.count()})

    def post(self, request):
        d = request.data
        email = str(d.get('email') or d.get('requested_by_email') or '').strip().lower()
        role = resolve_role(email)
        if not role:
            return Response({'error': 'Please use your @apisindia.com email address.'}, status=403)

        name = str(d.get('requested_by_name') or d.get('name') or '').strip()
        if not name:
            return Response({'error': 'Your name is required.'}, status=400)

        item_name = str(d.get('item_name') or '').strip()
        if not item_name:
            return Response({'error': 'What do you need? (item name is required)'}, status=400)

        category = str(d.get('category') or 'other').strip()
        if category not in {c[0] for c in ResourceRequest.CATEGORY_CHOICES}:
            category = 'other'
        urgency = str(d.get('urgency') or 'normal').strip()
        if urgency not in {c[0] for c in ResourceRequest.URGENCY_CHOICES}:
            urgency = 'normal'
        try:
            quantity = max(1, int(d.get('quantity') or 1))
        except (TypeError, ValueError):
            quantity = 1

        auto_approve = role in ('admin', 'super_admin')
        req = ResourceRequest.objects.create(
            requested_by_name=name[:200], requested_by_email=email,
            department=str(d.get('department') or '').strip()[:150],
            category=category, item_name=item_name[:200], quantity=quantity,
            urgency=urgency, reason=str(d.get('reason') or '').strip()[:300],
            needed_by=_parse_date(d.get('needed_by')),
            status='approved' if auto_approve else 'pending',
            reviewed_by=email if auto_approve else '',
            reviewed_at=timezone.now() if auto_approve else None,
        )
        return Response({
            'id': req.id, 'status': req.status,
            'message': ('Recorded and approved.' if auto_approve
                       else 'Request sent — an admin will review it shortly.'),
            'request': _brief(req),
        }, status=201)


class ResourceRequestActionView(APIView):
    """PATCH { action: 'approve'|'reject'|'fulfil'|'cancel', email, remarks? }

    - approve/reject: Admin or Super Admin only, from 'pending'.
    - fulfil: Admin or Super Admin only, from 'approved' — marks the item as
      actually handed over.
    - cancel: the requester themself, or Admin/Super Admin, from any status
      that isn't already a terminal one (fulfilled/cancelled).
    """

    def patch(self, request, request_id):
        try:
            req = ResourceRequest.objects.get(id=request_id)
        except ResourceRequest.DoesNotExist:
            return Response({'error': 'Request not found.'}, status=404)

        action = str(request.data.get('action') or '').strip()
        role, email = actor_role(request)
        is_staff = role in ('admin', 'super_admin')

        if action in ('approve', 'reject'):
            if not is_staff:
                return Response({'error': 'Only an admin can approve or reject requests.'}, status=403)
            if req.status != 'pending':
                return Response({'error': f'This request is already {req.status}.'}, status=400)
            req.status = 'approved' if action == 'approve' else 'rejected'
            req.reviewed_by = email
            req.reviewed_at = timezone.now()
            req.admin_remarks = str(request.data.get('remarks') or '').strip()[:300]
            req.save()
            return Response({'message': f'Request {req.status}.', 'request': _brief(req)})

        if action == 'fulfil':
            if not is_staff:
                return Response({'error': 'Only an admin can mark a request fulfilled.'}, status=403)
            if req.status != 'approved':
                return Response({'error': 'Only an approved request can be marked fulfilled.'}, status=400)
            req.status = 'fulfilled'
            req.fulfilled_by = email
            req.fulfilled_at = timezone.now()
            req.save()
            return Response({'message': 'Marked as fulfilled.', 'request': _brief(req)})

        if action == 'cancel':
            is_owner = email == req.requested_by_email.lower()
            if not (is_owner or is_staff):
                return Response({'error': 'You can only cancel your own requests.'}, status=403)
            if req.status in ('cancelled', 'fulfilled'):
                return Response({'error': f'This request is already {req.status}.'}, status=400)
            req.status = 'cancelled'
            req.reviewed_by = email
            req.reviewed_at = timezone.now()
            if is_staff and not is_owner:
                req.admin_remarks = str(request.data.get('remarks') or '').strip()[:300]
            req.save()
            return Response({'message': 'Request cancelled.', 'request': _brief(req)})

        return Response({'error': 'Invalid action.'}, status=400)
