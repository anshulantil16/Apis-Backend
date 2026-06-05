from django.db import models
from django.utils import timezone


class EOMEmployee(models.Model):
    USER_TYPE_CHOICES = [
        ('employee', 'Employee'),
        ('manager',  'Manager'),
        ('hod',      'HOD'),
        ('hr',       'HR Admin'),
    ]

    employee_id          = models.CharField(max_length=50, unique=True)
    name                 = models.CharField(max_length=200)
    email                = models.EmailField(blank=True)
    designation          = models.CharField(max_length=100, blank=True)
    department           = models.CharField(max_length=100, blank=True)
    zone                 = models.CharField(max_length=100, blank=True)   # Location / Unit
    reporting_manager_id = models.CharField(max_length=50, blank=True)
    hod_id               = models.CharField(max_length=50, blank=True)
    user_type            = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default='employee')
    is_active            = models.BooleanField(default=True)
    created_at           = models.DateTimeField(auto_now_add=True)
    updated_at           = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.employee_id} — {self.name}"


class EOMOTPToken(models.Model):
    employee   = models.ForeignKey(EOMEmployee, on_delete=models.CASCADE, related_name='otp_tokens')
    otp_code   = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used    = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def is_valid(self):
        return not self.is_used and self.expires_at > timezone.now()


class EOMCycle(models.Model):
    STATUS_CHOICES = [
        ('open',   'Open — Nominations Accepted'),
        ('closed', 'Closed'),
    ]
    MONTH_CHOICES = [
        (1,'January'),(2,'February'),(3,'March'),(4,'April'),
        (5,'May'),(6,'June'),(7,'July'),(8,'August'),
        (9,'September'),(10,'October'),(11,'November'),(12,'December'),
    ]

    name       = models.CharField(max_length=120)
    month      = models.IntegerField(choices=MONTH_CHOICES)
    year       = models.IntegerField()
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default='open')
    created_by = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['month', 'year']
        ordering = ['-year', '-month']

    def __str__(self):
        return self.name


class EOMNomination(models.Model):
    STATUS_CHOICES = [
        ('draft',            'Draft'),
        ('submitted',        'Submitted'),
        ('manager_approved', 'Manager Approved'),
        ('manager_rejected', 'Manager Rejected'),
        ('hod_approved',     'HOD Approved'),
        ('hod_rejected',     'HOD Rejected'),
        ('hr_finalized',     'HR Finalized'),
    ]

    employee = models.ForeignKey(EOMEmployee, on_delete=models.CASCADE, related_name='nominations')
    cycle    = models.ForeignKey(EOMCycle, on_delete=models.CASCADE, related_name='nominations')
    status   = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')

    # ── Employee form fields ─────────────────────────────────────────────────
    track                  = models.CharField(max_length=20, blank=True)
    part_a_achievement     = models.TextField(blank=True)
    smart_specific         = models.TextField(blank=True)
    smart_measurable       = models.TextField(blank=True)
    smart_achievable       = models.TextField(blank=True)
    smart_relevant         = models.TextField(blank=True)
    smart_timebound        = models.TextField(blank=True)
    evidence_1_description = models.TextField(blank=True)
    evidence_1_source      = models.CharField(max_length=300, blank=True)
    evidence_2_description = models.TextField(blank=True)
    evidence_2_source      = models.CharField(max_length=300, blank=True)
    declaration_agreed     = models.BooleanField(default=False)
    signature_name         = models.CharField(max_length=200, blank=True)

    # ── Manager scorecard (Annexure B) ──────────────────────────────────────
    manager_dim1_score              = models.PositiveSmallIntegerField(null=True, blank=True)  # max 50
    manager_dim1_comments           = models.TextField(blank=True)
    manager_dim2_score              = models.PositiveSmallIntegerField(null=True, blank=True)  # max 20
    manager_dim2_comments           = models.TextField(blank=True)
    manager_dim3_score              = models.PositiveSmallIntegerField(null=True, blank=True)  # max 10
    manager_dim3_comments           = models.TextField(blank=True)
    manager_dim4_score              = models.PositiveSmallIntegerField(null=True, blank=True)  # max 10
    manager_dim4_comments           = models.TextField(blank=True)
    manager_sustainability_desc     = models.TextField(blank=True)
    manager_sustainability_bonus    = models.PositiveSmallIntegerField(null=True, blank=True)  # 0 or 5
    manager_sustainability_just     = models.TextField(blank=True)
    manager_recommendation          = models.CharField(max_length=20, blank=True)   # recommend / not_recommend
    manager_panel_name              = models.CharField(max_length=200, blank=True)
    manager_remarks                 = models.TextField(blank=True)
    manager_reviewed_at             = models.DateTimeField(null=True, blank=True)

    # ── HOD review ───────────────────────────────────────────────────────────
    hod_remarks     = models.TextField(blank=True)
    hod_reviewed_at = models.DateTimeField(null=True, blank=True)

    # ── HR finalisation ──────────────────────────────────────────────────────
    hr_remarks      = models.TextField(blank=True)
    hr_finalized_at = models.DateTimeField(null=True, blank=True)
    is_winner       = models.BooleanField(default=False)

    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['employee', 'cycle']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.name} — {self.cycle.name}"
