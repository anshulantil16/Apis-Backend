from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from performance.models import EmployeeProfile
from ..models import EOMCycle, EOMNomination
from ..serializers import EOMCycleSerializer, EOMNominationSerializer


class EOMCycleListCreateView(APIView):
    """GET/POST /api/eom/cycles/"""

    def get(self, request):
        return Response(EOMCycleSerializer(EOMCycle.objects.all(), many=True).data)

    def post(self, request):
        s = EOMCycleSerializer(data=request.data)
        if s.is_valid():
            s.save()
            return Response(s.data, status=201)
        return Response(s.errors, status=400)


class EOMCycleUpdateView(APIView):
    """PATCH /api/eom/cycles/<cycle_id>/"""

    def patch(self, request, cycle_id):
        try:
            cycle = EOMCycle.objects.get(id=cycle_id)
        except EOMCycle.DoesNotExist:
            return Response({'error': 'Cycle not found.'}, status=404)
        s = EOMCycleSerializer(cycle, data=request.data, partial=True)
        if s.is_valid():
            s.save()
            return Response(s.data)
        return Response(s.errors, status=400)


class EOMAllNominationsView(APIView):
    """GET /api/eom/all-nominations/?cycle_id=<id>"""

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        qs = EOMNomination.objects.select_related('employee', 'cycle')
        if cycle_id:
            qs = qs.filter(cycle_id=cycle_id)
        return Response(EOMNominationSerializer(qs, many=True).data)


class EOMOverviewView(APIView):
    """GET /api/eom/org/overview/?cycle_id=<id>"""

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        if not cycle_id:
            return Response({'error': 'cycle_id required.'}, status=400)

        total_employees = EmployeeProfile.objects.filter(is_active=True).count()
        noms = EOMNomination.objects.filter(cycle_id=cycle_id)

        submitted    = noms.filter(status__in=['submitted', 'manager_approved', 'hod_approved', 'hr_finalized']).count()
        mgr_approved = noms.filter(status__in=['manager_approved', 'hod_approved', 'hr_finalized']).count()
        hod_approved = noms.filter(status__in=['hod_approved', 'hr_finalized']).count()
        finalized    = noms.filter(status='hr_finalized').count()
        winners      = noms.filter(is_winner=True).count()
        pending      = max(0, total_employees - submitted)

        return Response({
            'total_employees': total_employees,
            'submitted':       submitted,
            'mgr_approved':    mgr_approved,
            'hod_approved':    hod_approved,
            'hr_finalized':    finalized,
            'winners':         winners,
            'pending':         pending,
        })


class EOMHRFinalizeView(APIView):
    """PATCH /api/eom/nominations/<nom_id>/hr-finalize/"""

    def patch(self, request, nom_id):
        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        nom.status          = 'hr_finalized'
        nom.hr_remarks      = request.data.get('hr_remarks', nom.hr_remarks)
        nom.is_winner       = request.data.get('is_winner', nom.is_winner)
        nom.hr_finalized_at = timezone.now()
        nom.save()
        return Response(EOMNominationSerializer(nom).data)
