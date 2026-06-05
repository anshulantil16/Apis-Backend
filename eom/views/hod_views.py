from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from performance.models import EmployeeProfile
from ..models import EOMNomination
from ..serializers import EOMNominationSerializer


class EOMHODTeamView(APIView):
    """GET /api/eom/hod/<hod_id>/team/?cycle_id=<id>"""

    def get(self, request, hod_id):
        cycle_id = request.query_params.get('cycle_id')
        team     = EmployeeProfile.objects.filter(hod_id=hod_id, is_active=True)

        result = []
        for emp in team:
            entry = {
                'employee_id': emp.employee_id,
                'name':        emp.name,
                'designation': emp.designation,
                'department':  emp.department,
                'zone':        emp.zone,
                'nomination':  None,
            }
            if cycle_id:
                try:
                    nom = EOMNomination.objects.get(employee=emp, cycle_id=cycle_id)
                    entry['nomination'] = EOMNominationSerializer(nom).data
                except EOMNomination.DoesNotExist:
                    pass
            result.append(entry)
        return Response(result)


class EOMHODReviewView(APIView):
    """PATCH /api/eom/nominations/<nom_id>/hod-review/"""

    def patch(self, request, nom_id):
        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        action = request.data.get('action')
        if action not in ('approved', 'rejected'):
            return Response({'error': 'action must be approved or rejected.'}, status=400)

        nom.status          = 'hod_approved' if action == 'approved' else 'hod_rejected'
        nom.hod_remarks     = request.data.get('hod_remarks', nom.hod_remarks)
        nom.hod_reviewed_at = timezone.now()
        nom.save()
        return Response(EOMNominationSerializer(nom).data)
