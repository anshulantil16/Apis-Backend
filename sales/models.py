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

    # ── Who a person IS, as opposed to what they are called ───────────────
    # The review sheet carries an APIS ID and a BIZOM ID beside each of its
    # people columns, and those are the real identity. A name is not: the
    # same person is spelled three ways across a year of exports, and two
    # people genuinely share one. Counting distinct names reported 23 RSMs
    # where the business has a different number, in both directions at once.
    # Written by aop.read_person_codes(), which says how the two IDs combine.
    sales_head_code = models.CharField(max_length=60, blank=True, db_index=True)
    rsm_code        = models.CharField(max_length=60, blank=True, db_index=True)
    asm_code        = models.CharField(max_length=60, blank=True, db_index=True)
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


def sync_actual_source():
    """Bring every row's net_amount back to its own measured figure.

    Which file answers a given question is decided per request, in
    apply_filters -- see below for why. This runs after every upload and
    every deletion to undo the old behaviour, where the losing source was
    zeroed in the table, and to keep the two columns in step for rows
    written before that changed.

    The two primary-sales files are not a detail copy and a summary copy of
    the same thing:

      * the Pre-Sales Dump is a live ERP extract -- line level, dated to the
        day, all channels including export, but only the month it was taken
        in, plus a tail of credit notes raised since against earlier months;
      * the AOP-vs-ACH sheet is the monthly review file -- no line detail, no
        customers, no SKUs, general trade only, but two full financial years
        of actuals beside the plan, and it is the record the business itself
        reads.

    THE REVIEW SHEET OWNS EVERY MONTH IT HAS A FIGURE FOR. The dump owns only
    the months the sheet does not reach.

    It used to be a contest, with the dump taking any month it accounted for
    at least half of. That made the dashboard disagree with the sheet the
    business has open beside it: September came from the dump, so the
    year-to-date figure was the sheet's YTD ACH minus the sheet's September
    plus the dump's -- a number that appears nowhere in either file and could
    not be reconciled against anything.

    The dump is still the record for a DATE range, because it is the only one
    of the two that knows what day anything happened on; that election is
    made per request in apply_filters, not here.

    Nothing is zeroed. net_amount is every row's own figure, and which rows
    a question is answered from is decided per request, in apply_filters --
    because the answer depends on the question. Zeroing the losing source
    could not express that: it made one of the two permanently worth nothing,
    so a date range asked of the dump came back empty.

    What this function now does is record which months the sheet speaks for,
    which is what apply_filters needs in order to leave the dump out of a
    month/year total without leaving it out of everything.
    """
    from django.db.models import F

    SalesRecord.objects.update(net_amount=F('measured_amount'))


def sheet_months():
    """Months the review sheet has a figure for.

    A month it carries at zero is not one it speaks for: an unstarted month
    sits in the sheet as an empty cell, and claiming it would silence the
    dump for a month the sheet never described.
    """
    from django.db.models import Sum

    return {r['period'] for r in
            (SalesRecord.objects.filter(source=SalesRecord.SOURCE_PLAN)
             .values('period').annotate(v=Sum('measured_amount')).order_by())
            if r['period'] and float(r['v'] or 0) != 0}


def elected_sources():
    """-> {month: 'invoice' | 'plan'} for a month/year view, to explain it.

    Decided by sheet_months(), which is the same thing apply_filters reads,
    so the note an upload shows cannot drift away from what the dashboard
    actually does. It used to infer the election from which rows had been
    zeroed; nothing is zeroed any more, and reading it that way reported
    every month as coming from both files at once.
    """
    covered = sheet_months()
    out = {m: 'plan' for m in covered}
    for r in (SalesRecord.objects.filter(source=SalesRecord.SOURCE_INVOICE)
              .exclude(period__in=covered).values('period').distinct()):
        if r['period']:
            out[r['period']] = 'invoice'
    return out
