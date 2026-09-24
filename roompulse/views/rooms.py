"""Room list (live status grid, everyone) + Room CRUD (Super Admin only)."""
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import Room, BookingRequest
from ..status import room_status
from .perms import require_role, require_signed_in


def _serialize_room(room, bookings_by_room, now, pending_by_room=None):
    st = room_status(room, bookings_by_room.get(room.id, []), now=now)
    waiting = (pending_by_room or {}).get(room.id, [])
    return {
        'id': room.id, 'name': room.name, 'label': room.label,
        'floor': room.floor, 'capacity': room.capacity,
        'amenities': room.amenities, 'color': room.color,
        'is_active': room.is_active,
        # Requests for today that nobody has approved yet. The grid used to
        # show approved bookings alone, so booking a room and being told it had
        # gone for approval was followed by that room saying "nothing booked" --
        # with no way to tell whether the request existed. It also invited two
        # people to ask for the same slot in turn.
        'pending': [{
            'id': b.id,
            'start_time': b.start_time.strftime('%H:%M'),
            'end_time': b.end_time.strftime('%H:%M'),
            'requested_by_name': b.requested_by_name,
            'requested_by_email': b.requested_by_email,
        } for b in waiting],
        **st,
    }


class RoomListView(APIView):
    """GET: every active room with its LIVE status. Available to any logged-in
    role — this is the dashboard's main grid.
    POST: create a room (Super Admin only)."""

    def get(self, request):
        # It said "any logged-in role" and meant "anybody at all". The grid
        # carries who is meeting where and until when, which is not something
        # to hand to an unauthenticated caller on a server reachable from the
        # internet.
        if (err := require_signed_in(request)):
            return err
        # The project's clock, not the machine's. This was datetime.now(),
        # which reads the server's OS timezone -- on a UTC host that put the
        # whole live grid 5.5 hours away from the times people had typed in.
        now = timezone.localtime().replace(tzinfo=None)
        rooms = list(Room.objects.filter(is_active=True))
        today_bookings = BookingRequest.objects.filter(status='approved', date=now.date())
        by_room = {}
        for b in today_bookings:
            by_room.setdefault(b.room_id, []).append(b)

        pending_by_room = {}
        for b in (BookingRequest.objects
                  .filter(status='pending', date=now.date())
                  .order_by('start_time')):
            pending_by_room.setdefault(b.room_id, []).append(b)

        return Response({
            'results': [_serialize_room(r, by_room, now, pending_by_room) for r in rooms],
            'count': len(rooms),
        })

    def post(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        d = request.data
        name = str(d.get('name') or '').strip()
        floor = str(d.get('floor') or '').strip()
        if not name or not floor:
            return Response({'error': 'Room name and floor are required.'}, status=400)
        try:
            capacity = int(d.get('capacity') or 10)
        except (TypeError, ValueError):
            capacity = 10
        room = Room.objects.create(
            name=name, label=str(d.get('label') or '').strip(), floor=floor,
            capacity=max(1, capacity),
            amenities=d.get('amenities') if isinstance(d.get('amenities'), list) else [],
            color=str(d.get('color') or '#6366f1').strip(),
        )
        return Response({'id': room.id, 'message': f'Room "{room}" created.'}, status=201)


class RoomDetailView(APIView):
    """PATCH: edit a room. DELETE: retire it (soft-delete via is_active, so
    historical bookings against it stay intact). Super Admin only."""

    def patch(self, request, room_id):
        if (err := require_role(request, 'super_admin')):
            return err
        try:
            room = Room.objects.get(id=room_id)
        except Room.DoesNotExist:
            return Response({'error': 'Room not found.'}, status=404)
        d = request.data
        for field in ('name', 'label', 'floor', 'color'):
            if field in d:
                setattr(room, field, str(d[field]).strip())
        if 'capacity' in d:
            try:
                room.capacity = max(1, int(d['capacity']))
            except (TypeError, ValueError):
                pass
        if 'amenities' in d and isinstance(d['amenities'], list):
            room.amenities = d['amenities']
        if 'is_active' in d:
            room.is_active = bool(d['is_active'])
        room.save()
        return Response({'message': 'Room updated.'})

    def delete(self, request, room_id):
        if (err := require_role(request, 'super_admin')):
            return err
        try:
            room = Room.objects.get(id=room_id)
        except Room.DoesNotExist:
            return Response({'error': 'Room not found.'}, status=404)
        room.is_active = False
        room.save(update_fields=['is_active'])
        return Response({'message': f'Room "{room}" retired.'})
