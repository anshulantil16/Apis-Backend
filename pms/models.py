"""
PMS - Performance Management System
No sessions — one global employee pool, import/replace anytime.
"""
from django.db import models


class PMSEmployee(models.Model):
    BAND_CHOICES = [
        ('D', 'Director Band'),
        ('C', 'CXO / HOD / Leader'),
        ('M', 'Middle Management'),
        ('O', 'Officer / Supervisor'),
        ('W', 'Workforce / Associate'),
    ]

    employee_id     = models.CharField(max_length=50, unique=True)
    name            = models.CharField(max_length=200)
    designation     = models.CharField(max_length=200, blank=True)
    department      = models.CharField(max_length=200, blank=True)
    location        = models.CharField(max_length=200, blank=True)
    band            = models.CharField(max_length=5, choices=BAND_CHOICES, blank=True)
    gender          = models.CharField(max_length=20, blank=True)
    fiscal_year     = models.CharField(max_length=20, blank=True, default='2025-26')
    current_ctc     = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Scores (each out of 100)
    manager_score    = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    hod_score        = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    management_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # Prior year scores for trend
    fy_prev1_score  = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    fy_prev2_score  = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    # Remarks
    manager_remarks = models.TextField(blank=True)
    hod_remarks     = models.TextField(blank=True)

    # Simulation overrides
    override_increment_pct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    override_grade         = models.CharField(max_length=5, blank=True)
    promoted               = models.BooleanField(default=False)
    on_time_reward         = models.BooleanField(default=False)
    management_discretion  = models.BooleanField(default=False)
    promotion_readiness    = models.CharField(max_length=20, blank=True)  # ready_now / 1_year / 2_years / not_ready
    notes                  = models.TextField(blank=True)
    created_at             = models.DateTimeField(auto_now_add=True)
    updated_at             = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['department', 'name']

    def __str__(self):
        return f"{self.name} ({self.employee_id})"

    @property
    def final_score(self):
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

    GRADE_META = {
        'A+': {'label': 'Exceptional',       'inc_min': 12, 'inc_max': 15, 'promo_pct': 10, 'color': '#059669'},
        'A':  {'label': 'Outstanding',        'inc_min': 10, 'inc_max': 12, 'promo_pct': 8,  'color': '#0284c7'},
        'B+': {'label': 'Exceeds Target',     'inc_min': 7,  'inc_max': 10, 'promo_pct': 6,  'color': '#7c3aed'},
        'B':  {'label': 'Meets Target',       'inc_min': 4,  'inc_max': 7,  'promo_pct': 4,  'color': '#d97706'},
        'C':  {'label': 'Near Target',        'inc_min': 0,  'inc_max': 4,  'promo_pct': 0,  'color': '#ea580c'},
        'D':  {'label': 'Needs Improvement',  'inc_min': 2,  'inc_max': 2,  'promo_pct': 0,  'color': '#dc2626'},
    }

    @property
    def grade_config(self):
        return self.GRADE_META.get(self.effective_grade, self.GRADE_META['B'])

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
    def new_ctc(self):
        return round(float(self.current_ctc) + self.increment_amount, 2)

    @property
    def is_high_performer(self):
        return self.effective_grade in ('A+', 'A')

    @property
    def is_low_performer(self):
        return self.effective_grade in ('C', 'D')

    @property
    def performance_vs_salary(self):
        """High performer = top 25% score, High salary = top 25% CTC"""
        return {
            'final_score': self.final_score,
            'current_ctc': float(self.current_ctc),
        }
