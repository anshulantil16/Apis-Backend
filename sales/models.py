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


    # ── Document identity (Pre-Sales Dump) ────────────────────────────────
    # Whether a row counts as a sale at all is decided here, not in the money
    # columns. An ERP dump carries cancelled invoices and credit memos in the
    # same sheet as live sales; counting them is how a dashboard quietly
    # overstates the year.
    document_type  = models.CharField(max_length=60, blank=True, db_index=True)
    is_cancelled   = models.BooleanField(default=False, db_index=True)
    # True for a credit memo / return. Kept as its own flag rather than
    # inferred at query time so every aggregate agrees on what a return is.
    is_return      = models.BooleanField(default=False, db_index=True)

    invoice_date    = models.DateField(null=True, blank=True)
    posting_date    = models.DateField(null=True, blank=True)
    external_doc_no = models.CharField(max_length=100, blank=True)
    sales_order_no  = models.CharField(max_length=100, blank=True)
    reason_code     = models.CharField(max_length=80, blank=True)
    remarks         = models.CharField(max_length=500, blank=True)

    # ── Customer detail ───────────────────────────────────────────────────
    customer_district   = models.CharField(max_length=150, blank=True, db_index=True)
    customer_state_code = models.CharField(max_length=20, blank=True)
    customer_gst        = models.CharField(max_length=30, blank=True)
    # Customer Posting Group / Customer Price Group in the dump. Named for
    # what the business calls them rather than what the ERP calls them.
    business_type   = models.CharField(max_length=100, blank=True, db_index=True)
    warehouse_type  = models.CharField(max_length=100, blank=True, db_index=True)
    subzone         = models.CharField(max_length=100, blank=True, db_index=True)

    # ── Selling location (the branch/depot that billed it) ────────────────
    location       = models.CharField(max_length=150, blank=True, db_index=True)
    location_state = models.CharField(max_length=100, blank=True)

    # ── Product detail ────────────────────────────────────────────────────
    item_alt_code  = models.CharField(max_length=100, blank=True)   # I-CODE
    packaging_type = models.CharField(max_length=100, blank=True)
    item_sub_type  = models.CharField(max_length=100, blank=True)
    variant        = models.CharField(max_length=150, blank=True)
    prod_group     = models.CharField(max_length=150, blank=True)
    hsn_code       = models.CharField(max_length=40, blank=True)
    batch_no       = models.CharField(max_length=80, blank=True)
    # Packed / expiry, so ageing stock and short-shelf-life dispatch are
    # answerable from the same table.
    packed_on      = models.DateField(null=True, blank=True)
    use_by         = models.DateField(null=True, blank=True)
    mrp            = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    # FMCG plans and reports in tonnes as much as in rupees.
    gross_weight_kg = models.DecimalField(max_digits=16, decimal_places=3, default=0)
    net_weight_kg   = models.DecimalField(max_digits=16, decimal_places=3, default=0)

    # ── Money detail ──────────────────────────────────────────────────────
    # The invoice's own taxable value. `net_amount` is set from this, because
    # sales are reported net of scheme and before GST; this column keeps the
    # source figure so the two can be reconciled against the ERP.
    taxable_amount      = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    invoice_discount    = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    retail_scheme       = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    wholesale_scheme    = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    igst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    cgst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    sgst_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    tcs_amount  = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    total_with_tax = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    gst_jurisdiction = models.CharField(max_length=40, blank=True)

    # Export billing. Blank on a domestic row.
    currency_code  = models.CharField(max_length=10, blank=True)
    exchange_rate  = models.DecimalField(max_digits=16, decimal_places=6, default=0)
    line_amount_fc = models.DecimalField(max_digits=18, decimal_places=2, default=0)

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
            # Every headline figure filters cancelled rows out first.
            models.Index(fields=['is_cancelled', 'period']),
        ]

    def __str__(self):
        return f"{self.order_date} {self.product_name or self.sku} — {self.net_amount}"
