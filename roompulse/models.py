"""RoomPulse — conference room booking & live availability.

Standalone app: no models, tables or imports shared with pms/sales/eom. Owns
four tables (Room, BookingRequest, Employee, AdminUser) and its own URL
namespace.
"""
from django.db import models


class Room(models.Model):
    """A bookable conference room. Managed by Super Admin only."""
    name       = models.CharField(max_length=150)              # "Conference Room - 1"
    label      = models.CharField(max_length=100, blank=True)  # "(Apis)" brand/sub-name
    floor      = models.CharField(max_length=50)                # "1st Floor"
    capacity   = models.IntegerField(default=10)
    amenities  = models.JSONField(default=list, blank=True)     # ["Projector","Video Conf",...]
    color      = models.CharField(max_length=20, default='#6366f1')  # UI accent colour
    is_active  = models.BooleanField(default=True)  # inactive = retired, hidden from booking
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['floor', 'name']

    def __str__(self):
        return f"{self.label} {self.name}".strip()


class BookingRequest(models.Model):
    """One booking request/slot for a room. Employees create Pending requests;
    Admin/Super Admin approve, reject or book directly (auto-approved)."""
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('approved',  'Approved'),
        ('rejected',  'Rejected'),
        ('cancelled', 'Cancelled'),
    ]
    PURPOSE_CHOICES = [
        ('client_meeting',   'Client Meeting'),
        ('internal_meeting', 'Internal Team Meeting'),
        ('interview',        'Interview'),
        ('training',         'Training / Workshop'),
        ('board_meeting',    'Board Meeting'),
        ('presentation',     'Presentation'),
        ('vendor_meeting',   'Vendor Meeting'),
        ('other',            'Other'),
    ]

    # db_constraint=False: same reasoning as pms.WarningLetter.employee — this
    # app's Room/BookingRequest tables are brand new so there's no historical
    # drift here, but keeping FK checks off any table Django manages by string
    # reference costs nothing and avoids ever repeating that MySQL 3780 class
    # of bug if a future migration touches Room's pk type.
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='bookings',
                             db_constraint=False)

    requested_by_name  = models.CharField(max_length=200)
    requested_by_email = models.EmailField()
    department          = models.CharField(max_length=150, blank=True)

    date       = models.DateField(db_index=True)
    start_time = models.TimeField()
    end_time   = models.TimeField()

    purpose        = models.CharField(max_length=30, choices=PURPOSE_CHOICES, default='internal_meeting')
    purpose_detail = models.CharField(max_length=300, blank=True)
    attendees      = models.IntegerField(default=1)

    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    reviewed_by    = models.CharField(max_length=200, blank=True)
    reviewed_at    = models.DateTimeField(null=True, blank=True)
    admin_remarks  = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['date', 'start_time']
        indexes = [
            # Overlap checks and the live-status engine both filter by
            # (room, date, status) first — this is the one index that matters.
            models.Index(fields=['room', 'date', 'status']),
        ]

    def __str__(self):
        return f"{self.room} {self.date} {self.start_time}-{self.end_time} ({self.status})"


class Employee(models.Model):
    """Employee directory, bulk-uploaded by Super Admin via Excel (mirrors the
    SalesIQ/PMS upload pattern). Used to auto-fill booking requester details
    and to power department-level analytics — not an auth mechanism; login
    is still open to any @apisindia.com address per RoomPulse's role rules."""
    employee_code = models.CharField(max_length=50, blank=True, db_index=True)
    name          = models.CharField(max_length=200)
    email         = models.EmailField(unique=True)
    department    = models.CharField(max_length=150, blank=True, db_index=True)
    designation   = models.CharField(max_length=150, blank=True)
    location      = models.CharField(max_length=150, blank=True)
    reporting_manager = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} <{self.email}>"


class AdminUser(models.Model):
    """Email allowlist for the Admin role, managed by Super Admin from the UI
    (unlike SalesIQ's env-var allowlist — RoomPulse expects the roster to
    change often enough that a redeploy per change would be impractical)."""
    email      = models.EmailField(unique=True)
    name       = models.CharField(max_length=200, blank=True)
    added_by   = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['email']

    def __str__(self):
        return self.email
