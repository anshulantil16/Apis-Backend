"""Room list (live status grid, everyone) + Room CRUD (Super Admin only)."""
from datetime import datetime
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import Room, BookingRequest
from ..status import room_status
from .perms import require_role


def _serialize_room(room, bookings_by_room, now):
    st = room_status(room, bookings_by_room.get(room.id, []), now=now)
    return {
        'id': room.id, 'name': room.name, 'label': room.label,
        'floor': room.floor, 'capacity': room.capacity,
        'amenities': room.amenities, 'color': room.color,
        'is_active': room.is_active,
        **st,
    }


class RoomListView(APIView):
    """GET: every active room with its LIVE status. Available to any logged-in
    role — this is the dashboard's main grid.
    POST: create a room (Super Admin only)."""

    def get(self, request):
        now = datetime.now()
        rooms = list(Room.objects.filter(is_active=True))
        today_bookings = BookingRequest.objects.filter(status='approved', date=now.date())
        by_room = {}
        for b in today_bookings:
            by_room.setdefault(b.room_id, []).append(b)
        return Response({'results': [_serialize_room(r, by_room, now) for r in rooms],
                         'count': len(rooms)})

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
