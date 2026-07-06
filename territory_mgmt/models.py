from django.db import models


class OrganizationData(models.Model):
    """Territory Management - Employee organization structure by RM, Zone, Designation."""

    sno             = models.IntegerField()
    code            = models.CharField(max_length=50, unique=True, db_index=True)
    name            = models.CharField(max_length=200, db_index=True)
    designation     = models.CharField(max_length=200, db_index=True)
    hq              = models.CharField(max_length=200, blank=True)
    state           = models.CharField(max_length=100, db_index=True)
    zone            = models.CharField(max_length=100, db_index=True)
    rm              = models.CharField(max_length=200, db_index=True)

    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)
    batch_id        = models.CharField(max_length=50, blank=True, db_index=True)

    class Meta:
        ordering = ['sno']
        indexes = [
            models.Index(fields=['designation']),
            models.Index(fields=['state']),
            models.Index(fields=['zone']),
            models.Index(fields=['rm']),
            models.Index(fields=['batch_id']),
        ]
        db_table = 'territory_organization_data'

    def __str__(self):
        return f"{self.name} - {self.designation} ({self.zone})"
