"""RoomPulse views package.

    auth.py        email OTP login, role resolution (employee/admin/super_admin)
    rooms.py        room CRUD (Super Admin) + live status grid (everyone)
    bookings.py      booking create/approve/reject/cancel, day calendar
    employees.py     Super Admin employee-directory template/upload/list
    admins.py        Super Admin admin-roster management
    analytics.py      utilisation stats for Admin/Super Admin

Re-exported here so urls.py sees one flat namespace.
"""
from .auth import *          # noqa: F401,F403
from .rooms import *         # noqa: F401,F403
from .bookings import *      # noqa: F401,F403
from .employees import *     # noqa: F401,F403
from .admins import *        # noqa: F401,F403
from .analytics import *     # noqa: F401,F403

from .auth import resolve_role, SUPER_ADMIN_EMAIL
