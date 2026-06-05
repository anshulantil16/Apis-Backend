from django.db import models
from django.utils import timezone


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

    name       = models.CharField(max_length=120)           # "Employee of the Month — June 2026"
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

    employee = models.ForeignKey(
        'performance.EmployeeProfile',
        on_delete=models.CASCADE,
        related_name='eom_nominations',
    )
    cycle  = models.ForeignKey(EOMCycle, on_delete=models.CASCADE, related_name='nominations')
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default='draft')

    # ── Employee fills (form fields added here once design is confirmed) ─────
    # Placeholder — user will specify fields; add migration when ready.

    # ── Manager review ───────────────────────────────────────────────────────
    manager_remarks     = models.TextField(blank=True)
    manager_reviewed_at = models.DateTimeField(null=True, blank=True)

    # ── HOD review ───────────────────────────────────────────────────────────
    hod_remarks     = models.TextField(blank=True)
    hod_reviewed_at = models.DateTimeField(null=True, blank=True)

    # ── HR finalisation ──────────────────────────────────────────────────────
    hr_remarks       = models.TextField(blank=True)
    hr_finalized_at  = models.DateTimeField(null=True, blank=True)
    is_winner        = models.BooleanField(default=False)

    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['employee', 'cycle']
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.name} — {self.cycle.name}"
