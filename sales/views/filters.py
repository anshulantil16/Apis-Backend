"""Shared query contract for every SalesIQ analytics endpoint.

One filter vocabulary (date window + dimension filters) lives here so the whole
dashboard can drive every chart from a single filter bar, and so a query param
can never reach an arbitrary DB column.
"""
from datetime import date, timedelta

from django.db.models import Q

from ..models import SalesRecord, sheet_months
from ..ingest import (parse_date)


# Dimensions a client is allowed to group/filter by. Whitelisted rather than
# passed straight through so a query param can never reach an arbitrary column.
# NOTE on `region`: there is deliberately no such dimension. The review
# sheet's REGION column is read into `zone`, which the screen labels Region,
# and its Sub-Region into `state`, labelled Sub-Region. A separate `region`
# dimension existed for a column neither file has, so the dashboard offered
# Region twice -- once populated from the sheet, and once in the "no column
# for these" list telling the reader to add a column their file already has.
DIMENSIONS = {
    'state': 'state', 'zone': 'zone', 'area': 'area', 'city': 'city',
    'category': 'category', 'sub_category': 'sub_category', 'product': 'product_name',
    'product_name': 'product_name', 'sku': 'sku', 'brand': 'brand', 'pack_size': 'pack_size',
    'channel': 'channel', 'customer': 'customer_name', 'customer_name': 'customer_name',
    'customer_type': 'customer_type',
    'salesperson': 'salesperson', 'asm': 'asm', 'rsm': 'rsm', 'territory': 'territory',

    # From the two primary-sales files. The sales hierarchy runs
    # head > rsm > asm, and a dump row also knows which depot billed it and
    # what kind of customer and warehouse it went to.
    'sales_head': 'sales_head', 'subzone': 'subzone', 'district': 'customer_district',
    'business_type': 'business_type', 'warehouse_type': 'warehouse_type',
    'location': 'location',
    # Product hierarchy below sub-category.
    'variant': 'variant', 'prod_group': 'prod_group', 'item_sub_type': 'item_sub_type',
    'packaging_type': 'packaging_type', 'batch': 'batch_no',

    # Columns the dump carries that nothing could group or filter by before.
    # Customer Type is the most useful of them: Export / Modern Trade /
    # General Trade / Other is the cleanest split of the business in either
    # file, and it was being stored and then ignored.
    'uom': 'uom', 'location_state': 'location_state', 'hsn': 'hsn_code',
    'gst_jurisdiction': 'gst_jurisdiction', 'currency': 'currency_code',
    'line_type': 'line_type', 'gl_account': 'gl_account_name',
    # The dump's V-REMARS, which is the business's own classification of
    # every line: SALES, SR and GOOD SR (stock came back), SCHEME CN (an
    # offer settled later by credit note) and NOT A PART OF SALES. Worth
    # grouping by in its own right -- how much came back, and on what.
    'transaction_type': 'remarks',
}
FILTERABLE = ['state', 'zone', 'area', 'city', 'category', 'sub_category',
              'brand', 'channel', 'salesperson', 'asm', 'rsm', 'customer_name', 'sku',
              'sales_head', 'subzone', 'customer_district', 'business_type',
              'warehouse_type', 'location', 'variant', 'prod_group',
              # See DIMENSIONS above: carried by the dump, previously unusable.
              'customer_type', 'pack_size', 'item_sub_type', 'packaging_type',
              'uom', 'location_state', 'gst_jurisdiction', 'currency_code',
              'line_type', 'gl_account_name', 'hsn_code', 'remarks']


# The dump's Zone column doubles as a bucket for rows the business has
# already decided are not sales, and it says so in the value itself. They were
# being added to revenue and shown as a zone of their own on the zone chart --
# Rs 15.9 lakh of "NOT A PART OF SALES" sitting beside Delhi and Maharashtra.
# Excluded here rather than at import so the rows stay in the table and the
# file still reconciles line for line against the ERP.
# Zones whose rows are not primary sales, per the business.
#
# B2B and EXPORT are a different book of business: the review sheet that
# carries the plan is general trade only and has neither in it, so counting
# them on the invoice side put customers, SKUs and orders on screen that the
# target they sit beside was never set against.
#
# Matched case-insensitively, because the dump writes these by hand and
# `zone__in` is exact: one row reading "Export" rather than "EXPORT" would
# have walked straight through.
# Finished goods carry an Item Code beginning FG. Everything else in that
# column -- raw material, packaging, consumables -- can appear on a sales
# invoice without being a product the company sells.
FINISHED_GOODS_PREFIX = 'FG'

# A person is counted by their ID, never by their name -- see
# aop.read_person_codes for why, and for how the two IDs combine.
PERSON_CODE_FIELD = {
    'sales_head': 'sales_head_code',
    'rsm':        'rsm_code',
    'asm':        'asm_code',
}


def count_people(querysets, level, with_vacancies=False):
    """How many distinct PEOPLE a level holds, across several querysets.

    Identity is the ID where the sheet gave one. Where it did not -- the
    invoice dump carries no ID columns at all -- the name stands in, but
    only for people no coded row already accounted for: the same RSM named
    on the sheet with an ID and on the dump without one is one person, and
    counting both halves reported them twice.

    An unfilled territory is not a person. The sheet writes VACANT-TRI in
    the name column for one, and counting those made the ASM headcount the
    number of TERRITORIES rather than the number of managers -- a figure
    that goes UP as the company leaves more positions open. They are counted
    separately instead, because how many seats are empty is worth knowing,
    just not under the heading "ASM".
    """
    from ..ingest import is_vacant

    code_field = PERSON_CODE_FIELD.get(level)
    coded, named, spoken_for, vacant = set(), set(), set(), set()
    for qs in querysets:
        if qs is None:
            continue
        fields = [level] + ([code_field] if code_field else [])
        for row in qs.exclude(**{level: ''}).values_list(*fields).order_by().distinct():
            name = row[0]
            code = row[1] if code_field else ''
            if is_vacant(name):
                vacant.add(name)
                continue
            if code:
                coded.add(code)
                spoken_for.add(name)
            else:
                named.add(name)
    total = len(coded) + len(named - spoken_for)
    return (total, len(vacant)) if with_vacancies else total


# ── the earliest month anything on screen may show ───────────────────────
#
# Last year is loaded on purpose: the review sheet carries FY25-26 beside
# FY26-27 so growth, the year-on-year comparison and the forecast's fit have
# something to stand on. None of that is a reason to PUT it on screen, and
# putting it there did real damage -- the revenue trend opened on May 2025
# and ran a year of actuals against an AOP of zero, because last year has no
# plan in this file. A reader seeing twelve months at 0% of plan reasonably
# concludes the dashboard is broken.
#
# So the two uses are separated. Reads that answer "what is on screen" stop
# at this floor. The comparison helpers below -- money_base(),
# same_months_last_year(), the year-on-year view -- deliberately reach past
# it, because computing a growth percentage out of last year is exactly the
# backend work last year is loaded for.
#
# Settable, because the floor is a financial year and financial years end.
DISPLAY_FROM = date(2026, 4, 1)


def display_floor():
    """The earliest month any on-screen figure may include."""
    try:
        from django.conf import settings
        raw = getattr(settings, 'SALESIQ_DISPLAY_FROM', None)
        if isinstance(raw, date):
            return raw
        if raw:
            y, m = str(raw).split('-')[:2]
            return date(int(y), int(m), 1)
    except Exception:
        pass
    return DISPLAY_FROM


NOT_SALES_ZONES = ['NOT A PART OF SALES', 'B2B', 'EXPORT']


def _not_sales_zone_q():
    q = Q()
    for z in NOT_SALES_ZONES:
        q |= Q(zone__iexact=z)
    return q


def sheet_zones():
    """The zones the review sheet carries -- the business the plan is set for.

    A whitelist, derived from the sheet itself, rather than a hand-written
    list of what to drop. The dump carries rows the plan was never set
    against -- EXPORT, B2B, CPC -- and counting them put customers, orders
    and revenue on screen beside a target that does not cover them.

    Derived rather than listed because the list would need maintaining: a
    zone added to the ERP next month is counted until somebody remembers to
    exclude it, and nothing on screen would say so. This way the question is
    always "does the plan cover it", which is the question that matters.

    It also keeps what a hand-written list would have got wrong: the sheet
    carries MT, E-COM and Govt. Bus., so they ARE part of the plan and
    belong in the figure, however little they look like general trade.
    """
    return {z for z in
            SalesRecord.objects.filter(source=SalesRecord.SOURCE_PLAN)
            .exclude(zone='').values_list('zone', flat=True).distinct()}


def in_plan_scope(qs):
    """Drop the zones that are not primary sales: B2B and EXPORT.

    The single statement of the rule. The headline, the comparisons behind
    it and the figure an upload reports all have to apply it identically --
    when they did not, a comparison counted a zone the headline above it had
    dropped, and the growth between them was an artefact of the difference.

    This was briefly a whitelist derived from the review sheet, which also
    dropped CPC because the sheet has no plan for it. That is defensible and
    it is not what the business counts: CPC sales are sales. The rule is the
    two zones named, and only those -- so a figure here is the one the
    business arrives at by hand, which is the only test that matters.
    """
    return qs.exclude(_not_sales_zone_q())


def _multi(request, key):
    """Collect a repeatable / comma-separated query param into a list."""
    vals = []
    for raw in request.query_params.getlist(key):
        vals += [v.strip() for v in str(raw).split(',') if v.strip()]
    return vals


def pending_months(qs):
    """Months with a plan against them and nothing measured yet.

    A financial year is loaded with twelve months of plan the day it opens, so
    from April the table holds targets for months that have not happened. They
    aggregate to revenue of zero, which is not the same claim as "nothing
    sold" -- and every statistic in the dashboard was reading it as though it
    were: the mean of the last two years was computed over six months of
    not-yet, the seasonal index for October to March came out half what it
    should be, and the trend line fell off a cliff at the end of the year.
    """
    from django.db.models import Sum as _Sum
    return {r['period'] for r in
            (qs.values('period')
               .annotate(measured=_Sum('measured_amount'),
                         planned=_Sum('target_amount'))
               .order_by())
            if r['period'] and float(r['planned'] or 0) > 0
            and float(r['measured'] or 0) == 0}


def same_months_last_year(qs):
    """This financial year to date, against the same months of the last one.

    The dashboard already compares against "the prior period", meaning the
    stretch of equal length immediately before. For a business with a festive
    quarter that compares Christmas with the monsoon and calls the difference
    growth. This lines April up with April, which is what the review sheet's
    LYTD ACH column does and what everybody actually means by "up on last
    year".

    Deliberately ignores the date filter: narrowing to this year should not
    take last year's comparison away with it.
    """
    from django.db.models import Sum as _Sum
    from ..analytics import financial_year, fy_label

    rows = (qs.values('period')
              .annotate(earned=_Sum('net_amount'), measured=_Sum('measured_amount'),
                        planned=_Sum('target_amount'))
              .order_by('period'))

    by_fy = {}
    for r in rows:
        d = r['period']
        if not d:
            continue
        # A month still ahead of the business is not a month that sold zero.
        if float(r['planned'] or 0) > 0 and float(r['measured'] or 0) == 0:
            continue
        by_fy.setdefault(financial_year(d), {})[d.month] = float(r['earned'] or 0)

    if len(by_fy) < 2:
        return None
    years = sorted(by_fy)
    this_fy, last_fy = years[-1], years[-2]
    shared = sorted(set(by_fy[this_fy]) & set(by_fy[last_fy]))
    if not shared:
        return None

    now = sum(by_fy[this_fy][m] for m in shared)
    before = sum(by_fy[last_fy][m] for m in shared)
    return {
        'this_year': round(now, 2),
        'last_year': round(before, 2),
        'growth_pct': round((now / before - 1) * 100, 1) if before else None,
        'months': len(shared),
        'this_label': fy_label(this_fy),
        'last_label': fy_label(last_fy),
    }


def comparable_window(qs):
    """Revenue and plan over the months where BOTH exist.

    Achievement was being computed as total revenue over total plan, and in
    the real file those two cover different stretches of time: eighteen
    months of sales (two financial years) against twelve months of plan (the
    current one). That reported 86% -- the business's own sheet, comparing
    like with like in its YTD columns, said 66%.

    The two fail to line up from both ends, so both ends have to be cut:

      * Apr 2025 to Mar 2026 is sales with no plan against it -- last year,
        loaded so the dashboard can show history and growth. Counting it
        towards this year's plan inflates achievement.
      * Oct 2026 to Mar 2027 is plan with no sales against it yet -- the rest
        of the financial year. Counting it deflates achievement, and is the
        reason a healthy business reads as though it is missing target.

    What is left is the overlap, which is what "achievement" has always meant
    and is exactly what the review sheet's YTD AOP and YTD ACH columns hold.

    -> {'revenue', 'target', 'pct', 'months', 'from', 'to'}
    """
    from django.db.models import Sum as _Sum

    rows = (qs.values('period')
              .annotate(planned=_Sum('target_amount'),
                        measured=_Sum('measured_amount'),
                        earned=_Sum('net_amount'))
              .order_by('period'))

    revenue = target = 0.0
    months = []
    # The other side of the same cut: months that carry a plan and nothing
    # against it yet. They are excluded from achievement for the reason
    # above, and they are exactly what is left to play for -- which is what
    # a required run rate is divided by.
    ahead, ahead_target = [], 0.0
    for r in rows:
        planned = float(r['planned'] or 0)
        measured = float(r['measured'] or 0)
        if planned <= 0 or not r['period']:
            continue
        # A month counts only if it has a plan AND something actually
        # happened in it. Nothing measured against a plan means the month is
        # still ahead of the business, not that it sold nothing.
        if measured == 0:
            ahead.append(r['period'])
            ahead_target += planned
            continue
        revenue += float(r['earned'] or 0)
        target += planned
        months.append(r['period'])

    return {
        'revenue': round(revenue, 2),
        'target': round(target, 2),
        'pct': round(revenue / target * 100, 1) if target else None,
        'months': len(months),
        'from': months[0].isoformat() if months else None,
        'to': months[-1].isoformat() if months else None,
        # Still to come, within whatever window is on screen.
        'months_ahead': len(ahead),
        'target_ahead': round(ahead_target, 2),
        'ahead_from': ahead[0].isoformat() if ahead else None,
        'ahead_to': ahead[-1].isoformat() if ahead else None,
    }


def with_actuals(qs):
    """The same slice of the business, minus months still ahead of it.

    For anything that computes a statistic -- a mean, a seasonal index, a
    forecast. Views that show the plan itself keep the full span and mark
    those months instead.
    """
    return qs.exclude(period__in=pending_months(qs))


def apply_dim_filters(qs, request):
    """Apply only the dimension filters (no dates). Split out so the
    previous-period comparison can reuse the exact same slice of the business
    while swapping the date window.

    Cancelled documents are dropped here, which makes this and apply_filters
    the two doors every dashboard read passes through on its way to a figure.
    Doing it per view instead would mean twenty places to remember, and the
    one that got forgotten would quietly report a cancelled invoice as
    revenue.
    """
    qs = in_plan_scope(qs.exclude(is_cancelled=True).exclude(is_not_sales=True))
    applied = {}
    for f in FILTERABLE:
        vals = _multi(request, f)
        if vals:
            qs = qs.filter(**{f'{f}__in': vals})
            applied[f] = vals
    return qs, applied


def latest_financial_year(qs):
    """The financial year the data actually reaches, as its opening year.

    Read from the newest month carrying a measured figure rather than from
    today's clock. A file exported in March and opened in April still
    describes the year it describes, and a test fixture does not change
    meaning depending on the day it is run.
    """
    from django.db.models import Max as _Max
    from ..analytics import financial_year

    newest = qs.exclude(measured_amount=0).aggregate(d=_Max('period'))['d']
    return financial_year(newest) if newest else None


def financial_year_window(qs):
    """-> (1 April, 31 March, label) for the year the data reaches.

    (None, None, None) when nothing is loaded.
    """
    from ..analytics import FY_START_MONTH, fy_label

    y = latest_financial_year(qs)
    if y is None:
        return None, None, None
    return (date(y, FY_START_MONTH, 1),
            date(y + 1, FY_START_MONTH, 1) - timedelta(days=1),
            fy_label(y))


def month_start(value):
    """'2026-04', '2026-04-15' or a date -> the 1st of that month, or None.

    The screen asks by month now, not by day. Both spellings are accepted so
    that a link someone saved with a full date still resolves to the month it
    falls in rather than failing.
    """
    if not value:
        return None
    raw = str(value).strip()
    d = parse_date(raw if len(raw) > 7 else raw + '-01')
    return d.replace(day=1) if d else None


def month_end(value):
    """Same, but the LAST day of that month.

    A month range that ended on the 1st would take in only the sheet rows,
    which are stored on the 1st, and none of the invoices after them.
    """
    d = month_start(value)
    if not d:
        return None
    nxt = d.replace(year=d.year + 1, month=1) if d.month == 12 else d.replace(month=d.month + 1)
    return nxt - timedelta(days=1)


def apply_filters(qs, request, default_window=True, money_scope=True, floor=True):
    """Shared filter parsing (dates + dimensions). Returns (qs, applied dict).

    With no date filter given, the window defaults to the CURRENT FINANCIAL
    YEAR rather than to everything in the table.

    Both files hold more than one year -- the review sheet carries last year's
    actuals beside this year's so the dashboard can show growth -- and summing
    the lot gave a headline of Rs 298 crore labelled "revenue", under a
    subtitle comparing it with six months of last year. It was eighteen
    months of two financial years standing where the business reads its year
    to date, and it did not match the figure the business has in front of it:
    the review sheet's own YTD ACH column.

    The point is that one default reaches every endpoint at once -- the
    headline, the breakdowns, the rankings, the export -- so they all describe
    the same stretch of time. Views that exist to look ACROSS years (the trend
    line, the forecast, growth and seasonality) pass default_window=False,
    because for them history is the subject rather than noise.

    There is no way to turn the window off. An "all history" option used to
    do it, and what it actually showed was both financial years of the review
    sheet added together -- eighteen months of two years standing where the
    business reads its year to date, which is the Rs 298 crore figure that
    appears in neither file. A window that wide is still available by naming
    the months, where at least the screen says which ones.

    money_scope=False keeps both files in scope regardless of the dates. The
    panels built out of invoice detail -- customers, SKUs, order sizes --
    pass it, because they are reading the dump either way and the rule above
    would only ever take rows away from them.
    """
    qs, applied = apply_dim_filters(qs, request)
    # Last year is loaded for the maths, not for the screen. Everything that
    # answers "what am I looking at" stops here; the comparison helpers that
    # genuinely need history go around this function, not through it.
    #
    # floor=False is for a figure that is COMPUTED from history rather than
    # being history on display: a seasonal index for April is worth having
    # precisely because two Aprils went into it, and it shows a multiplier,
    # never last April's rupees. Anything that puts a month on an axis or in
    # a table keeps the floor.
    if floor:
        lo = display_floor()
        qs = qs.filter(order_date__gte=lo)
        applied['display_from'] = lo.strftime('%Y-%m')
    # The window is a RANGE OF MONTHS, not of days. `month_from`/`month_to`
    # are what the screen sends; `from`/`to` are still read so that a saved
    # link keeps working, and they are snapped to whole months too.
    #
    # Day-level ranges are gone deliberately. They could only ever be
    # answered by the invoice dump, which covers one month, while the review
    # sheet -- the record the business actually keeps, and the one the
    # targets live in -- has no days in it at all: every row is a month,
    # stored on the 1st. So a day range quietly swapped which file answered
    # and left the target behind, and the same question asked by month and by
    # day came back with two different numbers. One grain, one file, one
    # answer.
    m_from = month_start(request.query_params.get('month_from')
                         or request.query_params.get('from'))
    m_to = month_end(request.query_params.get('month_to')
                     or request.query_params.get('to'))

    if money_scope:
        # The review sheet answers for every month it speaks for, whatever
        # the window. The dump's rows for those months are left out of the
        # total rather than zeroed -- they are still the only record of who
        # bought what, and detail_qs below reads them.
        covered = sheet_months()
        if covered:
            qs = qs.exclude(source=SalesRecord.SOURCE_INVOICE, period__in=covered)
        # Said out loud rather than left to be inferred: which of the two
        # files a figure came from is the first thing anyone asks when a
        # number looks wrong, and the screen could not answer it.
        applied['source'] = 'review_sheet' if covered else 'invoice_dump'

    if default_window and not m_from and not m_to:
        fy_from, fy_to, label = financial_year_window(qs)
        if fy_from:
            m_from, m_to = fy_from, fy_to
            # Flagged so the screen can say which year it is showing and
            # offer to widen it. A default nobody can see is a default
            # nobody can correct.
            applied['window'] = {'basis': 'financial_year', 'label': label,
                                 'from': fy_from.isoformat(),
                                 'to': fy_to.isoformat()}
    if m_from:
        qs = qs.filter(order_date__gte=m_from)
        applied['from'] = m_from.isoformat()
        applied['month_from'] = m_from.strftime('%Y-%m')
    if m_to:
        qs = qs.filter(order_date__lte=m_to)
        applied['to'] = m_to.isoformat()
        applied['month_to'] = m_to.strftime('%Y-%m')
    return qs, applied


# What the review sheet can be grouped by. Everything else -- customer, SKU,
# city, batch, HSN, the rest of the dump's 70 columns -- exists only on an
# invoice, so a breakdown by it has to be read off the dump whatever window
# is on screen. Taken from the sheet's own column contract so the two cannot
# drift apart.
def sheet_fields():
    from ..aop import DIMENSIONS as AOP_DIMENSIONS
    # `subzone` is not one of the sheet's own column names, so it is added
    # here rather than taken from the contract above. The sheet's Sub-Region
    # is stored there verbatim -- MH-1, KA-5, Lulu -- while `state` holds a
    # state name derived from that code so the two files name states alike.
    # The dump fills subzone too, with its own vocabulary (Delhi NCR), so
    # without this the Sub-Region list would hold both at once.
    return {f for f in AOP_DIMENSIONS if f != 'sfo_count'} | {'subzone'}


def money_base(request, field=None, dims=True, dump_only=False):
    """Dimension filters only -- no date window -- but source-scoped.

    For the comparisons that deliberately reach outside the window on screen:
    the prior period, this year against the same months last year, the growth
    quadrant and the movers list. They all need history, and they all need it
    counted once. Reaching for apply_dim_filters directly gave them history
    with both files in it, so every month the two describe together was
    counted twice -- in exactly the comparisons nobody checks by hand.
    """
    base, _ = apply_dim_filters(SalesRecord.objects.all(), request)
    if not dims:
        # Everything, still source-scoped and still minus cancelled rows --
        # the "whole company" side of a year-on-year comparison.
        base = in_plan_scope(SalesRecord.objects.exclude(is_cancelled=True)
                             .exclude(is_not_sales=True))
    if dump_only or (field is not None and field not in sheet_fields()):
        return base.filter(source=SalesRecord.SOURCE_INVOICE)
    covered = sheet_months()
    if covered:
        base = base.exclude(source=SalesRecord.SOURCE_INVOICE, period__in=covered)
    return base


def qs_for_dimension(request, field):
    """-> (queryset, applied, from_dump) for a breakdown by `field`.

    The review sheet can be split by channel, head, RSM, ASM, zone, state,
    item and brand, and by nothing else. Customer, SKU, category, city,
    batch, HSN and the rest of the dump's seventy columns exist only on an
    invoice, so a breakdown by one of those has to come off the dump whatever
    window is on screen -- otherwise it empties out the moment the money is
    being read from the sheet, which is most of the time.
    """
    if field in sheet_fields():
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        return qs, applied, False
    qs, applied = apply_filters(SalesRecord.objects.all(), request,
                                money_scope=False)
    applied['source'] = 'invoice_dump'
    return qs.filter(source=SalesRecord.SOURCE_INVOICE), applied, True


def filters_the_dump_cannot_answer(applied):
    """-> the applied filters the invoice file has no column for.

    The review sheet and the invoice dump do not carry the same columns, and
    Channel is the one that bites: the sheet has CHANEL TYPE, the dump's
    export does not, so every dump row holds a blank channel. Filter to GT
    and the money narrows correctly off the sheet while every panel built out
    of invoice detail -- customers, SKUs, categories, cities, order sizes --
    silently empties, because a blank channel is not GT.

    Empty is the wrong answer. "No categories" reads as "nothing sold in GT",
    which is false; the truth is that the invoice file cannot be asked the
    question. That is worth saying on screen, and it is worth saying which
    filter did it, because the remedy is a column in an export rather than
    anything in this code.
    """
    blind = []
    for field, vals in (applied or {}).items():
        if field not in FILTERABLE or not vals:
            continue
        has_any = (SalesRecord.objects
                   .filter(source=SalesRecord.SOURCE_INVOICE)
                   .exclude(**{field: ''}).exists())
        if not has_any:
            blind.append(field)
    return blind


def dim_applied(request):
    """-> the dimension filters in force, without running the whole door.

    The invoice-only panels threw this away and reported only
    {'source': 'invoice_dump'}, so when one of them came back empty there was
    nothing left to say WHICH filter emptied it.
    """
    _, applied = apply_dim_filters(SalesRecord.objects.none(), request)
    return applied


def why_empty(applied, from_dump, has_rows):
    """-> why a panel came back with nothing, or None if it did not.

    An empty panel is a sentence the reader completes for themselves, and
    they complete it wrongly. "No categories" under a Channel filter reads as
    "nothing sold in GT"; what it actually means is that the invoice file has
    no channel column, so the question could not be put to it. Those two are
    opposite in meaning and identical on screen.

    Returned as a reason plus the fields involved rather than as finished
    text, so the labels stay in the one place that already knows them -- the
    screen calls a zone a Region, and the backend should not have to.
    """
    if has_rows:
        return None
    blind = filters_the_dump_cannot_answer(applied) if from_dump else []
    if blind:
        return {'reason': 'no_column', 'fields': blind}
    narrowed = [f for f in applied if f in FILTERABLE]
    if narrowed:
        return {'reason': 'filtered_out', 'fields': narrowed}
    return {'reason': 'nothing_loaded', 'fields': []}


def detail_qs(request):
    """The invoice dump alone, under the current filters.

    Everything the review sheet has none of is read through here: customers,
    SKUs, products below brand, invoice counts, order sizes. Those panels
    cover only the months the dump reaches, which is why the overview reports
    `invoiced_pct` beside them -- what share of the money on screen they
    actually describe.
    """
    qs, _ = apply_filters(SalesRecord.objects.all(), request, money_scope=False)
    return qs.filter(source=SalesRecord.SOURCE_INVOICE)


def _period_bounds(qs):
    """The window the data actually covers: months something happened in.

    A financial year is loaded with twelve months of plan the day it opens,
    so from April the table holds rows for months still ahead of the
    business -- a target, and nothing else. Those rows carry an order_date,
    so max() over them put the end of "the current window" in February 2027.

    Everything measured from these bounds then compared unlike things. The
    prior-period figure under the revenue tile ran eleven months back
    against six months of sales and called the difference growth; the growth
    quadrant and the movers list split the same way.
    """
    agg = (qs.exclude(period__in=pending_months(qs))
             .aggregate(lo=models_min('order_date'), hi=models_max('order_date')))
    return agg['lo'], agg['hi']


# Small indirections so the imports above stay tidy.
def models_min(f):
    from django.db.models import Min
    return Min(f)


def models_max(f):
    from django.db.models import Max
    return Max(f)


def _money(v):
    return round(float(v or 0), 2)


def _pct_change(cur, prev):
    """Growth %. None when there's no baseline — 0 would read as 'flat', which
    is a different and misleading statement."""
    if not prev:
        return None
    return round(((cur - prev) / prev) * 100, 1)

