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


# ── the daily review sheet ────────────────────────────────────────────────
#
# A third file, beside the AOP sheet and the invoice dump: the one circulated
# each morning, one row per GTR head, carrying that head's MTD / YTD / FY
# position. It is deliberately NOT stored as SalesRecord rows.
#
# Every money figure on it is a sum of transactions already in this database.
# Loading it as sales would report the same rupee two and three times over --
# once as the invoice line, again inside MTD, again inside YTD -- and every
# headline on the dashboard would inflate the moment somebody uploaded the
# morning review.
#
# What it is instead: the business's own published statement of where each
# head stood on a given day. That has value the transactions do not have --
# it is what was circulated and acted on -- and it is what the per-person
# report is generated from. So it lives in its own two tables, read by the
# report and by nothing on the dashboard.
class ReviewSnapshot(models.Model):
    """One upload of the daily GTR-head review sheet."""
    upload = models.ForeignKey(SalesUpload, on_delete=models.CASCADE,
                               related_name='review_snapshots',
                               null=True, blank=True)
    filename    = models.CharField(max_length=255, blank=True)
    sheet_name  = models.CharField(max_length=120, blank=True)
    uploaded_by = models.CharField(max_length=200, blank=True)

    # The month the MTD columns describe, read off the sheet's own headers
    # ("Sep-26 AOP", "MTD Sep-26 PRI SALES") rather than from the clock, so
    # a sheet uploaded late still files itself under the month it reports.
    as_of_month = models.DateField(null=True, blank=True, db_index=True)
    # The day it describes is not on the sheet. Defaults to the upload date
    # and can be corrected, because "as at" is the one thing a report sent to
    # a person must not get wrong.
    as_of_date  = models.DateField(null=True, blank=True, db_index=True)

    # 'lakhs' or 'rupees' -- what the file was written in. Detected, not
    # assumed; everything below is stored in rupees whichever it was.
    source_unit = models.CharField(max_length=20, blank=True)

    row_count  = models.IntegerField(default=0)
    warnings   = models.JSONField(default=list, blank=True)
    notes      = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-as_of_date', '-created_at']

    def __str__(self):
        return f"review {self.as_of_date or self.created_at:%Y-%m-%d} — {self.row_count} heads"


class ReviewRow(models.Model):
    """One GTR head's line on the review sheet.

    Only the sheet's INPUT columns are stored. Every percentage and backlog
    on it -- AOP ACH %, Growth Over LM, Backlog TGT FTM / YTD / FY -- is
    arithmetic over the columns beside it, and is recomputed on read. Keeping
    a derived figure next to the figures it derives from is how the report
    and the sheet end up disagreeing after somebody edits one cell.
    """
    snapshot = models.ForeignKey(ReviewSnapshot, on_delete=models.CASCADE,
                                 related_name='rows')

    # Who and where. channel is GT / OT; region is the GTR code for GT and
    # the format name (MT, E-COM, Govt. Bus.) for OT.
    channel   = models.CharField(max_length=60, blank=True, db_index=True)
    region    = models.CharField(max_length=60, blank=True, db_index=True)
    head_name = models.CharField(max_length=200, blank=True, db_index=True)
    # Carried over from the AOP sheet's ID columns where the name matches, so
    # a report can be addressed to a person rather than to a spelling.
    head_code = models.CharField(max_length=60, blank=True, db_index=True)

    sfo_count = models.IntegerField(default=0)

    # Rupees. Month to date.
    lmtd              = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    month_target      = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    yesterday_billing = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    mtd_primary       = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    mtd_secondary     = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # Rupees. April to date, and the full financial year.
    ytd_target = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    ytd_actual = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    fy_target  = models.DecimalField(max_digits=18, decimal_places=2, default=0)
    fy_actual  = models.DecimalField(max_digits=18, decimal_places=2, default=0)

    # A subtotal line (GT Total / OT Total / Grand Total). Kept rather than
    # dropped, because it is the sheet's own statement of the total and so is
    # what the import checks its own addition against -- but never mixed in
    # with the head rows it is made of.
    is_total = models.BooleanField(default=False, db_index=True)
    row_no   = models.IntegerField(default=0)

    class Meta:
        ordering = ['row_no']
        indexes = [models.Index(fields=['snapshot', 'is_total'])]

    # ── everything the sheet derives, derived ─────────────────────────────
    @staticmethod
    def _pct(part, whole):
        """A percentage, or None where there is nothing to measure against.

        Zero is not the answer to "what percent of no plan": the handover row
        on the real sheet carries sales against a blank AOP, and reporting
        that as 0% tells the person they achieved nothing.
        """
        whole = float(whole or 0)
        if whole == 0:
            return None
        return round(float(part or 0) / whole * 100, 1)

    @property
    def month_pct(self):
        return self._pct(self.mtd_primary, self.month_target)

    @property
    def ytd_pct(self):
        return self._pct(self.ytd_actual, self.ytd_target)

    @property
    def fy_pct(self):
        """Progress through the annual plan, NOT performance against it.

        FY ACH equals YTD ACH all year -- nothing has been sold past today --
        so this reads low by construction until March. It is the one figure
        on the sheet most often mistaken for a score.
        """
        return self._pct(self.fy_actual, self.fy_target)

    @property
    def growth_pct(self):
        return None if not float(self.lmtd or 0) else round(
            (float(self.mtd_primary) / float(self.lmtd) - 1) * 100, 1)

    @property
    def month_backlog(self):
        return float(self.month_target) - float(self.mtd_primary)

    @property
    def ytd_backlog(self):
        return float(self.ytd_target) - float(self.ytd_actual)

    @property
    def fy_backlog(self):
        return float(self.fy_target) - float(self.fy_actual)

    def __str__(self):
        return f"{self.region} {self.head_name}"


# -- who may upload --------------------------------------------------------
#
# Ownership used to be settable only in code or in SALESIQ_ADMIN_EMAILS, which
# means a server visit and a restart to let one more person load the morning
# file. That is the right strictness for OWNERSHIP and the wrong strictness
# for a daily chore, so uploading is split off from owning.
#
# An uploader can load the morning workbook and build the reports. They cannot
# clear the data, and they cannot grant this to anybody else -- both stay with
# the one hard-coded super admin, because those are the two actions there is
# no way back from.
class UploaderGrant(models.Model):
    email      = models.EmailField(unique=True)
    name       = models.CharField(max_length=200, blank=True)
    granted_by = models.CharField(max_length=200, blank=True)
    is_active  = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['email']

    def __str__(self):
        return self.email


# -- who the reports go to -------------------------------------------------
#
# Two kinds of recipient, and the difference is what they are allowed to see.
#
#   head     their own territory and nothing else. The peer chart in their
#            report shows where they sit, which is the point of it, but they
#            get no file but their own.
#   manager  one rolled-up report across the territories they cover, plus
#            each of those heads' own files.
#
# A manager's coverage is stated here rather than inferred from the sheet.
# The Region Summary tab carries channel, region and head -- it does not carry
# who those heads report to, and guessing a reporting line from a spreadsheet
# is how somebody receives a territory that is not theirs.
class ReportRecipient(models.Model):
    ROLE_HEAD    = 'head'
    ROLE_MANAGER = 'manager'
    ROLES = [(ROLE_HEAD, 'Head — their own territory'),
             (ROLE_MANAGER, 'Manager — everyone they cover')]

    role  = models.CharField(max_length=20, default=ROLE_HEAD, db_index=True)
    name  = models.CharField(max_length=200, blank=True)
    email = models.EmailField(db_index=True)

    # For a head: which row on the sheet is theirs. Matched against APIS ID
    # first, then region, then name -- an ID because a name is not an
    # identity and is spelled several ways across a year of exports.
    head_key = models.CharField(max_length=120, blank=True, db_index=True)

    # For a manager: the regions they cover. Empty means every region, which
    # is a real case (a national head) and so has to be said deliberately
    # rather than being what an unfilled field happens to mean -- see
    # covers_all.
    regions     = models.JSONField(default=list, blank=True)
    covers_all  = models.BooleanField(default=False)

    is_active    = models.BooleanField(default=True, db_index=True)
    last_sent_at = models.DateTimeField(null=True, blank=True)
    note         = models.CharField(max_length=300, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['role', 'name', 'email']
        constraints = [
            # One person, one row per role. A duplicated head row means two
            # identical files landing in the same inbox each morning, which
            # reads as the system being broken.
            models.UniqueConstraint(fields=['email', 'role', 'head_key'],
                                    name='one_row_per_person_per_role'),
        ]

    def __str__(self):
        return f'{self.email} ({self.role})'

    def covered_regions(self, all_regions):
        """-> the regions this manager's report covers."""
        if self.covers_all:
            return list(all_regions)
        return [r for r in all_regions if r in (self.regions or [])]
