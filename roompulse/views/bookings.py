"""Booking create/list/approve/reject/cancel + per-room day calendar."""
from datetime import datetime, date as date_cls, time as time_cls
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import Room, BookingRequest
from ..status import find_conflicts
from .perms import require_role, actor_role
from .auth import resolve_role


def _parse_date(s):
    try:
        return datetime.strptime(str(s).strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError):
        return None


def _parse_time(s):
    try:
        return datetime.strptime(str(s).strip(), '%H:%M').time()
    except (ValueError, AttributeError):
        return None


def _brief(b):
    return {
        'id': b.id, 'room_id': b.room_id, 'room_name': str(b.room),
        'requested_by_name': b.requested_by_name, 'requested_by_email': b.requested_by_email,
        'department': b.department, 'date': b.date.isoformat(),
        'start_time': b.start_time.strftime('%H:%M'), 'end_time': b.end_time.strftime('%H:%M'),
        'purpose': b.purpose, 'purpose_label': b.get_purpose_display(),
        'purpose_detail': b.purpose_detail, 'attendees': b.attendees,
        'status': b.status, 'reviewed_by': b.reviewed_by,
        'reviewed_at': b.reviewed_at.isoformat() if b.reviewed_at else None,
        'admin_remarks': b.admin_remarks, 'created_at': b.created_at.isoformat(),
    }


class BookingListView(APIView):
    """GET: bookings, filtered by room / date / status / mine=<email>.
    POST: create a booking request.
       - Employee → always Pending, regardless of what they send.
       - Admin/Super Admin → auto-approved (this is them booking directly,
         the digital equivalent of the old "admin just does it" email reply).
    """

    def get(self, request):
        qs = BookingRequest.objects.select_related('room').all()
        room_id = request.query_params.get('room')
        if room_id:
            qs = qs.filter(room_id=room_id)
        d = _parse_date(request.query_params.get('date'))
        if d:
            qs = qs.filter(date=d)
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
        return Response({'results': [_brief(b) for b in qs[:limit]], 'count': qs.count()})

    def post(self, request):
        d = request.data
        email = str(d.get('email') or d.get('requested_by_email') or '').strip().lower()
        role = resolve_role(email)
        if not role:
            return Response({'error': 'Please use your @apisindia.com email address.'}, status=403)

        try:
            room = Room.objects.get(id=d.get('room_id'), is_active=True)
        except (Room.DoesNotExist, TypeError, ValueError):
            return Response({'error': 'Room not found or no longer available.'}, status=404)

        booking_date = _parse_date(d.get('date'))
        start_time = _parse_time(d.get('start_time'))
        end_time = _parse_time(d.get('end_time'))
        if not booking_date:
            return Response({'error': 'A valid date (YYYY-MM-DD) is required.'}, status=400)
        if not start_time or not end_time:
            return Response({'error': 'Start and end time (HH:MM) are required.'}, status=400)
        if end_time <= start_time:
            return Response({'error': 'End time must be after start time.'}, status=400)
        if booking_date < timezone.localdate():
            return Response({'error': 'Cannot book a room in the past.'}, status=400)

        name = str(d.get('requested_by_name') or d.get('name') or '').strip()
        if not name:
            return Response({'error': 'Your name is required.'}, status=400)

        purpose = str(d.get('purpose') or 'internal_meeting').strip()
        valid_purposes = {c[0] for c in BookingRequest.PURPOSE_CHOICES}
        if purpose not in valid_purposes:
            purpose = 'other'
        try:
            attendees = max(1, int(d.get('attendees') or 1))
        except (TypeError, ValueError):
            attendees = 1
        if attendees > room.capacity:
            return Response({
                'error': f'{room} seats {room.capacity}, but {attendees} attendees were requested. '
                         f'Choose a larger room or reduce attendees.',
            }, status=400)

        auto_approve = role in ('admin', 'super_admin')
        if auto_approve:
            conflicts = find_conflicts(BookingRequest.objects.filter(room=room, status='approved'),
                                       booking_date, start_time, end_time)
            if conflicts:
                c = conflicts[0]
                return Response({
                    'error': f'{room} is already booked {c.start_time.strftime("%H:%M")}–'
                             f'{c.end_time.strftime("%H:%M")} by {c.requested_by_name} '
                             f'for "{c.get_purpose_display()}".',
                    'conflict': _brief(c),
                }, status=409)

        booking = BookingRequest.objects.create(
            room=room, requested_by_name=name[:200], requested_by_email=email,
            department=str(d.get('department') or '').strip()[:150],
            date=booking_date, start_time=start_time, end_time=end_time,
            purpose=purpose, purpose_detail=str(d.get('purpose_detail') or '').strip()[:300],
            attendees=attendees,
            status='approved' if auto_approve else 'pending',
            reviewed_by=email if auto_approve else '',
            reviewed_at=timezone.now() if auto_approve else None,
        )
        return Response({
            'id': booking.id, 'status': booking.status,
            'message': (f'Booked. {room} is confirmed for you.' if auto_approve
                       else f'Request sent — {room} will be confirmed once an admin approves it.'),
            'booking': _brief(booking),
        }, status=201)


class BookingActionView(APIView):
    """PATCH { action: 'approve'|'reject'|'cancel', email, remarks? }

    - approve/reject: Admin or Super Admin only.
    - cancel: the requester themself (any status not already cancelled), or
      Admin/Super Admin (any booking, any status).
    """

    def patch(self, request, booking_id):
        try:
            booking = BookingRequest.objects.select_related('room').get(id=booking_id)
        except BookingRequest.DoesNotExist:
            return Response({'error': 'Booking not found.'}, status=404)

        action = str(request.data.get('action') or '').strip()
        role, email = actor_role(request)

        if action in ('approve', 'reject'):
            if role not in ('admin', 'super_admin'):
                return Response({'error': 'Only an admin can approve or reject bookings.'},
                                status=403)
            if booking.status != 'pending':
                return Response({'error': f'This booking is already {booking.status}.'}, status=400)

            if action == 'approve':
                conflicts = find_conflicts(
                    BookingRequest.objects.filter(room=booking.room, status='approved'),
                    booking.date, booking.start_time, booking.end_time, exclude_id=booking.id)
                if conflicts:
                    c = conflicts[0]
                    return Response({
                        'error': f'Cannot approve — conflicts with an existing booking by '
                                 f'{c.requested_by_name} ({c.start_time.strftime("%H:%M")}–'
                                 f'{c.end_time.strftime("%H:%M")}).',
                        'conflict': _brief(c),
                    }, status=409)
                booking.status = 'approved'
            else:
                booking.status = 'rejected'
            booking.reviewed_by = email
            booking.reviewed_at = timezone.now()
            booking.admin_remarks = str(request.data.get('remarks') or '').strip()[:300]
            booking.save()
            return Response({'message': f'Booking {booking.status}.', 'booking': _brief(booking)})

        if action == 'cancel':
            is_owner = email == booking.requested_by_email.lower()
            is_staff = role in ('admin', 'super_admin')
            if not (is_owner or is_staff):
                return Response({'error': 'You can only cancel your own bookings.'}, status=403)
            if booking.status == 'cancelled':
                return Response({'error': 'This booking is already cancelled.'}, status=400)
            booking.status = 'cancelled'
            booking.reviewed_by = email
            booking.reviewed_at = timezone.now()
            if is_staff and not is_owner:
                booking.admin_remarks = str(request.data.get('remarks') or '').strip()[:300]
            booking.save()
            return Response({'message': 'Booking cancelled.', 'booking': _brief(booking)})

        return Response({'error': 'Invalid action.'}, status=400)


class RoomCalendarView(APIView):
    """Full day timeline for one room — every non-cancelled booking, so
    Admin can see pending requests alongside confirmed ones for that day."""

    def get(self, request, room_id):
        d = _parse_date(request.query_params.get('date')) or timezone.localdate()
        try:
            room = Room.objects.get(id=room_id)
        except Room.DoesNotExist:
            return Response({'error': 'Room not found.'}, status=404)
        bookings = (BookingRequest.objects.filter(room=room, date=d)
                   .exclude(status='cancelled').order_by('start_time'))
        return Response({
            'room': {'id': room.id, 'name': str(room), 'capacity': room.capacity},
            'date': d.isoformat(),
            'bookings': [_brief(b) for b in bookings],
        })
