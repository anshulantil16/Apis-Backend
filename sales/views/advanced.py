"""SalesIQ advanced analytics endpoints.

Thin HTTP wrappers — all the maths lives in sales/analytics.py so the formulas
can be unit-tested without going through a request.
"""
import io
from datetime import date, timedelta

import openpyxl
from django.db.models import Sum, Count, Min, Max
from django.http import HttpResponse
from rest_framework.views import APIView
from .auth import SalesIQAdminView, SalesIQView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from ..models import SalesUpload, SalesRecord
from ..ingest import (map_headers, parse_date, parse_num, build_template,
                      TEXT_FIELDS, NUM_FIELDS, TEXT_MAX)

from .. import analytics as AN
from .filters import (DIMENSIONS, apply_filters, apply_dim_filters, _period_bounds,
                     detail_qs, qs_for_dimension, money_base,
                      with_actuals)



def _dim_or_400(request, default='state'):
    key = (request.query_params.get('dim') or default).strip().lower()
    field = DIMENSIONS.get(key)
    return key, field


class SalesParetoView(SalesIQView):
    """80/20 concentration with ABC classification."""
    def get(self, request):
        key, field = _dim_or_400(request, 'customer')
        if not field:
            return Response({'error': f'Unknown dimension "{key}".',
                             'available': sorted(DIMENSIONS.keys())}, status=400)
        qs, applied, _dump = qs_for_dimension(request, field)
        data = AN.pareto(qs, field)
        data.update({'dimension': key, 'filters': applied})
        return Response(data)


class SalesMatrixView(SalesIQView):
    """Revenue-vs-growth quadrant (star / cash cow / rising / watch)."""
    def get(self, request):
        key, field = _dim_or_400(request, 'product')
        if not field:
            return Response({'error': f'Unknown dimension "{key}".'}, status=400)
        qs, applied, _dump = qs_for_dimension(request, field)
        lo, hi = _period_bounds(qs)
        # Source-scoped, or the window before this one counts both files for
        # every month they describe together and every growth figure is wrong.
        data = AN.growth_matrix(money_base(request, field), field, lo, hi)
        data.update({'dimension': key, 'filters': applied})
        return Response(data)


class SalesMoversView(SalesIQView):
    """Biggest absolute gainers and losers vs the prior equal window."""
    def get(self, request):
        key, field = _dim_or_400(request, 'state')
        if not field:
            return Response({'error': f'Unknown dimension "{key}".'}, status=400)
        qs, applied, _dump = qs_for_dimension(request, field)
        lo, hi = _period_bounds(qs)
        data = AN.movers(money_base(request, field), field, lo, hi)
        data.update({'dimension': key, 'filters': applied})
        return Response(data)


class SalesAnomaliesView(SalesIQView):
    # An outlier is only an outlier against a run of other months.
    def get(self, request):
        try:
            z = max(1.0, min(4.0, float(request.query_params.get('z', 2.0))))
        except (TypeError, ValueError):
            z = 2.0
        qs, applied = apply_filters(SalesRecord.objects.all(), request,
                                    default_window=False)
        # A month that has not happened is not an anomaly.
        data = AN.anomalies(with_actuals(qs), z=z)
        data['filters'] = applied
        return Response(data)


class SalesSeasonalityView(SalesIQView):
    # A seasonal index needs the same month in more than one year.
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request,
                                    default_window=False)
        # Averaging October over a year that has run and one that has not
        # halved every month in the back half of the financial year.
        data = AN.seasonality(with_actuals(qs))
        data['filters'] = applied
        return Response(data)


class SalesHeatmapView(SalesIQView):
    def get(self, request):
        key, field = _dim_or_400(request, 'state')
        if not field:
            return Response({'error': f'Unknown dimension "{key}".'}, status=400)
        try:
            top = max(3, min(25, int(request.query_params.get('top', 12))))
        except (TypeError, ValueError):
            top = 12
        qs, applied, _dump = qs_for_dimension(request, field)
        data = AN.heatmap(qs, field, top=top)
        data.update({'dimension': key, 'filters': applied})
        return Response(data)


class SalesRFMView(SalesIQView):
    """Recency / Frequency / Monetary customer segmentation."""
    # Recency, frequency and money per CUSTOMER. The review sheet has none.
    def get(self, request):
        qs = detail_qs(request)
        applied = {'source': 'invoice_dump'}
        data = AN.rfm(qs)
        data['filters'] = applied
        return Response(data)


class SalesCohortsView(SalesIQView):
    # A cohort is a group of customers, defined by when they first bought.
    # The review sheet has no customers at all.
    def get(self, request):
        qs = detail_qs(request)
        applied = {'source': 'invoice_dump'}
        data = AN.cohorts(qs)
        data['filters'] = applied
        return Response(data)


class SalesNewRepeatView(SalesIQView):
    # New or repeat is a fact about a customer. Invoices only.
    def get(self, request):
        qs = detail_qs(request)
        applied = {'source': 'invoice_dump'}
        data = AN.new_vs_repeat(qs)
        data['filters'] = applied
        return Response(data)


class SalesYoYView(SalesIQView):
    """Year-on-year: uses dimension filters but ignores the date window so
    prior years remain visible when the user narrows the range."""
    def get(self, request):
        base = money_base(request)
        applied = {}
        # Both arguments are source-scoped. The first is the full-history set
        # and does not pass through apply_dim_filters, so it is also the one
        # read that would otherwise still count cancelled invoices.
        data = AN.year_on_year(money_base(request, dims=False), base)
        data['filters'] = applied
        return Response(data)


class SalesPacingView(SalesIQView):
    """How far through the year's plan the business is. Plan and actual both
    come from the review sheet, so this reads the money queryset."""
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.pacing(qs)
        data['filters'] = applied
        return Response(data)


class SalesPriceView(SalesIQView):
    # Realised price is revenue over quantity, and the review sheet carries no quantity.
    def get(self, request):
        qs = detail_qs(request)
        applied = {'source': 'invoice_dump'}
        data = AN.price_realisation(qs)
        data['filters'] = applied
        return Response(data)

