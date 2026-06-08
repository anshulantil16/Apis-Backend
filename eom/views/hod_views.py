from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import EOMEmployee, EOMNomination
from ..serializers import EOMNominationSerializer

HOD_SCORE_FIELDS = [
    'hod_dim1_score', 'hod_dim1_comments',
    'hod_dim2_score', 'hod_dim2_comments',
    'hod_dim3_score', 'hod_dim3_comments',
    'hod_dim4_score', 'hod_dim4_comments',
    'hod_sustainability_desc', 'hod_sustainability_bonus',
    'hod_sustainability_just', 'hod_recommendation',
    'hod_panel_name', 'hod_remarks',
]


class EOMHODTeamView(APIView):
    """GET /api/eom/hod/<hod_id>/team/?cycle_id=<id>"""

    def get(self, request, hod_id):
        cycle_id = request.query_params.get('cycle_id')
        team     = EOMEmployee.objects.filter(hod_id=hod_id, is_active=True)

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

        if nom.status not in ('submitted', 'hod_rejected'):
            return Response({'error': f'Cannot review from status: {nom.status}'}, status=400)

        for field in HOD_SCORE_FIELDS:
            if field in request.data:
                setattr(nom, field, request.data[field])

        action = request.data.get('action')
        if action == 'approved':
            nom.status = 'hod_approved'
        elif action == 'rejected':
            nom.status = 'hod_rejected'

        nom.hod_reviewed_at = timezone.now()
        nom.save()
        return Response(EOMNominationSerializer(nom).data)
