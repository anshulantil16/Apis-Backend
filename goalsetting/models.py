"""Goal Setting Hub - agreeing what a year's goals are, before any of it is scored.

Deliberately a separate app with its own tables, the way Appraisal Hub is. It
carries its own employee master (uploaded from a sheet), its own OTP login and
its own admin, so it can be run for a department that is not yet on any other
tool without entangling the two products' data.

Where it differs from Appraisal Hub is the point of the thing. In appraisal a
manager may only RATE what the employee wrote. Here a manager and an HOD may
genuinely change it - add a KRA, delete a KPI, move a weightage - because the
goals themselves are what is being negotiated. That makes history the core of
the model rather than an afterthought: if a manager can rewrite someone's
goals, the employee must be able to see exactly what was changed and by whom.
Hence PlanVersion, which stores a complete snapshot at every hand-off and is
never overwritten.
"""
from django.db import models
from django.utils import timezone

# Same four as the appraisal form. Employees fill both products; inventing a
# different set here would make the two read as unrelated tools.
CATEGORIES = [
    'Financial',
    'Customer Enhancement',
    'Internal Business Process',
    'People Development',
]

FREQUENCY_OPTIONS = ['Monthly', 'Quarterly', 'Half-Yearly', 'Annually',
                     'Weekly', 'Daily', 'One-Time']

ADMIN_BOOTSTRAP_EMAIL = 'anshul@apisindia.com'


class EmployeeProfile(models.Model):
    """Goal-setting employee master, loaded from a sheet by the admin."""

    USER_TYPE_CHOICES = [
        ('employee', 'Employee'),
        ('manager', 'Manager'),
        ('hod', 'HOD'),
        ('admin', 'Admin'),
    ]

    employee_id = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    designation = models.CharField(max_length=150, blank=True)
    department = models.CharField(max_length=150, blank=True)
    zone = models.CharField(max_length=100, blank=True)
    subzone = models.CharField(max_length=100, blank=True)
    reporting_manager_id = models.CharField(max_length=50, blank=True)
    hod_id = models.CharField(max_length=50, blank=True)
    user_type = models.CharField(max_length=20, choices=USER_TYPE_CHOICES, default='employee')
    is_active = models.BooleanField(default=True)
    joined_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Goal Setting Employee'

    def __str__(self):
        return f'{self.employee_id} - {self.name}'

    @property
    def manager(self):
        return EmployeeProfile.objects.filter(employee_id=self.reporting_manager_id).first()

    @property
    def hod(self):
        return EmployeeProfile.objects.filter(employee_id=self.hod_id).first()


class GoalCycle(models.Model):
    """The period goals are being set for."""

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('open', 'Open for Goal Setting'),
        ('locked', 'Locked'),
        ('closed', 'Closed'),
    ]

    name = models.CharField(max_length=150)
    fiscal_year = models.CharField(max_length=10)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    submission_deadline = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    created_by = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-fiscal_year', '-created_at']
        unique_together = ['name', 'fiscal_year']

    def __str__(self):
        return f'{self.name} ({self.fiscal_year})'

    @property
    def accepts_edits(self):
        return self.status == 'open'


class GoalPlan(models.Model):
    """One employee's goals for one cycle, and where it currently sits.

    The status is the whole workflow. It moves in one direction - employee,
    manager, HOD, back to the employee - and the only backwards move is a
    return for changes, which is recorded like any other hand-off.
    """

    STATUS_CHOICES = [
        ('draft',              'Draft with Employee'),
        ('submitted',          'Submitted - with Manager'),
        ('with_hod',           'With HOD'),
        ('awaiting_employee',  'Returned to Employee for Acceptance'),
        ('accepted',           'Accepted - Goals Agreed'),
        ('returned',           'Sent Back for Changes'),
    ]

    # Who may edit the KRA/KPI table at each stage. Anything not listed here is
    # read-only for everyone, which is what makes 'accepted' final.
    EDITORS = {
        'draft':             ['employee'],
        'returned':          ['employee'],
        'submitted':         ['manager'],
        'with_hod':          ['hod'],
        'awaiting_employee': [],
        'accepted':          [],
    }

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='goal_plans')
    cycle = models.ForeignKey(GoalCycle, on_delete=models.CASCADE, related_name='goal_plans')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')

    employee_note = models.TextField(blank=True)
    manager_note = models.TextField(blank=True)
    hod_note = models.TextField(blank=True)
    employee_acceptance_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    manager_acted_at = models.DateTimeField(null=True, blank=True)
    hod_acted_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ['employee', 'cycle']
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.employee.name} - {self.cycle.name}'

    def may_edit(self, role):
        """Admin may edit at any stage; everyone else only when they hold it.

        The override is deliberate and it is NOT a hole in the audit trail:
        an admin save records a version like any other change, so a sheet that
        was altered after both sides agreed it says so, with a name on it. The
        power to fix a mistake is useless if using it cannot be seen.
        """
        if role == 'admin':
            return True
        return role in self.EDITORS.get(self.status, [])

    @property
    def total_weightage(self):
        return round(sum(k.weightage for kra in self.kras.all() for k in kra.kpis.all()), 2)

    @property
    def kpi_count(self):
        return sum(kra.kpis.count() for kra in self.kras.all())

    def snapshot(self):
        """The KRA/KPI tree as plain data, for storing in a version.

        Stored as JSON rather than by cloning rows: a version must stay
        readable exactly as it was even after a KRA is deleted, and a foreign
        key to a deleted row cannot do that.
        """
        return [{
            'category': kra.category,
            'title': kra.title,
            'description': kra.description,
            'kpis': [{
                'metric': k.metric,
                'weightage': k.weightage,
                'frequency': k.frequency,
                'unit_of_measurement': k.unit_of_measurement,
                'parameter_type': k.parameter_type,
                'data_source': k.data_source,
                'target_value': k.target_value,
            } for k in kra.kpis.all()],
        } for kra in self.kras.all()]


class KRA(models.Model):
    """A Key Result Area - one row of the goal sheet, holding its KPIs."""

    plan = models.ForeignKey(GoalPlan, on_delete=models.CASCADE, related_name='kras')
    category = models.CharField(max_length=100, blank=True)
    title = models.CharField(max_length=1000)
    description = models.TextField(blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'KRA'

    def __str__(self):
        return self.title or f'KRA {self.pk}'


class GoalKPI(models.Model):
    """A measure under a KRA. Same columns the appraisal sheet uses.

    No score fields: this product stops at agreeing the goals. What was
    achieved against them is Appraisal Hub's job.
    """

    kra = models.ForeignKey(KRA, on_delete=models.CASCADE, related_name='kpis')
    metric = models.CharField(max_length=500, blank=True)
    weightage = models.FloatField(default=0)
    frequency = models.CharField(max_length=100, blank=True)
    unit_of_measurement = models.CharField(max_length=200, blank=True)
    parameter_type = models.CharField(max_length=200, blank=True)
    data_source = models.CharField(max_length=500, blank=True)
    target_value = models.CharField(max_length=500, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'KPI'

    def __str__(self):
        return self.metric or f'KPI {self.pk}'


def diff_snapshots(before, after):
    """What changed between two snapshots, in words an employee can read.

    Keyed on category + KRA title, because that is what a person recognises;
    ids would be meaningless in a snapshot and unstable across a delete-and-
    recreate save. A retitled KRA therefore reads as one removed and one added,
    which is honest - the reader still sees both lines.
    """
    def index(rows):
        return {(k.get('category', ''), k.get('title', '')): k for k in rows}

    old, new = index(before), index(after)
    out = []

    for key in new.keys() - old.keys():
        out.append({'type': 'kra_added', 'kra': key[1] or '(untitled)', 'category': key[0]})
    for key in old.keys() - new.keys():
        out.append({'type': 'kra_removed', 'kra': key[1] or '(untitled)', 'category': key[0]})

    for key in old.keys() & new.keys():
        kra_label = key[1] or '(untitled)'
        o_kpis = {k.get('metric', ''): k for k in old[key].get('kpis', [])}
        n_kpis = {k.get('metric', ''): k for k in new[key].get('kpis', [])}

        for m in n_kpis.keys() - o_kpis.keys():
            out.append({'type': 'kpi_added', 'kra': kra_label, 'kpi': m or '(unnamed)'})
        for m in o_kpis.keys() - n_kpis.keys():
            out.append({'type': 'kpi_removed', 'kra': kra_label, 'kpi': m or '(unnamed)'})

        for m in o_kpis.keys() & n_kpis.keys():
            for field in ('weightage', 'target_value', 'frequency',
                          'unit_of_measurement', 'parameter_type', 'data_source'):
                a, b = o_kpis[m].get(field), n_kpis[m].get(field)
                if a != b:
                    out.append({'type': 'kpi_changed', 'kra': kra_label, 'kpi': m or '(unnamed)',
                                'field': field, 'from': a, 'to': b})
    return out


class PlanVersion(models.Model):
    """A complete snapshot of the goals at one hand-off, kept forever.

    This is the point of the product. A manager and an HOD can rewrite what
    someone proposed, so "what did I actually submit?" has to be answerable
    months later. Snapshots are never edited or deleted - a correction is a
    new version, not a change to an old one.
    """

    plan = models.ForeignKey(GoalPlan, on_delete=models.CASCADE, related_name='versions')
    version_no = models.PositiveIntegerField()
    actor_role = models.CharField(max_length=20)
    actor_name = models.CharField(max_length=200, blank=True)
    actor_employee_id = models.CharField(max_length=50, blank=True)
    action = models.CharField(max_length=40)
    note = models.TextField(blank=True)
    kras = models.JSONField(default=list)
    changes = models.JSONField(default=list, blank=True)
    total_weightage = models.FloatField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['version_no']
        unique_together = ['plan', 'version_no']

    def __str__(self):
        return f'{self.plan} - v{self.version_no} by {self.actor_role}'

    @classmethod
    def record(cls, plan, *, role, name='', employee_id='', action='', note=''):
        """Freeze the plan as it stands, and say how it differs from before."""
        previous = plan.versions.order_by('-version_no').first()
        snap = plan.snapshot()
        return cls.objects.create(
            plan=plan,
            version_no=(previous.version_no + 1) if previous else 1,
            actor_role=role, actor_name=name, actor_employee_id=employee_id,
            action=action, note=note,
            kras=snap,
            changes=diff_snapshots(previous.kras if previous else [], snap),
            total_weightage=plan.total_weightage,
        )


class PlanEvent(models.Model):
    """Who did what, including actions that change no goals at all."""

    plan = models.ForeignKey(GoalPlan, on_delete=models.CASCADE, related_name='events')
    actor_role = models.CharField(max_length=20)
    actor_name = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=60)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f'{self.actor_role} {self.action} - {self.plan}'


class OTPToken(models.Model):
    """Sign-in code, the same shape Appraisal Hub uses."""

    employee = models.ForeignKey(EmployeeProfile, on_delete=models.CASCADE, related_name='otp_tokens')
    otp_code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']

    def is_valid(self):
        return not self.is_used and self.expires_at > timezone.now()
