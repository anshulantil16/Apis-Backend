"""The second primary-sales file: AOP vs ACH, month-wise per GTR person.

Where the Pre-Sales Dump is long — one row per invoice line — this sheet is
wide. One row is a person-and-item, and the year runs sideways across it in
three blocks of twelve:

    Apr-26 AOP ... Mar-27 AOP     the plan for this financial year
    Apr-25     ... Mar-26         last year's actuals
    Apr-26     ... Mar-27         this year's actuals

Those blocks are told apart by what is written in the header, not by where
the column sits: the " AOP" suffix marks a plan figure, and the two-digit
year separates last year from this one. Reading them positionally would
break the first time somebody inserts a column.

Each row is unpivoted into one record per month, so both files land in the
same table and every existing dashboard — trend, zone split, year-on-year —
works against them without knowing which file it is looking at.

The eight summary columns at the end (YTD AOP, YTD ACH, LYTD ACH, FY AOP,
FY ACH, LMTD, MTD SEC SALES) are deliberately not stored. Every one is its
own monthly columns added up, and storing a total beside the parts it is
made of is how a dashboard ends up reporting the year twice.
"""
import re
from datetime import date

from .ingest import _norm, parse_num

# Dimension columns, in this sheet's own vocabulary. The mapping to the
# canonical names was given by the business: this sheet's REGION is what the
# rest of SalesIQ calls a zone, and its Sub-Region is a state.
DIMENSIONS = {
    'channel':       ['chanel type', 'channel type'],
    'sales_head':    ['head'],
    'rsm':           ['gtr head'],
    'asm':           ['report incharge', 'reporting manager', 'report in charge',
                      'reporting incharge'],
    'zone':          ['region'],
    'state':         ['sub region'],
    'item_alt_code': ['i code', 'icode'],
    'product_name':  ['item name'],
    'brand':         ['brand'],
    'sfo_count':     ['no of sfo', 'no of sfo s', 'number of sfo'],
}

# Present, and deliberately not stored — see the module docstring.
SUMMARY_COLUMNS = [
    'YTD AOP', 'YTD ACH', 'LYTD ACH', "FY'26-27 AOP", "FY'26-27 ACH",
    'LMTD', 'MTD SEC SALES',
]
# 'Key' is a concatenation of the columns either side of it.
IGNORED = ['Key'] + SUMMARY_COLUMNS

MONTHS = {m: i for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
     'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], start=1)}

# "Apr-26", "Apr-26 AOP", "Apr 26", "APR-26 AOP"
_MONTH_RE = re.compile(r'^([a-z]{3})[\s\-]*(\d{2}|\d{4})(\s+aop)?$')


def parse_month_header(header):
    """-> (first-of-month date, is_target) for a month column, else None.

    A two-digit year is read as 20xx: this is a financial-year plan sheet and
    a column headed Apr-26 means April 2026, never 1926.
    """
    m = _MONTH_RE.match(_norm(header))
    if not m:
        return None
    mon, yr, aop = m.group(1), m.group(2), m.group(3)
    if mon not in MONTHS:
        return None
    year = int(yr)
    if year < 100:
        year += 2000
    return date(year, MONTHS[mon], 1), bool(aop)


def looks_like_aop_sheet(headers):
    """Is this the AOP-vs-ACH file rather than the Pre-Sales Dump?

    Decided on the plan columns, because they are what only this file has.
    A bare month column alone would not be enough — a dump could plausibly
    carry one.
    """
    targets = 0
    for h in headers:
        parsed = parse_month_header(h)
        if parsed and parsed[1]:
            targets += 1
    return targets >= 3


def map_columns(header_row):
    """-> (dimension field -> index, [(index, month, is_target)], unknown headers)."""
    lookup = {}
    for field, aliases in DIMENSIONS.items():
        for a in aliases:
            lookup[_norm(a)] = field

    dims, months, unknown = {}, [], []
    for ci, cell in enumerate(header_row):
        if cell is None or str(cell).strip() == '':
            continue
        parsed = parse_month_header(cell)
        if parsed:
            months.append((ci, parsed[0], parsed[1]))
            continue
        field = lookup.get(_norm(cell))
        if field is not None:
            dims.setdefault(field, ci)
        else:
            unknown.append(str(cell).strip())
    return dims, months, unknown


def unpivot(row, dims, months):
    """One wide row -> one dict per month that carries a figure.

    A month with neither a plan nor an actual is skipped rather than stored
    as a zero: a zero is a claim that nothing sold, and an empty cell in a
    part-finished year is not making that claim.
    """
    def dim(field):
        ci = dims.get(field)
        if ci is None or ci >= len(row):
            return ''
        v = row[ci]
        return '' if v is None else str(v).strip()

    base = {f: dim(f) for f in DIMENSIONS if f != 'sfo_count'}
    sfo = int(parse_num(row[dims['sfo_count']] if 'sfo_count' in dims
                        and dims['sfo_count'] < len(row) else 0))

    by_month = {}
    for ci, month, is_target in months:
        if ci >= len(row):
            continue
        raw = row[ci]
        if raw is None or str(raw).strip() == '':
            continue
        value = parse_num(raw)
        slot = by_month.setdefault(month, {'target': None, 'actual': None})
        # Last value wins for a repeated header, which only happens in a
        # malformed sheet; the alternative is silently adding them together.
        slot['target' if is_target else 'actual'] = value

    out = []
    for month in sorted(by_month):
        slot = by_month[month]
        if slot['target'] is None and slot['actual'] is None:
            continue
        out.append({
            **base,
            'sfo_count': sfo,
            'period': month,
            'target_amount': slot['target'] or 0.0,
            'net_amount': slot['actual'] or 0.0,
        })
    return out
