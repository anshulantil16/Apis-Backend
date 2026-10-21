"""CTC Calculator — the candidates a CTC is worked out for.

Rows arrive in bulk: someone downloads the Excel template, fills in one row
per candidate and uploads it back. Each upload is kept as a batch, so the
page can show what came in together, who sent it and when, and a mistaken
upload can be removed in one go without touching anyone else's.

The break-up itself is worked out on demand (calculator.py) from these
inputs, the rules in rules.py and the editable values in CtcSettings, so a
change to a setting shows on every candidate at once.
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

    employee_code  = models.CharField(max_length=40, blank=True)
    candidate_name = models.CharField(max_length=200)
    # Sales / HO / Plant — decides which VPP table applies.
    category       = models.CharField(max_length=20, blank=True)
    department     = models.CharField(max_length=120, blank=True)
    designation    = models.CharField(max_length=120, blank=True)
    grade          = models.CharField(max_length=40, blank=True)
    cadre          = models.CharField(max_length=60, blank=True)
    # Sets the Child Education Allowance (rules.CEA_PER_CHILD).
    number_of_children = models.PositiveSmallIntegerField(default=0)
    date_of_joining = models.DateField(null=True, blank=True)
    work_location  = models.CharField(max_length=120, blank=True)
    # Annual CTC in rupees, as offered.
    ctc            = models.DecimalField(max_digits=12, decimal_places=2)
    # Old Regime / New Regime (columns.TAX_REGIMES), or blank. Rows uploaded
    # before Both Regime was dropped may still hold it; it counts as New Regime.
    tax_regime     = models.CharField(max_length=20, blank=True)
    # The Gross Earnings items answered on the template, {key: True/False}
    # (columns.OPTION_COLUMNS); a missing key takes that column's default.
    # Always empty for cadres O and W.
    gross_earnings = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['upload', 'row_number']
        verbose_name = 'CTC candidate'

    def __str__(self):
        return self.candidate_name


class CtcSettings(models.Model):
    """The values P & C's workbook leaves open, editable by a super admin.
    (Minimum wages are by state, in rules.MIN_WAGES.)

    A single row (pk=1). Everything starts at 0 except the two statutory
    limits, so nothing on a salary sheet is a guess: until someone sets LTA
    or Gratuity, the break-up shows them as 0.
    """

    lta_d = models.PositiveIntegerField(default=0, help_text='LTA per month, cadre D')
    lta_c = models.PositiveIntegerField(default=0, help_text='LTA per month, cadre C')
    lta_m = models.PositiveIntegerField(default=0, help_text='LTA per month, cadre M')
    lta_o = models.PositiveIntegerField(default=0, help_text='LTA per month, cadre O')
    lta_w = models.PositiveIntegerField(default=0, help_text='LTA per month, cadre W')

    gratuity_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0,
                                           help_text='% of Basic, per month')

    # The D cadre's "Actual" reimbursements in the workbook.
    d_fuel   = models.PositiveIntegerField(default=0, help_text='Fuel & vehicle, per month, cadre D')
    d_driver = models.PositiveIntegerField(default=0, help_text='Driver, per month, cadre D')
    d_health = models.PositiveIntegerField(default=0, help_text='Health & wellness, per month, cadre D')

    pf_wage_ceiling = models.PositiveIntegerField(
        default=25000, help_text='PF wage ceiling per month (12% of Basic up to this; Rs 3,000 at 25,000)')
    esi_bonus_gross_limit = models.PositiveIntegerField(
        default=21000, help_text='ESI and Statutory Bonus apply up to this gross per month')

    updated_at = models.DateTimeField(auto_now=True)
    updated_by_name = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = 'CTC settings'
        verbose_name_plural = 'CTC settings'

    def __str__(self):
        return 'CTC settings'

    @classmethod
    def current(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def lta_for(self, cadre):
        return getattr(self, f'lta_{cadre.lower()}', 0)

    def actual_for(self, key):
        """A D-cadre "Actual" reimbursement."""
        return getattr(self, f'd_{key}', 0)
