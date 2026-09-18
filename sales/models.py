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
    # Two different things, kept apart because they read differently to the
    # person who just uploaded a file. A warning means something was lost or
    # needs a decision. A note means the import did exactly what it should
    # and is saying so — "1,400 cancelled rows excluded" is reassurance, not
    # a problem, and dressing it as one made a clean import of a normal ERP
    # export look like seven faults.
    warnings     = models.JSONField(default=list, blank=True)
    notes        = models.JSONField(default=list, blank=True)
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


    # ── Where this row came from ──────────────────────────────────────────
    # Two primary-sales files describe overlapping ground. The Pre-Sales Dump
    # is invoice detail; the AOP-vs-ACH sheet is the same sales already
    # summed by month, alongside the plan. Summing both would count every
    # rupee twice, so each row records which file it came from and the
    # dashboards prefer invoice detail wherever it exists.
    SOURCE_INVOICE = 'invoice'      # Pre-Sales Dump — one row per invoice line
    SOURCE_PLAN    = 'plan'         # AOP vs ACH — one row per person/item/month
    source = models.CharField(max_length=20, default=SOURCE_INVOICE, db_index=True)

    # Above RSM in the AOP sheet's hierarchy: HEAD > GTR HEAD > REPORT.INCHARGE.
    sales_head = models.CharField(max_length=200, blank=True, db_index=True)
    # Field officers behind this line. A row attribute, not a monthly one, so
    # it repeats across the months a row unpivots into.
    sfo_count  = models.IntegerField(default=0)

    # The AOP sheet's own achievement figure, always stored as given. It is the
    # same money the Pre-Sales Dump reports line by line, so it is kept HERE
    # rather than in net_amount, and sync_actual_source() decides which of the
    # two feeds the dashboards. Every aggregate in this app sums net_amount and
    # target_amount off one queryset, so keeping both sources in net_amount
    # would double every rupee that appears in both files.
    # The row's OWN measured actual, whichever file it came from, kept
    # untouched for the life of the row. net_amount above is the ELECTED
    # actual: it is this figure when this row's source owns its month, and
    # zero when the other file does. Keeping the two apart is what makes
    # sync_actual_source() re-runnable -- it recomputes the election from
    # these originals every time, so uploading and deleting files in any
    # order lands on the same answer.
    measured_amount = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # ── Document identity (Pre-Sales Dump) ────────────────────────────────
    # Whether a row counts as a sale at all is decided here, not in the money
    # columns. An ERP dump carries cancelled invoices and credit memos in the
    # same sheet as live sales; counting them is how a dashboard quietly
    # overstates the year.
    document_type  = models.CharField(max_length=60, blank=True, db_index=True)
    # The dump's "Type" column, which is the LINE type, not the document
    # type: Business Central writes Item for a product line and G/L Account
    # for a charge posted straight to a ledger account — freight, rounding,
    # a manual adjustment. Both are real money on the invoice, but only an
    # Item line has a product on it, so the two must be told apart before a
    # product report can be trusted.
    line_type      = models.CharField(max_length=60, blank=True, db_index=True)
    # Which ledger account a G/L Account line was posted to. Without it the
    # non-product money on an invoice -- Rs 10 lakh in the first real file --
    # is a number with no explanation; with it, it reads as Freight Others,
    # Packing Material-Import, Purchase Third Party.
    gl_account_no   = models.CharField(max_length=40, blank=True)
    gl_account_name = models.CharField(max_length=150, blank=True, db_index=True)
    is_cancelled   = models.BooleanField(default=False, db_index=True)
    # True for a credit memo / return. Kept as its own flag rather than
    # inferred at query time so every aggregate agrees on what a return is.
    is_return      = models.BooleanField(default=False, db_index=True)
    # The business's own verdict, from the dump's V-REMARS column: freight,
    # packaging and spares that ride on a sales invoice but are not sales.
    # A flag rather than a query-time match on the remark text, so the rule
    # is decided once at import and every aggregate agrees with every other.
    is_not_sales   = models.BooleanField(default=False, db_index=True)

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
            # Actuals are read from one source at a time.
            models.Index(fields=['source', 'period']),
        ]

    def __str__(self):
        return f"{self.order_date} {self.product_name or self.sku} — {self.net_amount}"


# How much of a month the invoice extract has to account for before it is
# treated as that month's record of sales.
#
# The number only has to separate "this is the month the extract covers" from
# "these are a few stragglers", and the real gap between those two is enormous
# -- in the file this was built against, the covered month came in at 236% of
# the review sheet's figure and the month before it at 3%. Half is a long way
# from either.
INVOICE_MONTH_COVERAGE = 0.5


def sync_actual_source():
    """Decide, for each month, which file that month's sales come from.

    The two primary-sales files are not a detail copy and a summary copy of
    the same thing, which is the assumption this function used to make:

      * the Pre-Sales Dump is a live ERP extract -- line level, all channels
        including export, but only the month it was taken in, plus a tail of
        credit notes raised since against earlier months;
      * the AOP-vs-ACH sheet is the monthly review file -- no line detail and
        general trade only, but two full financial years of actuals beside
        the plan.

    Letting the dump win outright, as it did, threw away eighteen months of
    history to keep one month of detail. What was left was a trend chart whose
    first seven months were made entirely of credit notes and so ran below
    zero, and a headline of Rs 16 crore -- one September -- standing in for the
    company's year.

    So the election is per month, and a month goes to the dump only when the
    dump actually accounts for it. A month it merely clipped stays with the
    review sheet.

    Whichever source loses a month has its rows zeroed for that month rather
    than dropped: the returns in a month the review sheet owns are already
    inside that sheet's net figure, and adding the dump's credit notes on top
    would count them twice.

    Every branch reads from measured_amount and writes only net_amount, so
    this is re-runnable and order-independent. Run it after every upload and
    every deletion.
    """
    from django.db.models import F, Q, Sum

    plan = SalesRecord.objects.filter(source=SalesRecord.SOURCE_PLAN)
    invoice = SalesRecord.objects.filter(source=SalesRecord.SOURCE_INVOICE)

    if not plan.exists():
        # Nothing to reconcile against; the dump is all there is.
        invoice.update(net_amount=F('measured_amount'))
        return
    if not invoice.exists():
        plan.update(net_amount=F('measured_amount'))
        invoice.update(net_amount=F('measured_amount'))
        return

    # What each source says it sold, month by month. The dump is judged on
    # its POSITIVE lines only: a month holding nothing but credit notes has
    # a negative total, and "less than the review sheet" is the wrong reason
    # to reject it -- it never claimed to cover that month at all.
    inv_positive = {
        r['period']: float(r['v'] or 0)
        for r in (invoice.exclude(is_cancelled=True)
                  .values('period')
                  .annotate(v=Sum('measured_amount',
                                  filter=Q(measured_amount__gt=0)))
                  .order_by())
    }
    plan_actual = {
        r['period']: float(r['v'] or 0)
        for r in (plan.values('period').annotate(v=Sum('measured_amount'))
                  .order_by())
    }

    invoice_months = set()
    for period, sold in inv_positive.items():
        if sold <= 0:
            continue
        claimed = plan_actual.get(period, 0.0)
        # No review figure to compare against means nothing contradicts the
        # dump, so the dump stands.
        if claimed <= 0 or sold >= claimed * INVOICE_MONTH_COVERAGE:
            invoice_months.add(period)

    invoice.filter(period__in=invoice_months).update(net_amount=F('measured_amount'))
    invoice.exclude(period__in=invoice_months).update(net_amount=0)
    plan.exclude(period__in=invoice_months).update(net_amount=F('measured_amount'))
    plan.filter(period__in=invoice_months).update(net_amount=0)


def elected_sources():
    """-> {month: 'invoice' | 'plan'}, for explaining the election to the user.

    Reads the same figures sync_actual_source() elected on, so the note the
    upload shows cannot drift away from what the dashboard actually did.
    """
    from django.db.models import Sum

    out = {}
    for src, name in ((SalesRecord.SOURCE_INVOICE, 'invoice'),
                      (SalesRecord.SOURCE_PLAN, 'plan')):
        for r in (SalesRecord.objects.filter(source=src)
                  .exclude(net_amount=0).values('period')
                  .annotate(v=Sum('net_amount')).order_by()):
            if r['period']:
                out[r['period']] = name
    return out
