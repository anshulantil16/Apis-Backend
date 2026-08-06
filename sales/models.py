"""SalesIQ — sales analytics data model.

Deliberately standalone: this app shares no models, tables or imports with
pms/appraisal/eom. It owns exactly two tables.

Design note — SalesRecord is a denormalised fact table rather than a set of
normalised dimension tables. Sales sheets arrive as flat exports with
inconsistent master data (the same state spelled three ways), so there is no
reliable key to join on. Storing the text inline and aggregating with GROUP BY
keeps ingestion forgiving and every dashboard query a single-table scan.
"""
from django.db import models


class SalesUpload(models.Model):
    """One uploaded sales report. Deleting it cascades to its rows, so a bad
    upload can be rolled back without touching the rest of the data."""
    filename     = models.CharField(max_length=255, blank=True)
    uploaded_by  = models.CharField(max_length=200, blank=True)
    row_count    = models.IntegerField(default=0)
    skipped_rows = models.IntegerField(default=0)
    total_revenue = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    period_start = models.DateField(null=True, blank=True)
    period_end   = models.DateField(null=True, blank=True)
    warnings     = models.JSONField(default=list, blank=True)
    status       = models.CharField(max_length=20, default='completed')  # completed / failed
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.filename or 'upload'} — {self.row_count} rows"


class SalesRecord(models.Model):
    """A single sales line (one invoice line, or one pre-aggregated row)."""
    upload = models.ForeignKey(SalesUpload, on_delete=models.CASCADE, related_name='records')

    # ── When ──────────────────────────────────────────────────────────────
    order_date = models.DateField(db_index=True)
    # First day of the order month, precomputed at ingest. Monthly grouping is
    # the single most common query here and MySQL cannot use an index on a
    # function like MONTH(order_date), so this column is what keeps it fast.
    period     = models.DateField(db_index=True)
    invoice_no = models.CharField(max_length=100, blank=True)

    # ── Where ─────────────────────────────────────────────────────────────
    zone     = models.CharField(max_length=100, blank=True, db_index=True)
    state    = models.CharField(max_length=100, blank=True, db_index=True)
    city     = models.CharField(max_length=100, blank=True)
    area     = models.CharField(max_length=150, blank=True, db_index=True)
    region   = models.CharField(max_length=100, blank=True)

    # ── What ──────────────────────────────────────────────────────────────
    sku          = models.CharField(max_length=100, blank=True, db_index=True)
    product_name = models.CharField(max_length=255, blank=True, db_index=True)
    category     = models.CharField(max_length=150, blank=True, db_index=True)
    sub_category = models.CharField(max_length=150, blank=True)
    brand        = models.CharField(max_length=150, blank=True)
    pack_size    = models.CharField(max_length=80, blank=True)
    uom          = models.CharField(max_length=40, blank=True)

    # ── Who bought ────────────────────────────────────────────────────────
    channel       = models.CharField(max_length=100, blank=True, db_index=True)
    customer_code = models.CharField(max_length=100, blank=True)
    customer_name = models.CharField(max_length=255, blank=True, db_index=True)
    customer_type = models.CharField(max_length=100, blank=True)

    # ── Who sold ──────────────────────────────────────────────────────────
    salesperson = models.CharField(max_length=200, blank=True, db_index=True)
    asm         = models.CharField(max_length=200, blank=True, db_index=True)
    rsm         = models.CharField(max_length=200, blank=True, db_index=True)
    territory   = models.CharField(max_length=150, blank=True)

    # ── Money ─────────────────────────────────────────────────────────────
    quantity      = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    unit_price    = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    gross_amount  = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    discount      = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tax           = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    # The headline metric every dashboard number is built on.
    net_amount    = models.DecimalField(max_digits=18, decimal_places=2, default=0, db_index=True)
    target_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-order_date']
        indexes = [
            # Dashboard queries are almost always "filter a date window, then
            # group by one dimension" — these cover the common pairings.
            models.Index(fields=['period', 'state']),
            models.Index(fields=['period', 'category']),
            models.Index(fields=['period', 'salesperson']),
            models.Index(fields=['period', 'channel']),
        ]

    def __str__(self):
        return f"{self.order_date} {self.product_name or self.sku} — {self.net_amount}"
