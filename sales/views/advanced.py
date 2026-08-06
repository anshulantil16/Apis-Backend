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
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from ..models import SalesUpload, SalesRecord
from ..ingest import (map_headers, parse_date, parse_num, build_template,
                      TEXT_FIELDS, NUM_FIELDS, TEXT_MAX)

from .. import analytics as AN
from .filters import (DIMENSIONS, apply_filters, apply_dim_filters, _period_bounds)



def _dim_or_400(request, default='state'):
    key = (request.query_params.get('dim') or default).strip().lower()
    field = DIMENSIONS.get(key)
    return key, field


class SalesParetoView(APIView):
    """80/20 concentration with ABC classification."""
    def get(self, request):
        key, field = _dim_or_400(request, 'customer')
        if not field:
            return Response({'error': f'Unknown dimension "{key}".',
                             'available': sorted(DIMENSIONS.keys())}, status=400)
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.pareto(qs, field)
        data.update({'dimension': key, 'filters': applied})
        return Response(data)


class SalesMatrixView(APIView):
    """Revenue-vs-growth quadrant (star / cash cow / rising / watch)."""
    def get(self, request):
        key, field = _dim_or_400(request, 'product')
        if not field:
            return Response({'error': f'Unknown dimension "{key}".'}, status=400)
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        lo, hi = _period_bounds(qs)
        base, _ = apply_dim_filters(SalesRecord.objects.all(), request)
        data = AN.growth_matrix(base, field, lo, hi)
        data.update({'dimension': key, 'filters': applied})
        return Response(data)


class SalesMoversView(APIView):
    """Biggest absolute gainers and losers vs the prior equal window."""
    def get(self, request):
        key, field = _dim_or_400(request, 'state')
        if not field:
            return Response({'error': f'Unknown dimension "{key}".'}, status=400)
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        lo, hi = _period_bounds(qs)
        base, _ = apply_dim_filters(SalesRecord.objects.all(), request)
        data = AN.movers(base, field, lo, hi)
        data.update({'dimension': key, 'filters': applied})
        return Response(data)


class SalesAnomaliesView(APIView):
    def get(self, request):
        try:
            z = max(1.0, min(4.0, float(request.query_params.get('z', 2.0))))
        except (TypeError, ValueError):
            z = 2.0
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.anomalies(qs, z=z)
        data['filters'] = applied
        return Response(data)


class SalesSeasonalityView(APIView):
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.seasonality(qs)
        data['filters'] = applied
        return Response(data)


class SalesHeatmapView(APIView):
    def get(self, request):
        key, field = _dim_or_400(request, 'state')
        if not field:
            return Response({'error': f'Unknown dimension "{key}".'}, status=400)
        try:
            top = max(3, min(25, int(request.query_params.get('top', 12))))
        except (TypeError, ValueError):
            top = 12
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.heatmap(qs, field, top=top)
        data.update({'dimension': key, 'filters': applied})
        return Response(data)


class SalesRFMView(APIView):
    """Recency / Frequency / Monetary customer segmentation."""
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.rfm(qs)
        data['filters'] = applied
        return Response(data)


class SalesCohortsView(APIView):
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.cohorts(qs)
        data['filters'] = applied
        return Response(data)


class SalesNewRepeatView(APIView):
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.new_vs_repeat(qs)
        data['filters'] = applied
        return Response(data)


class SalesYoYView(APIView):
    """Year-on-year: uses dimension filters but ignores the date window so
    prior years remain visible when the user narrows the range."""
    def get(self, request):
        base, applied = apply_dim_filters(SalesRecord.objects.all(), request)
        data = AN.year_on_year(SalesRecord.objects.all(), base)
        data['filters'] = applied
        return Response(data)


class SalesPacingView(APIView):
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.pacing(qs)
        data['filters'] = applied
        return Response(data)


class SalesPriceView(APIView):
    def get(self, request):
        qs, applied = apply_filters(SalesRecord.objects.all(), request)
        data = AN.price_realisation(qs)
        data['filters'] = applied
        return Response(data)

