from django.db import models

from accounts.moderation import ModeratedContent, ModerationStatus


class EmployeeReferral(ModeratedContent):
    """One submission of the Employee Referral Form (Vacancies popup on the
    intranet home page). Mirrors that form's sections one for one, so a
    field added there has an obvious matching column here.

    Inherits ModeratedContent for the submitter snapshot, not for the gate:
    a referral goes to HR rather than onto the dashboard, so there is nothing
    to publish and it is marked approved on arrival. What the inherited
    columns buy is attribution — `referrer_name` is free text the submitter
    types and could say anything, while `submitted_by` is the portal account
    that actually sent it.
    """

    RECOMMENDATION_CHOICES = [
        ('strong', 'Strongly Recommend'),
        ('recommend', 'Recommend'),
        ('reservations', 'Recommend with Reservations'),
        ('not', 'Do Not Recommend'),
    ]
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Shortlisted', 'Shortlisted'),
        ('Interviewing', 'Interviewing'),
        ('Hired', 'Hired'),
        ('Rejected', 'Rejected'),
    ]

    # Candidate details
    candidate_name = models.CharField(max_length=200)
    # Free text, not a foreign key: vacancies live in the frontend's static
    # hiring-plan list (VACANCY_LISTINGS), not a `vacancies` table — storing
    # the picked "title — location, state" string keeps the submission
    # self-contained even after that list changes.
    position_applied_for = models.CharField(max_length=250)
    candidate_department = models.CharField(max_length=100, blank=True)
    candidate_employee_id = models.CharField(max_length=50, blank=True)
    candidate_contact = models.CharField(max_length=30, blank=True)
    candidate_email = models.EmailField(blank=True)

    # Referrer details
    referrer_name = models.CharField(max_length=200)
    referrer_employee_id = models.CharField(max_length=50, blank=True)
    referrer_designation = models.CharField(max_length=150, blank=True)
    referrer_department = models.CharField(max_length=100, blank=True)
    referrer_contact = models.CharField(max_length=30, blank=True)
    referrer_email = models.EmailField(blank=True)

    # Relationship with candidate
    relationship = models.CharField(max_length=100, blank=True)
    relationship_other = models.CharField(max_length=200, blank=True)
    association_duration = models.CharField(max_length=100, blank=True)

    # Candidate employment details, if previously worked together
    previous_organization = models.CharField(max_length=200, blank=True)
    candidate_previous_designation = models.CharField(max_length=150, blank=True)
    employment_from = models.DateField(null=True, blank=True)
    employment_to = models.DateField(null=True, blank=True)
    reporting_to = models.CharField(max_length=150, blank=True)

    # Candidate skills & performance
    technical_skills = models.CharField(max_length=300, blank=True)
    strengths = models.CharField(max_length=300, blank=True)
    areas_of_improvement = models.CharField(max_length=300, blank=True)
    overall_performance = models.CharField(max_length=50, blank=True)

    # Overall recommendation
    recommendation = models.CharField(max_length=20, choices=RECOMMENDATION_CHOICES)
    disciplinary_issues = models.BooleanField(default=False)
    disciplinary_details = models.TextField(blank=True)

    # Declaration by referrer
    referrer_signature = models.CharField(max_length=200, blank=True)
    declaration_date = models.DateField(null=True, blank=True)

    # HR-only review fields — set from the Django admin, not the public form
    verified_by = models.CharField(max_length=150, blank=True)
    verification_date = models.DateField(null=True, blank=True)
    hr_remarks = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.candidate_name} referred by {self.referrer_name} — {self.position_applied_for}'

    def moderation_label(self):
        return f'{self.candidate_name} for {self.position_applied_for}'

    def moderation_detail(self):
        return {
            'Referred by': self.referrer_name,
            'Department': self.candidate_department,
            'Recommendation': self.get_recommendation_display() if self.recommendation else '',
            'Pipeline status': self.status,
        }
