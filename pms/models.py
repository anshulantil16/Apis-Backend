"""
PMS - Performance Management System
Complete HR data model with all employee fields
"""
from django.db import models
from django.utils import timezone


GRADE_META = {
    'A+': {'label': 'Exceptional',       'inc_min': 12, 'inc_max': 15, 'promo_pct': 5, 'color': '#059669'},
    'A':  {'label': 'Outstanding',        'inc_min': 10, 'inc_max': 12, 'promo_pct': 4, 'color': '#0284c7'},
    'B+': {'label': 'Exceeds Target',     'inc_min': 7,  'inc_max': 10, 'promo_pct': 3, 'color': '#7c3aed'},
    'B':  {'label': 'Meets Target',       'inc_min': 4,  'inc_max': 7,  'promo_pct': 2, 'color': '#d97706'},
    'C':  {'label': 'Near Target',        'inc_min': 0,  'inc_max': 4,  'promo_pct': 0, 'color': '#ea580c'},
    'D':  {'label': 'Needs Improvement',  'inc_min': 2,  'inc_max': 2,  'promo_pct': 0, 'color': '#dc2626'},
}

# ── Merit-Increment matrix per PMS Policy (APIS India, v1 Apr 2026) ────────────
# Fixed increment by Performance Grade × Cadre-group (no ranges).
#   staff1 = O1–O5, M1, M2, M3            → Merit Increment %
#   staff2 = M4, M5, M6, C1, C2, C3       → Merit Increment %
#   worker = W1–W4                        → FIXED monthly amount (₹), annual = ×12
#   special= C4, C5, D                    → MD's decision / Director prerogative (no auto %)
# promo_pct/worker_promo_monthly apply only when the employee is promoted.
INCREMENT_MATRIX = {
    'A+': {'staff1': 14, 'staff2': 10, 'worker_monthly': 800, 'worker_promo_monthly': 400, 'promotion_pct': 5, 'sustained_pct': 1.0},
    'A':  {'staff1': 12, 'staff2': 9,  'worker_monthly': 600, 'worker_promo_monthly': 300, 'promotion_pct': 4, 'sustained_pct': 1.0},
    'B+': {'staff1': 10, 'staff2': 8,  'worker_monthly': 400, 'worker_promo_monthly': 200, 'promotion_pct': 3, 'sustained_pct': 0.5},
    'B':  {'staff1': 8,  'staff2': 7,  'worker_monthly': 200, 'worker_promo_monthly': 100, 'promotion_pct': 2, 'sustained_pct': 0.5},
    'C':  {'staff1': 4,  'staff2': 3,  'worker_monthly': 100, 'worker_promo_monthly': 0,   'promotion_pct': 0, 'sustained_pct': 0.0},
    'D':  {'staff1': 0,  'staff2': 0,  'worker_monthly': 0,   'worker_promo_monthly': 0,   'promotion_pct': 0, 'sustained_pct': 0.0},
}


# Cadre/Band-wise SPECIAL REWARD range (₹) per policy. C/D = discretionary (Director/MD).
SPECIAL_REWARD_RANGE = {
    'M': (25000, 50000),
    'O': (15000, 25000),
    'W': (6000, 15000),
}


def _grade_code(band, cadre):
    """Find the cadre-grade code (e.g. 'M3', 'O5', 'W1', 'C2') from the band/cadre fields."""
    import re
    candidates = [(band or '').strip().upper(), (cadre or '').strip().upper()]
    for v in candidates:
        v2 = v.replace(' ', '')
        if re.match(r'^[WOMCD]\d', v2):
            return v2
    for v in candidates:
        if v and v[0] in 'WOMCD':
            return v
    return ''


def increment_group(band, cadre):
    """Return 'worker' | 'staff1' | 'staff2' | 'special' for the merit-increment table."""
    code = _grade_code(band, cadre)
    if not code:
        return 'staff1'
    letter = code[0]
    digits = ''.join(ch for ch in code if ch.isdigit())
    num = int(digits) if digits else 0
    if letter == 'W':
        return 'worker'
    if letter == 'O':
        return 'staff1'
    if letter == 'M':
        return 'staff1' if num <= 3 else 'staff2'
    if letter == 'C':
        return 'staff2' if 1 <= num <= 3 else 'special'   # C4, C5 → special
    if letter == 'D':
        return 'special'
    return 'staff1'


class PMSEmployee(models.Model):
    # ── Identity & Personal ───────────────────────────────────────────────────
    employee_id              = models.CharField(max_length=50, unique=True)
    name                     = models.CharField(max_length=200)
    gender                   = models.CharField(max_length=20, blank=True)
    qualification            = models.CharField(max_length=200, blank=True)
    date_of_birth            = models.DateField(null=True, blank=True)
    date_of_joining          = models.DateField(null=True, blank=True)

    # ── Designation & Organization ────────────────────────────────────────────
    designation              = models.CharField(max_length=200, blank=True)
    new_designation          = models.CharField(max_length=200, blank=True)
    new_designation_type     = models.CharField(max_length=100, blank=True)  # STAT/MANAGER/etc
    cadre                    = models.CharField(max_length=50, blank=True)   # M1, M2, O1, W1
    band                     = models.CharField(max_length=10, blank=True)   # D/C/M/O/W
    level                    = models.CharField(max_length=50, blank=True)   # Job level
    department               = models.CharField(max_length=200, blank=True)
    business                 = models.CharField(max_length=200, blank=True)
    location                 = models.CharField(max_length=200, blank=True)
    payroll_location         = models.CharField(max_length=200, blank=True)
    new_operational_location = models.CharField(max_length=200, blank=True)
    sub_category             = models.CharField(max_length=200, blank=True)
    cost_centre              = models.CharField(max_length=100, blank=True)
    category                 = models.CharField(max_length=100, blank=True)
    hq_location              = models.CharField(max_length=200, blank=True)

    # ── Reporting ─────────────────────────────────────────────────────────────
    reporting_manager        = models.CharField(max_length=200, blank=True)
    reporting_manager_id     = models.CharField(max_length=50, blank=True)
    hod_name                 = models.CharField(max_length=200, blank=True)
    hod_id                   = models.CharField(max_length=50, blank=True)

    # ── CTC History ───────────────────────────────────────────────────────────
    fy_2223_ctc              = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fy_2324_ctc              = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    fy_2425_ctc              = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    current_ctc              = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    variable_pay             = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)  # Current Variable Pay (from import)

    # ── CTC Growth %  ──────────────────────────────────────────────────────────
    fy_2223_growth_pct       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fy_2324_growth_pct       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fy_2425_growth_pct       = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # ── Performance Score ─────────────────────────────────────────────────────
    # Single final score is imported directly; the grade is derived from it.
    final_score_value        = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    # Legacy per-rater scores (kept for backward-compat; no longer used in calc)
    emp_score                = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    manager_score            = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    hod_score                = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # ── Prior year GRADES for trend ───────────────────────────────────────────
    fy_2223_grade            = models.CharField(max_length=5, blank=True)
    fy_2324_grade            = models.CharField(max_length=5, blank=True)
    fy_2425_grade            = models.CharField(max_length=5, blank=True)

    # ── Promotion History ─────────────────────────────────────────────────────
    last_promotion_year      = models.IntegerField(null=True, blank=True)

    # ── Management Score & Overrides ──────────────────────────────────────────
    management_score         = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    override_increment_pct   = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    override_grade           = models.CharField(max_length=5, blank=True)
    salary_correction        = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # ── Career Actions ────────────────────────────────────────────────────────
    promoted                 = models.BooleanField(default=False)
    promotion_pct            = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    redesignation            = models.BooleanField(default=False)
    on_time_reward           = models.BooleanField(default=False)
    reward_amount            = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sustained_performance    = models.BooleanField(default=False)  # checkpoint flag; adds grade-based sustained %
    management_discretion_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    promotion_readiness      = models.CharField(max_length=20, blank=True)  # ready_now/1_year/2_years/not_ready

    # ── Remarks & Notes ───────────────────────────────────────────────────────
    manager_remarks          = models.TextField(blank=True)
    hod_remarks              = models.TextField(blank=True)
    notes                    = models.TextField(blank=True)

    # ── Metadata ──────────────────────────────────────────────────────────────
    fiscal_year              = models.CharField(max_length=20, blank=True, default='2025-26')
    created_at               = models.DateTimeField(auto_now_add=True)
    updated_at               = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['department', 'name']

    def __str__(self):
        return f"{self.name} ({self.employee_id})"

    # ── Calculated Properties ─────────────────────────────────────────────────
    @property
    def final_score(self):
        """Final score is imported directly (no weighting/formula)."""
        return round(float(self.final_score_value or 0), 2)

    @property
    def auto_grade(self):
        s = self.final_score
        if s >= 106: return 'A+'
        if s >= 95:  return 'A'
        if s >= 85:  return 'B+'
        if s >= 65:  return 'B'
        if s >= 51:  return 'C'
        return 'D'

    @property
    def effective_grade(self):
        return self.override_grade or self.auto_grade

    @property
    def grade_config(self):
        return GRADE_META.get(self.effective_grade, GRADE_META['B'])

    @property
    def increment_group(self):
        """Which merit-increment column applies: worker / staff1 / staff2 / special."""
        return increment_group(self.band, self.cadre)

    @property
    def band_letter(self):
        code = _grade_code(self.band, self.cadre)
        return code[0] if code else ''

    @property
    def special_reward_range(self):
        """Suggested special-reward ₹ range for this band (None = Director/MD discretion)."""
        return SPECIAL_REWARD_RANGE.get(self.band_letter)

    @property
    def is_worker(self):
        return self.increment_group == 'worker'

    @property
    def increment_amount(self):
        """Annual increment amount (₹). Staff = % of CTC; Workers = fixed monthly ×12."""
        if self.override_increment_pct is not None:
            return round(float(self.current_ctc) * float(self.override_increment_pct) / 100, 2)
        row = INCREMENT_MATRIX.get(self.effective_grade, INCREMENT_MATRIX['D'])
        grp = self.increment_group
        if grp == 'worker':
            return round(row['worker_monthly'] * 12, 2)
        if grp == 'special':
            return 0.0   # C4/C5/D — management discretion, no auto increment
        pct = row['staff2'] if grp == 'staff2' else row['staff1']
        return round(float(self.current_ctc) * pct / 100, 2)

    @property
    def effective_increment_pct(self):
        """Effective increment as % of current CTC (derived from the amount, works for workers too)."""
        cur = float(self.current_ctc) or 0
        return round(self.increment_amount / cur * 100, 2) if cur else 0.0

    @property
    def promotion_amount(self):
        if not self.promoted:
            return 0
        row = INCREMENT_MATRIX.get(self.effective_grade, INCREMENT_MATRIX['D'])
        if self.increment_group == 'worker':
            # Worker: fixed promotion amount (₹400/300/200/100/0/0 per month × 12)
            return round(row['worker_promo_monthly'] * 12, 2)
        # Staff: promotion % strictly from the policy table (A+5 / A4 / B+3 / B2 / C0 / D0)
        return round(float(self.current_ctc) * row['promotion_pct'] / 100, 2)

    @property
    def effective_promotion_pct(self):
        """Promotion increase as % of current CTC (0 if not promoted; derived for workers)."""
        if not self.promoted:
            return 0.0
        cur = float(self.current_ctc) or 0
        return round(self.promotion_amount / cur * 100, 2) if cur else 0.0

    @property
    def management_discretion_amount(self):
        return round(float(self.current_ctc) * float(self.management_discretion_pct) / 100, 2)

    @property
    def sustained_pct(self):
        """Grade-based sustained-performance % (only when the sustained checkpoint is met)."""
        if not self.sustained_performance:
            return 0.0
        row = INCREMENT_MATRIX.get(self.effective_grade, INCREMENT_MATRIX['D'])
        return float(row['sustained_pct'])

    @property
    def sustained_amount(self):
        return round(float(self.current_ctc) * self.sustained_pct / 100, 2)

    @property
    def reward_payout(self):
        """Special (one-time) reward ₹ — ENFORCED: capped at the band's max range (M/O/W).
        C/D bands have no fixed range (Director/MD discretion) so any amount is allowed."""
        if not self.on_time_reward:
            return 0.0
        amt = float(self.reward_amount or 0)
        rng = self.special_reward_range
        if rng:
            return min(max(amt, 0.0), float(rng[1]))
        return max(amt, 0.0)

    @property
    def salary_correction_allowed(self):
        """Policy notes 1 & 3: correction is allowed ONLY for A+/A/B+/B grades and NOT when promoted."""
        return (not self.promoted) and self.effective_grade in ('A+', 'A', 'B+', 'B')

    @property
    def salary_correction_amount(self):
        """ENFORCED: correction applies only when policy allows it (A+/A/B+/B & not promoted)."""
        if not self.salary_correction_allowed:
            return 0.0
        return float(self.salary_correction or 0)

    @property
    def merit_eligible(self):
        """Policy eligibility: joined on/before 01-Oct (prior FY). Informational — management may override."""
        if not self.date_of_joining:
            return True
        from datetime import date
        return self.date_of_joining <= date(2025, 10, 1)

    @property
    def new_ctc(self):
        """Revised (recurring) CTC = current + increment + promotion + mgmt-discretion%
        + sustained + salary/market correction (₹).
        NOTE: Special/On-Time Reward is a ONE-TIME payout and is NOT part of CTC."""
        cur = float(self.current_ctc)
        return round(cur + self.increment_amount + self.promotion_amount
                     + self.management_discretion_amount + self.sustained_amount
                     + self.salary_correction_amount, 2)

    @property
    def new_ctc_monthly(self):
        return round(self.new_ctc / 12, 2)

    @property
    def total_impact_pct(self):
        """Recurring CTC hike % (excludes the one-time special reward)."""
        cur = float(self.current_ctc) or 0
        total = (self.increment_amount + self.promotion_amount
                 + self.management_discretion_amount + self.sustained_amount
                 + self.salary_correction_amount)
        return round(total / cur * 100, 2) if cur else 0.0

    @property
    def age(self):
        if not self.date_of_birth:
            return None
        from datetime import date
        today = date.today()
        return today.year - self.date_of_birth.year - (
            (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
        )

    @property
    def tenure_years(self):
        if not self.date_of_joining:
            return None
        from datetime import date
        today = date.today()
        return today.year - self.date_of_joining.year - (
            (today.month, today.day) < (self.date_of_joining.month, self.date_of_joining.day)
        )


class PMSAuditLog(models.Model):
    """Tracks every change made to an employee record."""
    employee    = models.ForeignKey(PMSEmployee, on_delete=models.CASCADE, related_name='audit_logs')
    field       = models.CharField(max_length=100)
    old_value   = models.TextField(blank=True)
    new_value   = models.TextField(blank=True)
    changed_by  = models.CharField(max_length=200, blank=True, default='HR Admin')
    timestamp   = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-timestamp']


class PMSSettings(models.Model):
    """Singleton settings for PMS. Holds the company-wide Management Score that
    applies equally to every employee (management reviews the whole company as one)."""
    management_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'PMS Settings'

    @classmethod
    def get_solo(cls):
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj


class OfferLetter(models.Model):
    """Tracks generated offer/appraisal (CTC revision) letters for employees."""
    LETTER_TYPE_CHOICES = [
        ('increment', 'Increment Letter'),
        ('promotion', 'Promotion Letter'),
        ('redesignation', 'Redesignation Letter'),
        ('combined', 'Combined Promotion & Increment Letter'),
    ]

    # Standalone system — NOT tied to PMS. Employee data comes straight from the
    # uploaded Excel. The optional FK is kept only for backward compatibility.
    employee       = models.ForeignKey(PMSEmployee, on_delete=models.SET_NULL, related_name='offer_letters', null=True, blank=True)
    employee_code  = models.CharField(max_length=50, blank=True)
    employee_name  = models.CharField(max_length=200, blank=True)
    letter_type    = models.CharField(max_length=20, choices=LETTER_TYPE_CHOICES, default='increment')
    current_ctc    = models.DecimalField(max_digits=14, decimal_places=2)
    new_ctc        = models.DecimalField(max_digits=14, decimal_places=2)
    increment_pct  = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    promotion_pct  = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    effective_date = models.DateField()
    old_designation = models.CharField(max_length=200, blank=True)
    new_designation = models.CharField(max_length=200, blank=True)
    performance_rating = models.CharField(max_length=10, blank=True)
    grade_label    = models.CharField(max_length=100, blank=True)
    salutation     = models.CharField(max_length=20, blank=True)   # Mr./Ms./Mr/Ms/Mrs
    assessment     = models.CharField(max_length=100, blank=True)  # e.g. "Strong Performer"
    # Annexure-A (Compensation Break-up) extra employee details + component amounts
    function       = models.CharField(max_length=200, blank=True)
    cadre          = models.CharField(max_length=100, blank=True)
    grade          = models.CharField(max_length=100, blank=True)
    date_of_joining = models.CharField(max_length=50, blank=True)
    work_location  = models.CharField(max_length=200, blank=True)
    salary_breakup = models.JSONField(default=dict, blank=True)  # {component_key: amount}
    special_reward = models.DecimalField(max_digits=14, decimal_places=2, default=0)  # one-time, NOT in CTC
    special_reward_note = models.CharField(max_length=300, blank=True)
    pdf_file       = models.FileField(upload_to='offer_letters/', null=True, blank=True)
    email_sent     = models.BooleanField(default=False)
    email_sent_at  = models.DateTimeField(null=True, blank=True)
    email_address  = models.EmailField(blank=True)
    status         = models.CharField(max_length=20, default='pending',
                                      choices=[('pending', 'Pending'), ('sent', 'Sent'), ('failed', 'Failed')])
    batch_id       = models.CharField(max_length=50, blank=True, db_index=True)
    department     = models.CharField(max_length=200, blank=True, db_index=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        who = self.employee_name or (self.employee.name if self.employee else self.employee_code)
        return f"{who} - {self.letter_type.title()}"


class OfferLetterBatch(models.Model):
    """Tracks a bulk offer-letter generation run so the UI can poll progress
    while the letters are produced in a background thread."""
    batch_id    = models.CharField(max_length=50, unique=True, db_index=True)
    total       = models.IntegerField(default=0)
    processed   = models.IntegerField(default=0)
    generated   = models.IntegerField(default=0)
    emailed     = models.IntegerField(default=0)
    failed      = models.IntegerField(default=0)
    send_emails = models.BooleanField(default=False)
    status      = models.CharField(max_length=20, default='running')  # running / completed / error
    errors      = models.JSONField(default=list, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Batch {self.batch_id} — {self.processed}/{self.total} ({self.status})"
