from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from performance.models import EmployeeProfile
from ..models import EOMCycle, EOMNomination
from ..serializers import EOMNominationSerializer, EOMCycleSerializer

EMPLOYEE_FORM_FIELDS = [
    'track', 'part_a_achievement',
    'smart_specific', 'smart_measurable', 'smart_achievable',
    'smart_relevant', 'smart_timebound',
    'evidence_1_description', 'evidence_1_source',
    'evidence_2_description', 'evidence_2_source',
    'declaration_agreed', 'signature_name',
]


class EOMActiveCyclesView(APIView):
    """GET /api/eom/cycles/active/ — cycles currently open for nominations."""

    def get(self, request):
        cycles = EOMCycle.objects.filter(status='open')
        return Response(EOMCycleSerializer(cycles, many=True).data)


class EOMNominationView(APIView):
    """GET/POST /api/eom/nominations/<employee_id>/<cycle_id>/"""

    def get(self, request, employee_id, cycle_id):
        try:
            nom = EOMNomination.objects.get(
                employee__employee_id=employee_id, cycle_id=cycle_id
            )
            return Response(EOMNominationSerializer(nom).data)
        except EOMNomination.DoesNotExist:
            return Response({'detail': 'No nomination yet.'}, status=404)

    def post(self, request, employee_id, cycle_id):
        try:
            emp   = EmployeeProfile.objects.get(employee_id=employee_id)
            cycle = EOMCycle.objects.get(id=cycle_id)
        except (EmployeeProfile.DoesNotExist, EOMCycle.DoesNotExist) as e:
            return Response({'error': str(e)}, status=404)

        if cycle.status != 'open':
            return Response({'error': 'This cycle is closed for nominations.'}, status=403)

        nom, _ = EOMNomination.objects.get_or_create(employee=emp, cycle=cycle)

        if nom.status not in ('draft', 'manager_rejected'):
            return Response({'error': f'Cannot edit a nomination with status: {nom.status}'}, status=400)

        for field in EMPLOYEE_FORM_FIELDS:
            if field in request.data:
                setattr(nom, field, request.data[field])
        nom.save()
        return Response(EOMNominationSerializer(nom).data)


class EOMSubmitNominationView(APIView):
    """PATCH /api/eom/nominations/<nom_id>/submit/"""

    def patch(self, request, nom_id):
        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        if nom.status not in ('draft', 'manager_rejected'):
            return Response({'error': f'Cannot submit from status: {nom.status}'}, status=400)

        nom.status       = 'submitted'
        nom.submitted_at = timezone.now()
        nom.save()
        return Response(EOMNominationSerializer(nom).data)
