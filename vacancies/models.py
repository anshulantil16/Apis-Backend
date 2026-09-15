from django.db import models

from accounts.moderation import ModeratedContent, ModerationStatus


class Vacancy(ModeratedContent):
    """One open (or since-closed) position on the current hiring plan.

    Backs the Vacancies popup and its home-dashboard preview widget on the
    frontend, plus the "Position Applied For" dropdown on the Employee
    Referral Form — that dropdown only offers rows with status='Active'.

    Two independent states, and they are easy to confuse:

    * `moderation_status` — has a superadmin let this onto the dashboard yet?
      Inherited from ModeratedContent; anything not APPROVED is invisible to
      everyone except its own submitter and the console.
    * `status` — is the position still open, or has it been filled/closed?
      A business fact about an already-approved vacancy.

    A vacancy is only ever public when both say yes: approved, and Active.
    """

    TYPE_CHOICES = [('New', 'New'), ('Replacement', 'Replacement')]
    STATUS_CHOICES = [('Active', 'Active'), ('Closed', 'Closed')]

    function = models.CharField(max_length=50)
    department = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    grade = models.CharField(max_length=20, blank=True)
    location = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    reporting_manager = models.CharField(max_length=150, blank=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES, default='New')
    experience = models.CharField(max_length=50, blank=True)
    education = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='Active')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'vacancy'
        verbose_name_plural = 'vacancies'

    # ── what the console's pending queue shows ──────────────────────────────
    def moderation_label(self):
        return f'{self.title} — {self.location}, {self.state}'

    def moderation_detail(self):
        return {
            'Function': self.function, 'Department': self.department,
            'Grade': self.grade, 'Type': self.type,
            'Experience': self.experience, 'Education': self.education,
            'Reporting to': self.reporting_manager,
        }

    @classmethod
    def published(cls):
        """Rows the dashboard may show: approved, in newest-first order.

        Every public read goes through this. Filtering by hand in a view is
        how a pending row eventually gets shown to the company.
        """
        return cls.objects.filter(moderation_status=ModerationStatus.APPROVED)

    def __str__(self):
        return f'{self.title} — {self.location}, {self.state} ({self.status})'
