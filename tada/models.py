"""
TA/DA (Travel & Daily Allowance) Portal — standalone module.
Own tables (tada_*), own user directory, own OTP login, own workflow.
Workflow: Employee → Manager → HR → Finance.
"""
from django.db import models
from django.utils import timezone


class TadaUser(models.Model):
    """TA/DA portal user directory (imported separately from PMS)."""
    ROLE_CHOICES = [
        ('employee', 'Employee'),
        ('manager', 'Manager'),
        ('hr', 'HR'),
        ('finance', 'Finance'),
        ('admin', 'Admin'),
    ]
    employee_id          = models.CharField(max_length=50, unique=True)
    name                 = models.CharField(max_length=200)
    email                = models.EmailField(blank=True)
    designation          = models.CharField(max_length=200, blank=True)
    department           = models.CharField(max_length=200, blank=True)
    level                = models.CharField(max_length=10, blank=True)   # M1..M7, E1..E4
    grade                = models.CharField(max_length=20, blank=True)   # optional cadre-grade
    hq_city              = models.CharField(max_length=200, blank=True)
    reporting_manager_id = models.CharField(max_length=50, blank=True)
    role                 = models.CharField(max_length=20, choices=ROLE_CHOICES, default='employee')
    is_active            = models.BooleanField(default=True)
    vehicle_rc_no        = models.CharField(max_length=50, blank=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.employee_id})"


class TadaOTP(models.Model):
    """One-time password for TA/DA login."""
    user       = models.ForeignKey(TadaUser, on_delete=models.CASCADE, related_name='otps')
    code       = models.CharField(max_length=6)
    is_used    = models.BooleanField(default=False)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)


class TravelRequest(models.Model):
    """A single TA/DA request. Three types share this table."""
    TYPE_CHOICES = [
        ('tour_sanction',  'Tour Programme Sanction'),
        ('travel_expense', 'Travelling Expenses'),
        ('local_travel',   'Local Travel'),
    ]
    STATUS_CHOICES = [
        ('draft',            'Draft'),
        ('submitted',        'Submitted · Pending Manager'),
        ('manager_approved', 'Manager Approved · Pending HR'),
        ('manager_rejected', 'Rejected by Manager'),
        ('hr_approved',      'HR Approved · Pending Finance'),
        ('hr_rejected',      'Rejected by HR'),
        ('finance_approved', 'Finance Approved'),
        ('finance_rejected', 'Rejected by Finance'),
        ('paid',             'Paid / Settled'),
    ]
    user          = models.ForeignKey(TadaUser, on_delete=models.CASCADE, related_name='requests')
    request_type  = models.CharField(max_length=20, choices=TYPE_CHOICES)
    status        = models.CharField(max_length=25, choices=STATUS_CHOICES, default='draft')

    # ── Common / trip details ────────────────────────────────────────────────
    purpose          = models.TextField(blank=True)
    from_date        = models.DateField(null=True, blank=True)
    to_date          = models.DateField(null=True, blank=True)
    travel_address   = models.CharField(max_length=500, blank=True)
    destination_city = models.CharField(max_length=200, blank=True)
    city_grade       = models.CharField(max_length=1, blank=True)   # A / B / C
    contact_number   = models.CharField(max_length=20, blank=True)
    sanction_number  = models.CharField(max_length=100, blank=True)
    estimate_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)  # total of the est_* lines below
    travel_mode      = models.CharField(max_length=100, blank=True)
    local_travel_type = models.CharField(max_length=100, blank=True)   # Outdoor Duty, etc.

    # ── Ticket booking preference for the chosen travel mode ──────────────────
    TIME_PREF_CHOICES = [
        ('early_morning', 'Early Morning (12 AM – 6 AM)'),
        ('morning',       'Morning (6 AM – 12 PM)'),
        ('afternoon',     'Afternoon (12 PM – 4 PM)'),
        ('evening',       'Evening (4 PM – 8 PM)'),
        ('night',         'Night (8 PM – 12 AM)'),
    ]
    travel_mode_date      = models.DateField(null=True, blank=True)   # onward ticket date
    travel_mode_time_pref = models.CharField(max_length=20, choices=TIME_PREF_CHOICES, blank=True)
    return_mode_date      = models.DateField(null=True, blank=True)   # return ticket date
    return_mode_time_pref = models.CharField(max_length=20, choices=TIME_PREF_CHOICES, blank=True)

    # ── Pre-travel estimate, broken down by head ─────────────────────────────
    # Lodging / food / local are seeded from the policy matrices (band × city
    # grade × days); ticket and misc are entered by the employee. estimate_amount
    # above holds the total that goes for approval.
    est_ticket_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    est_lodging_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    est_food_amount    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    est_local_amount   = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    est_misc_amount    = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Advance drawn before departure; settled against the actual claim later.
    advance_amount   = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Why a travel mode outside the level's entitlement was chosen (emergency,
    # no train available, etc.). Required by the form when the mode is an
    # exception; travels with the request for the approver to weigh.
    mode_exception_reason = models.TextField(blank=True)

    # Policy breaches recorded at submission so the approver sees them (newline-separated).
    policy_flags     = models.TextField(blank=True)

    # ── Totals ───────────────────────────────────────────────────────────────
    total_claimed  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_approved = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # ── Workflow remarks & timestamps ────────────────────────────────────────
    manager_remarks = models.TextField(blank=True)
    hr_remarks      = models.TextField(blank=True)
    finance_remarks = models.TextField(blank=True)
    created_at        = models.DateTimeField(auto_now_add=True)
    submitted_at      = models.DateTimeField(null=True, blank=True)
    manager_action_at = models.DateTimeField(null=True, blank=True)
    hr_action_at      = models.DateTimeField(null=True, blank=True)
    finance_action_at = models.DateTimeField(null=True, blank=True)
    updated_at        = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_request_type_display()} · {self.user.name} · {self.status}"

    @property
    def number_of_days(self):
        if not self.from_date or not self.to_date:
            return None
        return (self.to_date - self.from_date).days + 1


class ExpenseItem(models.Model):
    """Line item for a Travelling-Expenses claim (tabbed categories) with a bill."""
    CATEGORY_CHOICES = [
        ('travel',          'Travel Details'),
        ('lodging',         'Lodging'),
        ('food',            'Food / DA'),
        ('local_transport', 'Local Transport'),
        ('misc',            'Miscellaneous'),
    ]
    request         = models.ForeignKey(TravelRequest, on_delete=models.CASCADE, related_name='expense_items')
    category        = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    date            = models.DateField(null=True, blank=True)
    description     = models.CharField(max_length=500, blank=True)
    from_location   = models.CharField(max_length=200, blank=True)
    to_location     = models.CharField(max_length=200, blank=True)
    mode            = models.CharField(max_length=100, blank=True)
    km              = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    claimed_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    bill            = models.FileField(upload_to='tada_bills/', null=True, blank=True)
    gst_verified    = models.BooleanField(default=False)
    policy_cap      = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    policy_flag     = models.CharField(max_length=400, blank=True)   # warning if over policy
    created_at      = models.DateTimeField(auto_now_add=True)


class LocalTravelItem(models.Model):
    """A single journey row inside a Local-Travel request."""
    request       = models.ForeignKey(TravelRequest, on_delete=models.CASCADE, related_name='local_items')
    date          = models.DateField(null=True, blank=True)
    purpose       = models.CharField(max_length=300, blank=True)
    from_location = models.CharField(max_length=200, blank=True)
    to_location   = models.CharField(max_length=200, blank=True)
    mode          = models.CharField(max_length=100, blank=True)   # Metro, Cab, Two-Wheeler...
    km            = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    amount        = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    policy_flag   = models.CharField(max_length=400, blank=True)


class ApprovalLog(models.Model):
    """Audit trail of every workflow action."""
    request   = models.ForeignKey(TravelRequest, on_delete=models.CASCADE, related_name='logs')
    stage     = models.CharField(max_length=20)   # employee / manager / hr / finance
    action    = models.CharField(max_length=20)   # submitted / approved / rejected / paid
    by_name   = models.CharField(max_length=200, blank=True)
    remarks   = models.TextField(blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['timestamp']
