"""AdminPulse (Django app: roompulse) — room booking + any other resource an
employee needs to request from Admin (stationery, IT equipment, etc.).

The app is still named `roompulse` internally — renaming a Django app means
renaming its migrations' app_label and every `roompulse_*` table already
live on QA/Live, which is far riskier than the cost of an internal name that
no longer matches the product's public name. AdminPulse is the brand;
`roompulse` is the implementation detail, same as how the `pms` app now also
hosts the unrelated Letters Generator — see STRUCTURE.md.

Standalone app: no models, tables or imports shared with pms/sales/eom. Owns
seven tables (Room, BookingRequest, ResourceRequest, SupportTicket,
TicketAttachment, Employee, AdminUser) and its own URL namespace.
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

    # A meeting that finished before its booked end. The room is free from
    # this moment, but the booking stays approved and keeps the times it was
    # booked for -- it did happen, and shortening end_time would quietly
    # rewrite that. `status.effective_end` is what reads this.
    released_at    = models.DateTimeField(null=True, blank=True)
    released_by    = models.CharField(max_length=200, blank=True)
    release_reason = models.CharField(max_length=300, blank=True)

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


class ResourceRequest(models.Model):
    """A request for anything Admin provides that ISN'T a room — stationery,
    IT equipment, furniture, housekeeping, printing, etc.

    Deliberately a separate model from BookingRequest rather than a unified
    polymorphic "Request" table: room bookings are time-slot + conflict-
    checked against a Room; resource requests are quantity + fulfilment
    checked with no time dimension at all. Forcing both shapes into one table
    would mean a pile of nullable fields that only make sense for one type —
    two narrow models are simpler to reason about than one wide one here.

    Status has an extra step rooms don't need: 'fulfilled'. Approving a room
    booking IS the outcome (the room is now yours at that time); approving a
    stationery request only means "yes, get them a stapler" — it isn't done
    until someone actually hands it over.
    """
    STATUS_CHOICES = [
        ('pending',   'Pending'),
        ('approved',  'Approved'),
        ('rejected',  'Rejected'),
        ('fulfilled', 'Fulfilled'),
        ('cancelled', 'Cancelled'),
    ]
    # IT-flavoured categories (equipment, access, software...) live on
    # SupportTicket instead — this list is everything else Admin covers.
    CATEGORY_CHOICES = [
        ('stationery_office_supplies', 'Stationery & Office Supplies'),
        ('housekeeping',               'Housekeeping'),
        ('pantry_refreshments',        'Pantry & Refreshments'),
        ('furniture_seating',          'Furniture & Seating'),
        ('facility_maintenance',       'Facility Maintenance'),
        ('electricity_lighting',       'Electricity & Lighting'),
        ('plumbing',                   'Plumbing'),
        ('security_access',            'Security & Access'),
        ('id_card_employee_badge',     'ID Card / Employee Badge'),
        ('courier_dispatch',           'Courier & Dispatch'),
        ('travel_accommodation',       'Travel & Accommodation'),
        ('cab_transportation',         'Cab / Transportation'),
        ('meeting_room',               'Meeting Room'),
        ('office_equipment',           'Office Equipment'),
        ('printing_photocopy',         'Printing & Photocopy'),
        ('events_administration',      'Events & Administration'),
        ('vendor_service_request',     'Vendor / Service Request'),
        ('workplace_safety',           'Workplace Safety'),
        ('general_administration',     'General Administration'),
        ('other',                      'Other'),
    ]
    URGENCY_CHOICES = [
        ('low', 'Low'), ('normal', 'Normal'), ('urgent', 'Urgent'),
    ]

    requested_by_name  = models.CharField(max_length=200)
    requested_by_email = models.EmailField()
    department          = models.CharField(max_length=150, blank=True)

    category    = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='other', db_index=True)
    item_name   = models.CharField(max_length=200)          # "A4 paper", "Wireless mouse"
    quantity    = models.IntegerField(default=1)
    urgency     = models.CharField(max_length=10, choices=URGENCY_CHOICES, default='normal')
    reason      = models.CharField(max_length=300, blank=True)
    needed_by   = models.DateField(null=True, blank=True)

    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    reviewed_by    = models.CharField(max_length=200, blank=True)
    reviewed_at    = models.DateTimeField(null=True, blank=True)
    admin_remarks  = models.CharField(max_length=300, blank=True)
    fulfilled_by   = models.CharField(max_length=200, blank=True)
    fulfilled_at   = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['status', 'category'])]

    def __str__(self):
        return f"{self.item_name} x{self.quantity} ({self.status})"


class SupportTicket(models.Model):
    """IT support ticket — technical/access issues, as opposed to the
    physical-item ResourceRequest above (stationery, equipment...). Reviewed
    by the IT Support role rather than Admin — see `AdminUser.scope`.

    Status has a pending→approved/rejected gate before work starts, same
    shape as ResourceRequest, plus an in_progress step for the work itself:
    pending (awaiting IT Support triage) → approved → in_progress → closed,
    or rejected at the triage step.
    """
    STATUS_CHOICES = [
        ('pending',     'Pending'),
        ('approved',    'Approved'),
        ('rejected',    'Rejected'),
        ('in_progress', 'In Progress'),
        ('closed',      'Closed'),
        # A requester withdrawing their own ticket was being written as
        # 'rejected' with a remark saying otherwise, so the data could not
        # tell "IT turned this down" from "the user no longer needed it" --
        # two very different things to report on, and the remark was the
        # only evidence, one free-text field away from being overwritten.
        ('cancelled',   'Cancelled'),
    ]
    CATEGORY_CHOICES = [
        ('account_login_access',       'Account / Login Access'),
        ('printer_scanner',            'Printer / Scanner'),
        ('vpn_access',                 'VPN Access'),
        ('system_application_access',  'System / Application Access'),
        ('software_installation',      'Software Installation'),
        ('antivirus_security',         'Antivirus / Security'),
        ('microsoft_365',              'Microsoft 365'),
        ('teams_video_conferencing',   'Teams / Video Conferencing'),
        ('server_storage',             'Server / Storage'),
        ('database_access',            'Database Access'),
        ('mobile_device_support',      'Mobile / Device Support'),
        ('it_asset_request',           'IT Asset Request'),
        ('other',                      'Other'),
    ]
    PRIORITY_CHOICES = [
        ('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('critical', 'Critical'),
    ]

    requested_by_name  = models.CharField(max_length=200)
    requested_by_email = models.EmailField()
    department          = models.CharField(max_length=150, blank=True)

    category    = models.CharField(max_length=30, choices=CATEGORY_CHOICES, default='other', db_index=True)
    priority    = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    subject     = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    related_to  = models.CharField(max_length=100, blank=True)

    status         = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', db_index=True)
    reviewed_by    = models.CharField(max_length=200, blank=True)
    reviewed_at    = models.DateTimeField(null=True, blank=True)
    admin_remarks  = models.CharField(max_length=300, blank=True)

    # Where the work came from.
    #
    # Plenty of IT and admin work is never asked for in writing: a server
    # restarted, a laptop rebuilt for a joiner, a printer fixed because
    # somebody walked over and said so. Counted only what came through the
    # queue, the monthly report understated the team's work by however much
    # of it people happened to raise tickets for -- which is the opposite of
    # what a report is for.
    #
    # Logged work lives in this table rather than its own, so "what did IT do
    # in September" is one query over one set of categories and statuses. A
    # report assembled from two tables drifts apart the first time a field is
    # added to one of them.
    ORIGIN_CHOICES = [
        ('requested', 'Raised by someone'),
        ('logged',    'Logged by IT / Admin'),
    ]
    origin = models.CharField(max_length=20, choices=ORIGIN_CHOICES,
                              default='requested', db_index=True)
    # Who the logged job was for -- a person, a department, "the server room".
    # Free text on purpose: much of this work is for nobody in particular.
    logged_for = models.CharField(max_length=200, blank=True)

    # Who actually did the work, and when it happened. Set when a ticket is
    # closed as well as on a logged job, so one monthly figure covers both.
    # `performed_on` is a date, not the row's timestamp: work is often written
    # up the following morning, and it belongs to the day it was done.
    performed_by_email = models.EmailField(blank=True, db_index=True)
    performed_by_name  = models.CharField(max_length=200, blank=True)
    performed_on       = models.DateField(null=True, blank=True, db_index=True)
    time_spent_minutes = models.PositiveIntegerField(null=True, blank=True)

    # The window actually worked. A duration alone cannot show WHEN: "ninety
    # minutes" reads the same whether it was a Tuesday afternoon or from
    # 23:00 to 00:30 bringing a server back. Someone who works late should be
    # able to point at it, so the hours are recorded and the part of them
    # falling outside office hours is worked out from these rather than
    # claimed. Either end may be blank -- not every job is worth timing.
    worked_from = models.TimeField(null=True, blank=True)
    worked_to   = models.TimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'category']),
            # The monthly report: everything one person did in a date range.
            models.Index(fields=['performed_on', 'performed_by_email']),
        ]

    def __str__(self):
        return f"{self.subject} ({self.status})"


class TicketAttachment(models.Model):
    """One uploaded file on a SupportTicket — a screenshot of the error, a
    log file, etc. A real FileField (MEDIA_ROOT/MEDIA_URL are already wired
    up app-wide — see config/urls.py's media serve route), not just a
    filename string: IT Support needs to actually open what was attached
    when deciding approve/reject, not just see that something was."""
    # db_constraint stays on: these files are evidence on a ticket, and an
    # attachment row whose ticket no longer exists is a file nobody can find
    # their way back to.
    ticket = models.ForeignKey(SupportTicket, on_delete=models.CASCADE,
                               related_name='attachments')
    file          = models.FileField(upload_to='roompulse_tickets/%Y/%m/')
    original_name = models.CharField(max_length=255, blank=True)
    uploaded_at   = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.original_name or self.file.name


class TicketEvent(models.Model):
    """Every move a ticket makes, kept forever.

    The ticket row holds only where it got to: reviewed_by and reviewed_at are
    a single slot, so the moment somebody closed a ticket you could no longer
    see who had approved it, and admin_remarks was overwritten each time. A
    ticket is meant to be the record of what happened -- who asked, who agreed,
    who did the work, when -- and that record has to be a list, not a field
    that the next action overwrites.

    Append-only by intent: rows are written by _log() in views/tickets.py and
    nothing in the app updates or deletes one.
    """

    ACTIONS = [
        ('created',   'Created'),
        ('approved',  'Approved'),
        ('rejected',  'Rejected'),
        ('started',   'Work started'),
        ('closed',    'Closed'),
        ('cancelled', 'Cancelled'),
        ('logged',    'Logged as done'),
    ]

    ticket      = models.ForeignKey(SupportTicket, on_delete=models.CASCADE,
                                    related_name='events')
    action      = models.CharField(max_length=20, choices=ACTIONS)
    from_status = models.CharField(max_length=20, blank=True)
    to_status   = models.CharField(max_length=20, blank=True)
    # Snapshotted, not a foreign key: who did this stays readable even after
    # somebody leaves and their roster row is removed.
    actor_email = models.EmailField(blank=True)
    actor_role  = models.CharField(max_length=20, blank=True)
    remarks     = models.CharField(max_length=300, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        indexes = [models.Index(fields=['ticket', 'created_at'])]

    def __str__(self):
        return f'{self.action} by {self.actor_email or "system"}'


class Employee(models.Model):
    """Employee directory, bulk-uploaded by Super Admin via Excel (mirrors the
    SalesIQ/PMS upload pattern). Used to auto-fill booking requester details
    and to power department-level analytics — not an auth mechanism; login
    is still open to any @apisindia.com address per AdminPulse's role rules.

    `role` is a convenience mirror of AdminUser, not the source of truth —
    resolve_role() in views/auth.py always checks AdminUser directly. This
    field exists purely so the directory can display who has Admin access
    without a second query, and so the bulk upload can GRANT admin access via
    a Role column (see views/employees.py) instead of adding people one at a
    time in the Team tab.
    """
    ROLE_CHOICES = [('employee', 'Employee'), ('admin', 'Admin'), ('it_support', 'IT Support')]

    employee_code = models.CharField(max_length=50, blank=True, db_index=True)
    name          = models.CharField(max_length=200)
    email         = models.EmailField(unique=True)
    department    = models.CharField(max_length=150, blank=True, db_index=True)
    designation   = models.CharField(max_length=150, blank=True)
    location      = models.CharField(max_length=150, blank=True)
    reporting_manager = models.CharField(max_length=200, blank=True)
    role          = models.CharField(max_length=20, choices=ROLE_CHOICES, default='employee')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} <{self.email}>"


class AdminUser(models.Model):
    """Email allowlist for the Admin and IT Support roles, managed by Super
    Admin from the UI (unlike SalesIQ's env-var allowlist — AdminPulse
    expects the roster to change often enough that a redeploy per change
    would be impractical).

    One table for both roles rather than a second allowlist model — same
    shape (email, name, who added them), same Super-Admin-only management
    UI, just scoped by what they can approve: Admin reviews room bookings
    and item requests, IT Support reviews SupportTickets. `resolve_role()`
    in auth.py returns this `scope` value directly as the caller's role.
    """
    SCOPE_CHOICES = [('admin', 'Admin'), ('it_support', 'IT Support')]

    email      = models.EmailField(unique=True)
    name       = models.CharField(max_length=200, blank=True)
    scope      = models.CharField(max_length=20, choices=SCOPE_CHOICES, default='admin')
    added_by   = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['email']

    def __str__(self):
        return self.email
