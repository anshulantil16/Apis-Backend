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
}
FILTERABLE = ['state', 'zone', 'area', 'city', 'region', 'category', 'sub_category',
              'brand', 'channel', 'salesperson', 'asm', 'rsm', 'customer_name', 'sku']


def _multi(request, key):
    """Collect a repeatable / comma-separated query param into a list."""
    vals = []
    for raw in request.query_params.getlist(key):
        vals += [v.strip() for v in str(raw).split(',') if v.strip()]
    return vals


def apply_dim_filters(qs, request):
    """Apply only the dimension filters (no dates). Split out so the
    previous-period comparison can reuse the exact same slice of the business
    while swapping the date window."""
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

