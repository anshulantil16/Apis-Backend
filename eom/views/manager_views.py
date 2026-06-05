from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import EOMEmployee, EOMNomination
from ..serializers import EOMNominationSerializer

MANAGER_SCORE_FIELDS = [
    'manager_dim1_score', 'manager_dim1_comments',
    'manager_dim2_score', 'manager_dim2_comments',
    'manager_dim3_score', 'manager_dim3_comments',
    'manager_dim4_score', 'manager_dim4_comments',
    'manager_sustainability_desc', 'manager_sustainability_bonus',
    'manager_sustainability_just', 'manager_recommendation',
    'manager_panel_name', 'manager_remarks',
]


class EOMManagerTeamView(APIView):
    """GET /api/eom/manager/<manager_id>/team/?cycle_id=<id>"""

    def get(self, request, manager_id):
        cycle_id = request.query_params.get('cycle_id')
        team     = EOMEmployee.objects.filter(reporting_manager_id=manager_id, is_active=True)

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


class EOMManagerReviewView(APIView):
    """PATCH /api/eom/nominations/<nom_id>/manager-review/"""

    def patch(self, request, nom_id):
        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        if nom.status not in ('submitted', 'manager_rejected'):
            return Response({'error': f'Cannot review from status: {nom.status}'}, status=400)

        # Save all scorecard fields
        for field in MANAGER_SCORE_FIELDS:
            if field in request.data:
                setattr(nom, field, request.data[field])

        action = request.data.get('action')
        if action == 'approved':
            nom.status = 'manager_approved'
        elif action == 'rejected':
            nom.status = 'manager_rejected'

        nom.manager_reviewed_at = timezone.now()
        nom.save()
        return Response(EOMNominationSerializer(nom).data)
