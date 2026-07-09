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
    'A+': {'staff1': 14, 'staff2': 10, 'worker_monthly': 800, 'worker_promo_monthly': 400, 'promotion_pct': 5},
    'A':  {'staff1': 12, 'staff2': 9,  'worker_monthly': 600, 'worker_promo_monthly': 300, 'promotion_pct': 4},
    'B+': {'staff1': 10, 'staff2': 8,  'worker_monthly': 400, 'worker_promo_monthly': 200, 'promotion_pct': 3},
    'B':  {'staff1': 8,  'staff2': 7,  'worker_monthly': 200, 'worker_promo_monthly': 100, 'promotion_pct': 2},
    'C':  {'staff1': 4,  'staff2': 3,  'worker_monthly': 100, 'worker_promo_monthly': 0,   'promotion_pct': 0},
    'D':  {'staff1': 0,  'staff2': 0,  'worker_monthly': 0,   'worker_promo_monthly': 0,   'promotion_pct': 0},
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
            return round(row['worker_promo_monthly'] * 12, 2)
        pct = float(self.promotion_pct) if self.promotion_pct else row['promotion_pct']
        return round(float(self.current_ctc) * pct / 100, 2)

    @property
    def management_discretion_amount(self):
        return round(float(self.current_ctc) * float(self.management_discretion_pct) / 100, 2)

    @property
    def new_ctc(self):
        """Revised CTC = current + increment + promotion + management-discretion (all as ₹ amounts)."""
        cur = float(self.current_ctc)
        return round(cur + self.increment_amount + self.promotion_amount + self.management_discretion_amount, 2)

    @property
    def new_ctc_monthly(self):
        return round(self.new_ctc / 12, 2)

    @property
    def total_impact_pct(self):
        cur = float(self.current_ctc) or 0
        total = self.increment_amount + self.promotion_amount + self.management_discretion_amount
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
