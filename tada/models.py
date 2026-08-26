"""
TA/DA (Travel & Daily Allowance) Portal — standalone module.
Own tables (tada_*), own user directory, own OTP login, own workflow.
Workflow: Employee → Manager → P&C (HR) → Finance.
"""
from django.db import models
from django.utils import timezone


class TadaUser(models.Model):
    """TA/DA portal user directory (imported separately from PMS)."""
    ROLE_CHOICES = [
        ('employee', 'Employee'),
        ('manager', 'Manager'),
        ('hr', 'P&C (HR)'),
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
        ('manager_approved', 'Manager Approved · Pending P&C (HR)'),
        ('manager_rejected', 'Rejected by Manager'),
        ('hr_approved',      'P&C (HR) Approved · Pending Finance'),
        ('hr_rejected',      'Rejected by P&C (HR)'),
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
    # Where the outbound journey actually starts — not assumed to be the
    # employee's HQ, since a trip can begin from wherever they already are.
    # Required at submission (see CreateTourSanctionView) so the approver and
    # the Travel Help Desk both know the route, not just the destination.
    from_city        = models.CharField(max_length=200, blank=True)
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
        ('not_required',   'Self-booked — nothing for the desk'),
        ('pending',        'Awaiting booking by the Travel Help Desk'),
        ('options_sent',   'Options sent — awaiting the employee\'s choice'),
        ('confirmed',      'Employee has chosen — awaiting ticketing'),
        ('booked',         'Booked'),
        ('cancelled',      'Booking cancelled'),
    ]

    # Most tours come back, so that is the default; a one-way leg (relocation,
    # onward posting) is the exception and has to be said out loud.
    TRIP_TYPE_CHOICES = [
        ('round_trip', 'To & Fro'),
        ('one_way',    'One Way'),
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
    # The way home can be a different mode from the way out, and it is checked
    # against the same class entitlement, so it is asked for separately.
    return_travel_mode    = models.CharField(max_length=100, blank=True)
    trip_type             = models.CharField(max_length=12, choices=TRIP_TYPE_CHOICES, default='round_trip')

    # ── Who is travelling, as the carrier needs it ───────────────────────────
    # Only the employee knows the spelling on their Aadhaar card, the number
    # they will actually answer while away, and their age. A ticket raised on a
    # guess is a ticket that fails at the counter, so these are asked for
    # whenever the company is doing the booking.
    traveller_name    = models.CharField(max_length=200, blank=True)   # exactly as per Aadhaar
    traveller_age     = models.PositiveIntegerField(null=True, blank=True)

    # ── Ticketing for the journey home ───────────────────────────────────────
    # Its own record because it is its own ticket: a separate PNR, carrier and
    # fare, and it can be booked or fail to be booked independently of the
    # outbound one.
    return_booking_mode      = models.CharField(max_length=10, choices=BOOKING_MODE_CHOICES, default='self')
    return_booking_status    = models.CharField(max_length=15, choices=BOOKING_STATUS_CHOICES, default='not_required')
    return_booking_reference = models.CharField(max_length=100, blank=True)
    return_booking_carrier   = models.CharField(max_length=200, blank=True)
    return_booking_fare      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    return_booking_remarks   = models.TextField(blank=True)
    return_booked_by         = models.CharField(max_length=200, blank=True)
    return_booked_at         = models.DateTimeField(null=True, blank=True)
    return_booking_ticket    = models.FileField(upload_to='tada_tickets/', null=True, blank=True)

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
    booking_ticket    = models.FileField(upload_to='tada_tickets/', null=True, blank=True)   # the actual ticket, as proof

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

    # A single-destination trip is booked as itself, not as a leg — this is
    # what BookingOption keys against for it.
    journey_key = 'trip'

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
        if not legs and not src.legs.exists() and src.booking_mode == 'company':
            legs = [src]
        # The way home is a ticket like any other, and was being left off this
        # list entirely - which is how a company-booked trip reached the desk
        # with nothing raised for the return.
        if src.trip_type == 'round_trip' and src.return_booking_mode == 'company':
            legs = legs + [ReturnJourney(src)]
        return legs

    @property
    def needs_booking(self):
        """Fully approved, and something still to book.

        Anything short of booked or cancelled is still work for someone —
        raising options, waiting on the employee, or ticketing what they chose.
        """
        if self.request_type != 'tour_sanction' or self.status not in (
                'hr_approved', 'finance_approved', 'paid'):
            return False
        return any(x.booking_status not in ('not_required', 'booked', 'cancelled')
                   for x in self.company_booked_legs)

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


class ReturnJourney:
    """The trip home, presented like a leg.

    It is a separate ticket - its own PNR, carrier and fare, bookable or
    unbookable on its own - but it lives in columns on the request rather than
    in a row of its own, because it is not a *stop*: giving it a TravelLeg would
    add a phantom destination to the estimate, which is costed per city grade.
    This wrapper bridges the two shapes, including writes, so the desk endpoint
    records a return booking through exactly the same code path as an outbound.
    """
    seq = 'return'
    is_return = True
    journey_key = 'return'

    def __init__(self, request):
        self._r = request

    def __repr__(self):
        return f'<ReturnJourney of request {self._r.id}>'

    @property
    def destination_city(self):
        return self._r.user.hq_city or 'base'

    @property
    def travel_mode(self):
        return self._r.return_travel_mode

    @property
    def ticket_date(self):
        return self._r.return_mode_date

    @property
    def ticket_time_pref(self):
        return self._r.return_mode_time_pref

    def get_ticket_time_pref_display(self):
        return dict(TravelRequest.TIME_PREF_CHOICES).get(self._r.return_mode_time_pref, '')

    # The estimate is not split between outbound and return, so the return
    # carries none of it rather than double-counting the ticket line.
    est_ticket_amount = 0

    def save(self, *a, **kw):
        self._r.save()


def _mirror(name):
    """booking_x on the wrapper is return_booking_x on the request."""
    src = 'return_booked_' + name[7:] if name.startswith('booked_') else 'return_' + name
    return property(lambda self: getattr(self._r, src),
                    lambda self, v: setattr(self._r, src, v))


for _f in ('booking_mode', 'booking_status', 'booking_reference', 'booking_carrier',
           'booking_fare', 'booking_remarks', 'booked_by', 'booked_at', 'booking_ticket'):
    setattr(ReturnJourney, _f, _mirror(_f))


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
    # Where THIS stop's journey starts — usually the previous stop, but asked
    # explicitly rather than assumed, since a leg can be reached from
    # somewhere off-itinerary too. Required (see CreateTourSanctionView).
    from_city        = models.CharField(max_length=200, blank=True)
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
    booking_ticket    = models.FileField(upload_to='tada_tickets/', null=True, blank=True)

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
    def journey_key(self):
        return str(self.seq)

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


class BookingOption(models.Model):
    """One flight or train the desk found for a journey, offered to the employee.

    A journey can have several viable options (three flights at different
    times, a train and a flight), and the desk should not be the one guessing
    which the employee wants. The desk lists what is available here; the
    employee picks one (see BookingSelectView); only then does the desk buy it
    and record the real PNR against the journey itself.

    Keyed by `journey_key` (the request's own 'trip', a leg's seq as a string,
    or 'return') rather than a foreign key to a specific model, because a
    journey can be any of TravelRequest, TravelLeg or the virtual
    ReturnJourney — see BookingActionView for how a journey_key resolves back
    to one of them.
    """
    request     = models.ForeignKey(TravelRequest, on_delete=models.CASCADE, related_name='booking_options')
    journey_key = models.CharField(max_length=20)
    seq         = models.PositiveIntegerField(default=0)   # display order among this journey's options

    mode    = models.CharField(max_length=100, blank=True)   # Flight / Train / Bus
    carrier = models.CharField(max_length=200, blank=True)   # airline or train name
    detail  = models.CharField(max_length=200, blank=True)   # flight/train number, route detail
    date    = models.DateField(null=True, blank=True)
    time    = models.CharField(max_length=20, blank=True)     # "14:30" — free text, the desk copies it off a booking site
    amount  = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    remarks = models.TextField(blank=True)

    is_selected = models.BooleanField(default=False)
    added_by    = models.CharField(max_length=200, blank=True)
    created_at  = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['journey_key', 'seq']

    def __str__(self):
        return f"{self.mode} {self.carrier} {self.detail} · {self.get_time_label()}"

    def get_time_label(self):
        parts = [str(self.date) if self.date else '', self.time or '']
        return ' '.join(p for p in parts if p)


class StayPlan(models.Model):
    """Where the employee intends to stay, night by night, on a tour programme.

    Pre-travel and separate from ExpenseItem's lodging rows, which are the
    actual folio filed afterwards. An approver releasing a lodging estimate and
    an advance against it was previously told only a rupee figure; this is the
    stay that figure is for, so the two can be judged against each other.

    Kept as its own rows rather than columns on the request because a trip has
    as many stays as it has stops - and sometimes more than one at a stop, if
    the employee changes hotel mid-way.
    """
    request    = models.ForeignKey(TravelRequest, on_delete=models.CASCADE, related_name='stays')
    seq        = models.PositiveIntegerField(default=0)   # display order
    # Which stop this stay belongs to, when the trip was broken down by city.
    # Null on a single-destination trip, which has no legs at all.
    leg_seq    = models.PositiveIntegerField(null=True, blank=True)

    location   = models.CharField(max_length=300)   # hotel / area / city, as the employee knows it
    check_in   = models.DateField(null=True, blank=True)
    check_out  = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['seq', 'check_in']

    def __str__(self):
        return f"{self.location} ({self.check_in} to {self.check_out})"

    @property
    def nights(self):
        """Nights this stay covers - checking in and out the same day is 0."""
        if not (self.check_in and self.check_out):
            return None
        return max(0, (self.check_out - self.check_in).days)


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

    Approving a tour programme is a judgement, not a rubber stamp, so each
    approver records *why* alongside the fact. The two stages are asked
    different questions, because they are answering different ones: the manager
    briefed the employee and owns the deviation call, while P&C (HR) is
    endorsing the tour itself. Kept here rather than on the request so each
    approver's answers stand on their own.
    """
    request   = models.ForeignKey(TravelRequest, on_delete=models.CASCADE, related_name='logs')
    stage     = models.CharField(max_length=20)   # employee / manager / hr / finance
    action    = models.CharField(max_length=20)   # submitted / approved / rejected / paid
    by_name   = models.CharField(max_length=200, blank=True)
    remarks   = models.TextField(blank=True)

    # Captured on approval of a tour programme (see ActionView).
    briefing                = models.TextField(blank=True)   # manager: what the employee was briefed
    tour_justification      = models.TextField(blank=True)   # P&C (HR): why the tour is justified
    advance_remarks         = models.TextField(blank=True)   # both: view on the advance requested
    deviation_justification = models.TextField(blank=True)   # manager, only when policy flags exist

    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['timestamp']
