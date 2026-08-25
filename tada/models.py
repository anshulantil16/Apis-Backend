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
        ('travel_desk', 'Travel Help Desk'),
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

    # A Travelling-Expenses claim settles the Tour Sanction it was approved
    # under: the sanction holds what the trip was estimated to cost and what
    # advance was drawn, the claim holds what it actually cost with bills.
    # Null for a standalone claim (travel that was never pre-sanctioned).
    sanction      = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='claims', limit_choices_to={'request_type': 'tour_sanction'})

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
    BOOKING_MODE_CHOICES = [
        ('self',    'Booked by me — I will claim the fare'),
        ('company', 'Booked by the company — Travel Help Desk'),
    ]
    BOOKING_STATUS_CHOICES = [
        ('not_required', 'Self-booked — nothing for the desk'),
        ('pending',      'Awaiting booking by the Travel Help Desk'),
        ('booked',       'Booked'),
        ('cancelled',    'Booking cancelled'),
    ]

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

    # Ticketing for a single-destination trip. A company booking is paid to the
    # carrier directly, so its fare never belongs in the employee's claim.
    booking_mode      = models.CharField(max_length=10, choices=BOOKING_MODE_CHOICES, default='self')
    booking_status    = models.CharField(max_length=15, choices=BOOKING_STATUS_CHOICES, default='not_required')
    booking_reference = models.CharField(max_length=100, blank=True)   # PNR / ticket no.
    booking_carrier   = models.CharField(max_length=200, blank=True)   # airline / operator
    booking_fare      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    booking_remarks   = models.TextField(blank=True)
    booked_by         = models.CharField(max_length=200, blank=True)
    booked_at         = models.DateTimeField(null=True, blank=True)

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

    # Rejected claims don't tie up the sanction — the employee refiles against it.
    LIVE_CLAIM_STATUSES = ['submitted', 'manager_approved', 'hr_approved',
                           'finance_approved', 'paid']

    @property
    def open_claim(self):
        """The live claim settling this sanction, if any."""
        if self.request_type != 'tour_sanction':
            return None
        return self.claims.filter(status__in=self.LIVE_CLAIM_STATUSES).first()

    @property
    def is_claimable(self):
        """A fully approved trip that has been taken and not yet claimed for."""
        return (self.request_type == 'tour_sanction'
                and self.status in ('hr_approved', 'finance_approved', 'paid')
                and self.open_claim is None)

    @property
    def company_booked_legs(self):
        """Journeys the Travel Help Desk is booking, whatever their state.

        A claim owns no journeys — the tickets belong to the sanction it
        settles, and that is where the approver needs to see them.
        """
        src = self.sanction if (self.request_type == 'travel_expense' and self.sanction) else self
        legs = [l for l in src.legs.all() if l.booking_mode == 'company']
        if legs or src.legs.exists():
            return legs
        return [src] if src.booking_mode == 'company' else []

    @property
    def needs_booking(self):
        """Fully approved, and something still to book."""
        if self.request_type != 'tour_sanction' or self.status not in (
                'hr_approved', 'finance_approved', 'paid'):
            return False
        return any(x.booking_status == 'pending' for x in self.company_booked_legs)

    @property
    def company_borne_fare(self):
        """Fare the company has paid directly — never the employee's to claim."""
        return round(sum(float(x.booking_fare or 0) for x in self.company_booked_legs
                         if x.booking_status == 'booked'), 2)

    @property
    def estimate_heads(self):
        """Per-head estimate keyed by the expense category it maps to, so a
        claim can be shown line by line against what was sanctioned."""
        return {
            'travel': float(self.est_ticket_amount),
            'lodging': float(self.est_lodging_amount),
            'food': float(self.est_food_amount),
            'local_transport': float(self.est_local_amount),
            'misc': float(self.est_misc_amount),
        }

    @property
    def advance_adjusted(self):
        """Advance already drawn against this claim's sanction."""
        return float(self.sanction.advance_amount) if self.sanction else 0.0

    @property
    def net_settlement(self):
        """Positive = still owed to the employee, negative = to recover."""
        return round(float(self.total_claimed) - self.advance_adjusted, 2)


class TravelLeg(models.Model):
    """One stop of a multi-city tour — 'days 1-4 in Delhi, then 5-7 in Kanpur'.

    A leg exists because entitlements are per city grade: Delhi is grade A and
    Kanpur grade C, so costing a mixed trip against a single destination over-
    or under-states the allowance. Each leg carries its own dates, city and the
    travel mode used to *reach* it; the journey home is the request-level
    return ticket.

    A single-destination trip needs no legs at all — the request-level fields
    still describe it, and older requests keep working untouched.
    """
    request          = models.ForeignKey(TravelRequest, on_delete=models.CASCADE, related_name='legs')
    seq              = models.PositiveIntegerField(default=0)   # display order
    from_date        = models.DateField(null=True, blank=True)
    to_date          = models.DateField(null=True, blank=True)
    destination_city = models.CharField(max_length=200, blank=True)
    travel_address   = models.CharField(max_length=500, blank=True)   # where you actually are at this stop
    city_grade       = models.CharField(max_length=1, blank=True)
    purpose          = models.CharField(max_length=500, blank=True)

    # Travel INTO this city (the return home is on the request).
    travel_mode      = models.CharField(max_length=100, blank=True)
    ticket_date      = models.DateField(null=True, blank=True)
    ticket_time_pref = models.CharField(max_length=20, choices=TravelRequest.TIME_PREF_CHOICES, blank=True)
    mode_exception_reason = models.TextField(blank=True)

    # Ticketing for this leg — see TravelRequest for the same fields at trip level.
    booking_mode      = models.CharField(max_length=10, choices=TravelRequest.BOOKING_MODE_CHOICES, default='self')
    booking_status    = models.CharField(max_length=15, choices=TravelRequest.BOOKING_STATUS_CHOICES, default='not_required')
    booking_reference = models.CharField(max_length=100, blank=True)
    booking_carrier   = models.CharField(max_length=200, blank=True)
    booking_fare      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    booking_remarks   = models.TextField(blank=True)
    booked_by         = models.CharField(max_length=200, blank=True)
    booked_at         = models.DateTimeField(null=True, blank=True)

    # Per-leg estimate. Lodging/food/local come from this leg's own city grade.
    est_ticket_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    est_lodging_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    est_food_amount    = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    est_local_amount   = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ['seq', 'from_date']

    def __str__(self):
        return f"Leg {self.seq + 1}: {self.destination_city} ({self.from_date} → {self.to_date})"

    @property
    def days(self):
        if not self.from_date or not self.to_date:
            return None
        d = (self.to_date - self.from_date).days + 1
        return d if d > 0 else None

    @property
    def estimate_heads(self):
        """This stop's estimate keyed by the expense category it maps to, so
        bills can be collected head by head against what it was sanctioned for."""
        return {
            'travel': float(self.est_ticket_amount),
            'lodging': float(self.est_lodging_amount),
            'food': float(self.est_food_amount),
            'local_transport': float(self.est_local_amount),
            'misc': 0.0,          # misc is estimated for the trip, not per stop
        }


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
    # Which stop of the sanctioned trip this bill belongs to. Null for a
    # single-destination trip or a standalone claim. Bills are collected per
    # stop per head so they line up with how the trip was sanctioned — and so
    # an over-run can be traced to the leg that caused it.
    leg             = models.ForeignKey('TravelLeg', null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name='expense_items')
    category        = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    date            = models.DateField(null=True, blank=True)
    # A single bill can cover several days (a hotel folio, a week of meals).
    # Without this the ceiling was compared against a one-day rate and
    # perfectly compliant bills were flagged.
    to_date         = models.DateField(null=True, blank=True)
    description     = models.CharField(max_length=500, blank=True)

    # Who issued the bill, and its reference — a hotel name, an airline and a
    # PNR, a cab operator and an invoice number. Finance reconciles on these.
    vendor          = models.CharField(max_length=200, blank=True)
    reference_no    = models.CharField(max_length=100, blank=True)

    # Lodging only. The stay decides how many nights the ceiling covers, and
    # the times matter: a check-out on the 4th at 06:00 is not a fourth night.
    check_in        = models.DateTimeField(null=True, blank=True)
    check_out       = models.DateTimeField(null=True, blank=True)
    from_location   = models.CharField(max_length=200, blank=True)
    to_location     = models.CharField(max_length=200, blank=True)
    mode            = models.CharField(max_length=100, blank=True)
    km              = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    claimed_amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    approved_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    bill            = models.FileField(upload_to='tada_bills/', null=True, blank=True)
    gst_verified    = models.BooleanField(default=False)
    policy_cap      = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # How that ceiling was arrived at, so a row can show its own arithmetic
    # instead of an unexplained total: 3 nights, 5 days, 48 km.
    cap_units       = models.PositiveIntegerField(default=1)
    cap_basis       = models.CharField(max_length=10, blank=True)     # night / day
    policy_flag     = models.CharField(max_length=400, blank=True)   # warning if over policy
    created_at      = models.DateTimeField(auto_now_add=True)

    @property
    def nights(self):
        """Nights this lodging bill covers, from the actual stay."""
        if not (self.check_in and self.check_out):
            return None
        n = (self.check_out.date() - self.check_in.date()).days
        return n if n > 0 else 1          # same-day check-out is still one night

    @property
    def days_covered(self):
        """Days a bill spans — one unless it carries an end date."""
        if not self.date:
            return 1
        if not self.to_date:
            return 1
        d = (self.to_date - self.date).days + 1
        return d if d > 0 else 1

    @property
    def cap_explained(self):
        """'Rs 2,800 x 3 nights' - the ceiling with its working shown."""
        if self.policy_cap is None or not self.cap_basis or not self.cap_units:
            return None
        rate = float(self.policy_cap) / self.cap_units
        unit = self.cap_basis + ('' if self.cap_units == 1 else 's')
        return 'Rs %s x %d %s' % (format(rate, ',.0f'), self.cap_units, unit)

    @property
    def per_night(self):
        """Nightly rate actually claimed — the figure the stay cap applies to."""
        n = self.nights
        return round(float(self.claimed_amount) / n, 2) if n else None


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
    """Audit trail of every workflow action.

    Approving a tour programme is a judgement, not a rubber stamp, so the
    manager and HR record *why* alongside the fact: what the employee was
    briefed, what they think of the advance, and — when the request breaks a
    policy limit — the justification for letting it through anyway. Kept here
    rather than on the request so each approver's answers stand on their own.
    """
    request   = models.ForeignKey(TravelRequest, on_delete=models.CASCADE, related_name='logs')
    stage     = models.CharField(max_length=20)   # employee / manager / hr / finance
    action    = models.CharField(max_length=20)   # submitted / approved / rejected / paid
    by_name   = models.CharField(max_length=200, blank=True)
    remarks   = models.TextField(blank=True)

    # Captured on approval of a tour programme (see ActionView).
    briefing                = models.TextField(blank=True)   # what the employee was briefed
    advance_remarks         = models.TextField(blank=True)   # view on the advance requested
    deviation_justification = models.TextField(blank=True)   # only when policy flags exist

    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['timestamp']
