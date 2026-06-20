"""
PMS - Performance Management System
Complete HR data model with all employee fields
"""
from django.db import models
from django.utils import timezone


GRADE_META = {
    'A+': {'label': 'Exceptional',       'inc_min': 12, 'inc_max': 15, 'promo_pct': 10, 'color': '#059669'},
    'A':  {'label': 'Outstanding',        'inc_min': 10, 'inc_max': 12, 'promo_pct': 8,  'color': '#0284c7'},
    'B+': {'label': 'Exceeds Target',     'inc_min': 7,  'inc_max': 10, 'promo_pct': 6,  'color': '#7c3aed'},
    'B':  {'label': 'Meets Target',       'inc_min': 4,  'inc_max': 7,  'promo_pct': 4,  'color': '#d97706'},
    'C':  {'label': 'Near Target',        'inc_min': 0,  'inc_max': 4,  'promo_pct': 0,  'color': '#ea580c'},
    'D':  {'label': 'Needs Improvement',  'inc_min': 2,  'inc_max': 2,  'promo_pct': 0,  'color': '#dc2626'},
}


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

    # ── Performance Scores ────────────────────────────────────────────────────
    self_score               = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
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
        """Weighted score: Manager×35% + HOD×35% + Management×30%"""
        m  = float(self.manager_score or 0)
        h  = float(self.hod_score or 0)
        mg = float(self.management_score or 0)
        return round(m * 0.35 + h * 0.35 + mg * 0.30, 2)

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
    def effective_increment_pct(self):
        if self.override_increment_pct is not None:
            return float(self.override_increment_pct)
        cfg = self.grade_config
        return (cfg['inc_min'] + cfg['inc_max']) / 2

    @property
    def increment_amount(self):
        return round(float(self.current_ctc) * self.effective_increment_pct / 100, 2)

    @property
    def promotion_amount(self):
        if not self.promoted:
            return 0
        return round(float(self.current_ctc) * float(self.promotion_pct) / 100, 2)

    @property
    def management_discretion_amount(self):
        return round(float(self.current_ctc) * float(self.management_discretion_pct) / 100, 2)

    @property
    def new_ctc(self):
        """Revised CTC = current_ctc × (1 + increment_pct% + promotion_pct% + mgmt_discretion_pct%)"""
        total_pct = (
            self.effective_increment_pct
            + (float(self.promotion_pct) if self.promoted else 0)
            + float(self.management_discretion_pct)
        )
        return round(float(self.current_ctc) * (1 + total_pct / 100), 2)

    @property
    def new_ctc_monthly(self):
        return round(self.new_ctc / 12, 2)

    @property
    def total_impact_pct(self):
        return round(
            self.effective_increment_pct
            + (float(self.promotion_pct) if self.promoted else 0)
            + float(self.management_discretion_pct),
            2
        )

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
