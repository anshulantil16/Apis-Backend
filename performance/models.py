from django.db import models
from django.utils import timezone


class EmployeeProfile(models.Model):
    """Stores employee master data, importable via Excel sheet."""
    USER_TYPE_CHOICES = [
        ('field_force', 'Field Force'),
        ('manager', 'Manager'),
        ('hod', 'HOD'),
        ('hr', 'HR Admin'),
    ]

    APP_SOURCE_CHOICES = [('performance', 'Performance Hub'), ('appraisal', 'Appraisal Hub')]

    employee_id = models.CharField(max_length=50)   # unique per source, see unique_together
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    designation = models.CharField(max_length=100, blank=True)
    department = models.CharField(max_length=100, blank=True)
    zone = models.CharField(max_length=100, blank=True)
    subzone = models.CharField(max_length=100, blank=True)
    reporting_manager_id = models.CharField(max_length=50, blank=True)
    hod_id = models.CharField(max_length=50, blank=True)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default='field_force')
    is_active = models.BooleanField(default=True)
    joined_date = models.DateField(null=True, blank=True)
    app_source = models.CharField(max_length=20, choices=APP_SOURCE_CHOICES, default='performance', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Employee Profile'
        unique_together = ['employee_id', 'app_source']

    def __str__(self):
        return f"{self.employee_id} — {self.name}"

    @property
    def reporting_manager(self):
        try:
            return EmployeeProfile.objects.get(employee_id=self.reporting_manager_id)
        except EmployeeProfile.DoesNotExist:
            return None


class PerformanceCycle(models.Model):
    """Defines a performance review period (quarter)."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('goal_setting', 'Goal Setting Open'),
        ('goals_locked', 'Goals Locked'),
        ('review_open', 'Review Open'),
        ('closed', 'Closed'),
    ]

    name = models.CharField(max_length=100)           # e.g. "Q1 FY2025-26"
    quarter = models.IntegerField(choices=[(1,'Q1'),(2,'Q2'),(3,'Q3'),(4,'Q4')])
    fiscal_year = models.CharField(max_length=10)     # e.g. "2025-26"
    goal_setting_deadline = models.DateField()
    review_start_date = models.DateField()
    review_deadline = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_by = models.CharField(max_length=200, blank=True)
    app_source = models.CharField(max_length=20, default='performance', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fiscal_year', 'quarter']
        unique_together = ['quarter', 'fiscal_year', 'app_source']

    def __str__(self):
        return self.name


class GoalCard(models.Model):
    """One GoalCard per Employee per Cycle. Contains all their quarterly goals."""
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('manager_approved', 'Manager Approved'),
        ('manager_rejected', 'Manager Rejected'),
        ('hod_approved', 'HOD Approved'),
        ('hod_rejected', 'HOD Rejected'),
        ('hr_approved', 'HR Approved'),
        ('finalized', 'Finalized'),
    ]

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='goal_cards')
    cycle = models.ForeignKey(PerformanceCycle, on_delete=models.CASCADE, related_name='goal_cards')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')
    manager_remarks = models.TextField(blank=True)
    hr_remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)
    hr_approved_at = models.DateTimeField(null=True, blank=True)

    # Appraisal form — Steps 2-4 (employee fills)
    self_review_answers = models.JSONField(default=list, blank=True)
    key_skills = models.JSONField(default=list, blank=True)
    training_programs = models.TextField(blank=True)
    feedback_manager = models.TextField(blank=True)
    feedback_manager_rating = models.PositiveSmallIntegerField(null=True, blank=True)
    feedback_organization = models.TextField(blank=True)
    feedback_organization_rating = models.PositiveSmallIntegerField(null=True, blank=True)

    # Manager-suggested additional skills (beyond what employee filled)
    manager_suggested_skills = models.JSONField(default=list, blank=True)

    # Manager remarks (manager fills before submitting rating)
    manager_special_achievements = models.TextField(blank=True)
    manager_promoted = models.CharField(max_length=10, blank=True)          # 'Yes' or 'No'
    manager_promoted_justification = models.TextField(blank=True)
    manager_salary_correction = models.TextField(blank=True)
    manager_salary_justification = models.TextField(blank=True)

    # HOD remarks (HOD fills after manager approval)
    hod_remarks = models.TextField(blank=True)
    hod_special_achievements = models.TextField(blank=True)
    hod_promoted = models.CharField(max_length=10, blank=True)              # 'Yes' or 'No'
    hod_promoted_justification = models.TextField(blank=True)
    hod_salary_correction = models.TextField(blank=True)
    hod_salary_justification = models.TextField(blank=True)
    hod_reviewed_at = models.DateTimeField(null=True, blank=True)

    # UPLIFT values assessment (manager fills)
    manager_uplift_ratings = models.JSONField(default=dict, blank=True)   # { "U": "Always", ... }
    manager_uplift_comments = models.JSONField(default=dict, blank=True)  # { "U": "comment", ... }

    # Competency assessment (HOD fills)
    hod_competency_ratings = models.JSONField(default=dict, blank=True)   # { "1": "Always", ... }

    class Meta:
        unique_together = ['employee', 'cycle']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.name} — {self.cycle.name}"

    @property
    def total_weightage(self):
        return sum(
            kpi.weightage
            for kra in self.goals.all()
            for kpi in kra.kpis.all()
        )

    @property
    def final_weighted_score(self):
        kpis = [kpi for kra in self.goals.all() for kpi in kra.kpis.all()]
        if not kpis:
            return 0
        total = sum((kpi.final_score or 0) * kpi.weightage for kpi in kpis)
        total_weight = sum(kpi.weightage for kpi in kpis)
        return round(total / total_weight, 2) if total_weight > 0 else 0


class CompetencyRating(models.Model):
    """Section C – manager-rated competency scores per employee per cycle."""
    COMPETENCIES = [
        ('ownership',      'Ownership & Accountability'),
        ('communication',  'Communication'),
        ('teamwork',       'Teamwork'),
        ('leadership',     'Leadership'),
        ('compliance',     'Compliance & Discipline'),
        ('problem_solving','Problem Solving'),
        ('innovation',     'Innovation'),
    ]

    goal_card = models.ForeignKey(GoalCard, on_delete=models.CASCADE, related_name='competency_ratings')
    competency = models.CharField(max_length=30, choices=COMPETENCIES)
    marks = models.IntegerField(null=True, blank=True)
    manager_remarks = models.TextField(blank=True)

    class Meta:
        unique_together = ['goal_card', 'competency']

    def __str__(self):
        return f"{self.goal_card} — {self.competency}: {self.marks}"


class Goal(models.Model):
    """KRA (Key Result Area) within a GoalCard, grouped by category."""
    goal_card = models.ForeignKey(GoalCard, on_delete=models.CASCADE, related_name='goals')
    category = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.goal_card} — {self.title}"


class KPI(models.Model):
    """KPI (Key Performance Indicator) within a KRA (Goal)."""
    kra = models.ForeignKey(Goal, on_delete=models.CASCADE, related_name='kpis')
    metric = models.CharField(max_length=200, blank=True)
    target_value = models.CharField(max_length=200, blank=True)
    weightage = models.FloatField(default=0)

    # Appraisal form fields (employee fills during goal setting)
    frequency = models.CharField(max_length=50, blank=True)
    unit_of_measurement = models.CharField(max_length=100, blank=True)
    parameter_type = models.CharField(max_length=100, blank=True)
    data_source = models.CharField(max_length=200, blank=True)
    actual_achievement = models.CharField(max_length=200, blank=True)
    manager_score = models.FloatField(null=True, blank=True)

    # Employee self-assessment
    self_completion_pct = models.IntegerField(null=True, blank=True)
    self_rating = models.IntegerField(null=True, blank=True)
    self_comments = models.TextField(blank=True)
    achievement_description = models.TextField(blank=True)

    # Manager rating
    manager_rating = models.IntegerField(null=True, blank=True)
    manager_comments = models.TextField(blank=True)

    # HOD rating (per-KPI score set by HOD during their review)
    hod_score = models.FloatField(null=True, blank=True)

    # HR calibration
    hr_rating = models.IntegerField(null=True, blank=True)
    hr_comments = models.TextField(blank=True)

    final_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return f"{self.kra.title} — {self.metric}"

    def compute_final_score(self):
        if self.hr_rating:
            score = (self.manager_rating or self.hr_rating) * 0.6 + self.hr_rating * 0.4
        elif self.manager_rating:
            score = self.manager_rating
        else:
            return None
        return round(score, 2)


class QuarterlyReview(models.Model):
    """Employee's quarter-end submission with evidence and self-assessment summary."""
    STATUS_CHOICES = [
        ('pending', 'Pending Submission'),
        ('submitted', 'Submitted'),
        ('manager_reviewed', 'Manager Reviewed'),
        ('hr_finalized', 'HR Finalized'),
        ('published', 'Published'),
    ]

    BAND_CHOICES = [
        ('outstanding', '🏆 Outstanding'),
        ('exceeds', '⭐ Exceeds Expectations'),
        ('meets', '✅ Meets Expectations'),
        ('below', '⚠️ Below Expectations'),
        ('poor', '❌ Needs Improvement'),
    ]

    goal_card = models.OneToOneField(GoalCard, on_delete=models.CASCADE, related_name='review')
    evidence_file = models.FileField(upload_to='performance/evidence/', null=True, blank=True)

    # Section D – Employee Self Remarks
    employee_summary = models.TextField(blank=True)
    key_achievements = models.TextField(blank=True)
    challenges_faced = models.TextField(blank=True)
    support_required = models.TextField(blank=True)
    training_needs = models.TextField(blank=True)
    career_aspirations = models.TextField(blank=True)
    learning_outcomes = models.TextField(blank=True)
    next_quarter_plans = models.TextField(blank=True)
    overall_self_rating = models.IntegerField(null=True, blank=True)

    # Section E – Manager Assessment
    manager_overall_rating = models.IntegerField(null=True, blank=True)
    manager_review_comments = models.TextField(blank=True)
    employee_strengths = models.TextField(blank=True)
    areas_of_improvement = models.TextField(blank=True)
    development_plan = models.TextField(blank=True)
    promotion_recommendation = models.CharField(max_length=100, blank=True)
    increment_recommendation = models.CharField(max_length=100, blank=True)

    # Section F – HR / Management Review
    hr_final_rating = models.IntegerField(null=True, blank=True)
    hr_comments = models.TextField(blank=True)
    functional_head_remarks = models.TextField(blank=True)
    functional_head_rating = models.IntegerField(null=True, blank=True)
    management_approval_remarks = models.TextField(blank=True)
    management_approval_rating = models.IntegerField(null=True, blank=True)

    final_weighted_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    performance_band = models.CharField(max_length=20, choices=BAND_CHOICES, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    submitted_at = models.DateTimeField(null=True, blank=True)
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)
    hr_finalized_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"Review: {self.goal_card}"

    def compute_band(self, score):
        if score >= 4.5:
            return 'outstanding'
        elif score >= 3.75:
            return 'exceeds'
        elif score >= 2.75:
            return 'meets'
        elif score >= 1.75:
            return 'below'
        return 'poor'


class GoalProgressUpdate(models.Model):
    """Mid-quarter progress log entry. Employees add these during the quarter."""
    STATUS_CHOICES = [
        ('on_track', 'On Track'),
        ('ahead', 'Ahead of Schedule'),
        ('at_risk', 'At Risk'),
        ('blocked', 'Blocked'),
        ('completed', 'Completed'),
    ]

    kpi = models.ForeignKey(KPI, on_delete=models.CASCADE, related_name='progress_updates', null=True, blank=True)
    completion_pct = models.IntegerField(default=0)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='on_track')
    notes = models.TextField(blank=True)
    highlights = models.TextField(blank=True)
    blockers = models.TextField(blank=True)
    update_date = models.DateField(auto_now_add=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-update_date', '-created_at']

    def __str__(self):
        return f"{self.goal.title} — {self.completion_pct}% on {self.update_date}"


class OTPToken(models.Model):
    """Short-lived one-time password for login verification."""
    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='otp_tokens')
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def is_valid(self):
        return not self.is_used and self.expires_at > timezone.now()


class ApprovalLog(models.Model):
    """Immutable log of every action taken on a GoalCard."""
    goal_card = models.ForeignKey(GoalCard, on_delete=models.CASCADE, related_name='approval_logs')
    actor_role = models.CharField(max_length=20)   # employee / manager / hr
    actor_name = models.CharField(max_length=200)
    action = models.CharField(max_length=50)       # submitted / approved / rejected / adjusted / rated / published
    comment = models.TextField(blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.actor_role} {self.action} on {self.goal_card}"
