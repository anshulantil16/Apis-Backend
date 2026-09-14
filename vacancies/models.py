from django.db import models


class Vacancy(models.Model):
    """One open (or since-closed) position on the current hiring plan.

    Backs the Vacancies popup and its home-dashboard preview widget on the
    frontend, plus the "Position Applied For" dropdown on the Employee
    Referral Form — that dropdown only offers rows with status='Active'.
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

    def __str__(self):
        return f'{self.title} — {self.location}, {self.state} ({self.status})'
