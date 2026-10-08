"""The daily GTR-head review sheet.

The third file in SalesIQ, beside the AOP sheet and the invoice dump. One row
per GTR head, circulated every morning, carrying that head's position across
three horizons: month to date, April to date, and the full financial year.

It is read into its own tables and never into SalesRecord -- see the note on
ReviewSnapshot for why. In short: every figure on it is a sum of transactions
already held, so loading it as sales would count the same rupee three times.

What is stored, and what is not
-------------------------------
Of the seventeen columns, six are inputs and the rest are arithmetic:

    AOP ACH %         MTD primary / month AOP
    Growth Over LM    MTD primary / LMTD - 1
    Backlog TGT FTM   month AOP - MTD primary
    YTD ACH %         YTD ACH / YTD AOP
    Backlog TGT YTD   YTD AOP - YTD ACH
    FY ACH %          FY ACH / FY AOP
    BACKLOG FY        FY AOP - FY ACH

Only the inputs are stored. The derived ones are recomputed on read, so that
correcting one cell cannot leave a percentage behind that disagrees with the
numbers printed beside it.

The sheet's own derived values are still READ -- as a check. If our addition
and the sheet's disagree, the import says so rather than quietly preferring
its own answer.
"""
import re
from datetime import date

from . import aop as AOP
from .ingest import _norm, parse_num


# -- columns --------------------------------------------------------------
#
# Fixed-text columns. The month-bearing ones (Sep-26 AOP, MTD Sep-26 PRI
# SALES) cannot be listed here because their text changes every month; they
# are matched by pattern below.
COLUMNS = {
    'channel':           ['chanel type', 'channel type'],
    'region':            ['region'],
    'head_name':         ['gtr head', 'gtr head name', 'head'],
    'sfo_count':         ['no of sfo', 'no of sfo s', 'nos of sfo'],
    'lmtd':              ['lmtd'],
    'yesterday_billing': ['yesterday billing'],
    'mtd_secondary':     ['mtd sec sales', 'mtd secondary sales', 'mtd sec'],
    'ytd_target':        ['ytd aop'],
    'ytd_actual':        ['ytd ach'],
}

# The sheet's own derived columns. Read as a check, never stored.
CHECKS = {
    'month_pct':     re.compile(r'^aop ach %?$'),
    'growth_pct':    re.compile(r'^growth over lm$'),
    'month_backlog': re.compile(r'^backlog tgt ftm$'),
    'ytd_pct':       re.compile(r'^ytd ach %$'),
    'ytd_backlog':   re.compile(r'^backlog tgt ytd$'),
    'fy_pct':        re.compile(r'^fy ach %$'),
    'fy_backlog':    re.compile(r'^backlog fy$'),
}

# "MTD Sep-26 PRI SALES", "MTD SEP 26 PRI SALES", "MTD PRI SALES"
_MTD_PRI = re.compile(r'^mtd\s*(?:([a-z]{3})\s*(\d{2}|\d{4})\s*)?pri(?:mary)?\s*sales$')
# "FY'26-27 AOP" / "FY 26 27 ACH". _norm turns the dash into a space but
# leaves the apostrophe alone, so the key arrives as "fy'26 27 aop" -- the
# apostrophe is optional here rather than assumed away.
_FY = re.compile(r"^fy\s*'?\s*(\d{2}|\d{4})\s*(\d{2}|\d{4})?\s*(aop|ach)$")


def _month_from(mon, yr):
    m = AOP.MONTHS.get((mon or '').lower())
    if not m:
        return None
    y = int(yr)
    return date(y + 2000 if y < 100 else y, m, 1)


def map_columns(header_row):
    """-> (field -> index, checks -> index, as-of month or None, unknown)."""
    lookup = {}
    for field, aliases in COLUMNS.items():
        for a in aliases:
            lookup[_norm(a)] = field

    cols, checks, unknown, as_of = {}, {}, [], None
    for ci, cell in enumerate(header_row):
        if cell is None or str(cell).strip() == '':
            continue
        key = _norm(cell)

        field = lookup.get(key)
        if field is not None:
            cols.setdefault(field, ci)
            continue

        m = _MTD_PRI.match(key)
        if m:
            cols.setdefault('mtd_primary', ci)
            if m.group(1):
                as_of = as_of or _month_from(m.group(1), m.group(2))
            continue

        m = _FY.match(key)
        if m:
            cols.setdefault('fy_target' if m.group(3) == 'aop' else 'fy_actual', ci)
            continue

        # "Sep-26 AOP" -- the one month column on this sheet, and the thing
        # that dates the whole snapshot.
        parsed = AOP.parse_month_header(cell)
        if parsed and parsed[1]:
            cols.setdefault('month_target', ci)
            as_of = as_of or parsed[0]
            continue

        for name, rx in CHECKS.items():
            if rx.match(key):
                checks.setdefault(name, ci)
                break
        else:
            unknown.append(str(cell).strip())

    return cols, checks, as_of, unknown


# Columns only this sheet has. Three of them together is the signature: the
# AOP sheet carries LMTD, YTD AOP and MTD SEC SALES too, so those cannot
# tell the two apart on their own.
SIGNATURE = ('month_backlog', 'growth_pct', 'ytd_backlog', 'fy_backlog', 'month_pct')


def looks_like_review_sheet(header_row):
    """Is this the daily review sheet rather than the AOP file or the dump?

    Checked BEFORE the AOP test, not after. This sheet carries exactly one
    month-AOP column where the AOP file carries twelve, so the AOP test
    (three or more) already says no -- but it carries NO OF SFO and the
    backlog columns, which nothing else does, and leaning on the absence of
    a test somewhere else is how a sheet silently lands in the wrong parser
    the day somebody adds a second month to it.
    """
    cols, checks, _, _ = map_columns(header_row)
    have = sum(1 for s in SIGNATURE if s in checks)
    return have >= 3 and 'head_name' in cols


# -- reading a row --------------------------------------------------------
#
# "GT Total", "OT Total", "Grand Total". They sit in the leftmost columns
# rather than in a column of their own.
_TOTAL = re.compile(r'\btotals?\b')

MONEY_FIELDS = ('lmtd', 'month_target', 'yesterday_billing', 'mtd_primary',
                'mtd_secondary', 'ytd_target', 'ytd_actual',
                'fy_target', 'fy_actual')


def is_total_row(row, cols):
    for field in ('channel', 'region', 'head_name'):
        ci = cols.get(field)
        if ci is None or ci >= len(row):
            continue
        if _TOTAL.search(_norm(row[ci])):
            return True
    return False


def read_row(row, cols):
    """-> field -> value, in the sheet's own units."""
    out = {}
    for field, ci in cols.items():
        v = row[ci] if ci < len(row) else None
        if field in MONEY_FIELDS:
            out[field] = parse_num(v, 0.0)
        elif field == 'sfo_count':
            out[field] = int(parse_num(v, 0.0))
        else:
            out[field] = str(v).strip() if v is not None else ''
    return out


def carry_forward(value, previous):
    """Merged cells read as blank below the first row of the merge.

    REGION is merged over the rows of a split territory -- GTR04 A covers
    both Arnab Ghosh and the handover line beneath him -- so the second row
    arrives with no region at all. Read literally, that row belongs to no
    territory and drops out of every grouping in the report.
    """
    return value if value else previous
