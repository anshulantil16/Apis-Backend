"""Super Admin: full database reset.

Destructive by design — wipes bookings, the employee directory and the admin
roster, then restores the room list to exactly the 3 real APIS rooms
(re-activating any that were retired, removing any that were added for
testing). Requires an explicit confirm token in addition to Super Admin
role, on top of whatever confirmation the frontend adds — a button this
destructive should never fire from a single accidental click reaching the
server.
"""
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import Room, BookingRequest, Employee, AdminUser
from ..seed_data import SEED_ROOMS, SEED_ROOM_DEFAULTS
from .perms import require_role


class ResetDatabaseView(APIView):
    def post(self, request):
        if (err := require_role(request, 'super_admin')):
            return err
        confirm = str(request.data.get('confirm') or '').strip().upper()
        if confirm != 'RESET':
            return Response({'error': 'Confirmation required: send confirm: "RESET".'}, status=400)

        counts = {
            'bookings': BookingRequest.objects.count(),
            'employees': Employee.objects.count(),
            'admins': AdminUser.objects.count(),
            'rooms': Room.objects.count(),
        }

        BookingRequest.objects.all().delete()
        Employee.objects.all().delete()
        AdminUser.objects.all().delete()
        Room.objects.all().delete()

        for r in SEED_ROOMS:
            Room.objects.create(**r, **SEED_ROOM_DEFAULTS)

        return Response({
            'message': (f'Database reset. Removed {counts["bookings"]} booking(s), '
                       f'{counts["employees"]} employee(s), {counts["admins"]} admin(s) and '
                       f'{counts["rooms"]} room(s) — restored {len(SEED_ROOMS)} rooms.'),
            'deleted': counts,
            'rooms_restored': len(SEED_ROOMS),
        })
