"""CTC Calculator — the candidates a CTC is worked out for.

Rows arrive in bulk: someone downloads the Excel template, fills in one row
per candidate and uploads it back. Each upload is kept as a batch, so the
page can show what came in together, who sent it and when, and a mistaken
upload can be removed in one go without touching anyone else's.

These are the calculator's inputs only. Nothing here works out a salary
yet — that waits on APIS's own CTC breakup rules.
"""
from django.db import models


class CtcUpload(models.Model):
    """One uploaded workbook."""

    original_name = models.CharField(max_length=255, blank=True)
    uploaded_by = models.ForeignKey(
        'accounts.PortalUser', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='+')
    # Kept as text as well, so the record still says who it was if the
    # account is later removed.
    uploaded_by_name = models.CharField(max_length=200, blank=True)
    row_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'CTC upload'

    def __str__(self):
        return f'{self.original_name or "upload"} ({self.row_count} rows)'

    def can_manage(self, user):
        """Whoever uploaded it, or a super admin."""
        return bool(user and (user.is_superadmin or self.uploaded_by_id == user.id))


class CtcCandidate(models.Model):
    """One row of an uploaded workbook — one candidate."""

    upload = models.ForeignKey(CtcUpload, on_delete=models.CASCADE, related_name='candidates')
    # The spreadsheet row it came from, so a problem can be traced back to
    # the line someone typed.
    row_number = models.PositiveIntegerField()

    candidate_name = models.CharField(max_length=200)
    department     = models.CharField(max_length=120, blank=True)
    designation    = models.CharField(max_length=120, blank=True)
    grade          = models.CharField(max_length=40, blank=True)
    cadre          = models.CharField(max_length=60, blank=True)
    work_location  = models.CharField(max_length=120, blank=True)
    # Annual CTC in rupees, as offered.
    ctc            = models.DecimalField(max_digits=12, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['upload', 'row_number']
        verbose_name = 'CTC candidate'

    def __str__(self):
        return self.candidate_name
