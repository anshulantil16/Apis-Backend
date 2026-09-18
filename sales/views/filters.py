"""Shared query contract for every SalesIQ analytics endpoint.

One filter vocabulary (date window + dimension filters) lives here so the whole
dashboard can drive every chart from a single filter bar, and so a query param
can never reach an arbitrary DB column.
"""
import io
from datetime import date, timedelta

import openpyxl
from django.db.models import Sum, Count, Min, Max
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from ..models import SalesUpload, SalesRecord
from ..ingest import (map_headers, parse_date, parse_num, build_template,
                      TEXT_FIELDS, NUM_FIELDS, TEXT_MAX)


# Dimensions a client is allowed to group/filter by. Whitelisted rather than
# passed straight through so a query param can never reach an arbitrary column.
DIMENSIONS = {
    'state': 'state', 'zone': 'zone', 'area': 'area', 'city': 'city', 'region': 'region',
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
}
FILTERABLE = ['state', 'zone', 'area', 'city', 'region', 'category', 'sub_category',
              'brand', 'channel', 'salesperson', 'asm', 'rsm', 'customer_name', 'sku',
              'sales_head', 'subzone', 'customer_district', 'business_type',
              'warehouse_type', 'location', 'variant', 'prod_group']


# The dump's Zone column doubles as a bucket for rows the business has
# already decided are not sales, and it says so in the value itself. They were
# being added to revenue and shown as a zone of their own on the zone chart --
# Rs 15.9 lakh of "NOT A PART OF SALES" sitting beside Delhi and Maharashtra.
# Excluded here rather than at import so the rows stay in the table and the
# file still reconciles line for line against the ERP.
NOT_SALES_ZONES = ['NOT A PART OF SALES']


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
    qs = qs.exclude(is_cancelled=True).exclude(zone__in=NOT_SALES_ZONES)
    applied = {}
    for f in FILTERABLE:
        vals = _multi(request, f)
        if vals:
            qs = qs.filter(**{f'{f}__in': vals})
            applied[f] = vals
    return qs, applied


def apply_filters(qs, request):
    """Shared filter parsing (dates + dimensions). Returns (qs, applied dict)."""
    qs, applied = apply_dim_filters(qs, request)
    d_from = parse_date(request.query_params.get('from'))
    d_to = parse_date(request.query_params.get('to'))
    if d_from:
        qs = qs.filter(order_date__gte=d_from)
        applied['from'] = d_from.isoformat()
    if d_to:
        qs = qs.filter(order_date__lte=d_to)
        applied['to'] = d_to.isoformat()
    return qs, applied


def _period_bounds(qs):
    agg = qs.aggregate(lo=models_min('order_date'), hi=models_max('order_date'))
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

