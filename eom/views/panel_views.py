from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from ..models import EOMNomination
from ..serializers import EOMNominationSerializer

PANEL_SCORE_FIELDS = [
    'panel_dim2_score', 'panel_dim2_comments',
    'panel_dim3_score', 'panel_dim3_comments',
    'panel_dim4_score', 'panel_dim4_comments',
    'panel_dim5_score', 'panel_dim5_comments',
    'panel_dim6_score', 'panel_dim6_comments',
    'panel_sustainability_desc', 'panel_sustainability_bonus',
    'panel_sustainability_just', 'panel_recommendation',
    'panel_panel_name', 'panel_remarks',
]


class EOMPanelNominationsView(APIView):
    """GET /api/eom/panel/nominations/?cycle_id=<id>
    Returns only HOD-approved nominations (hod_rejected are excluded).
    """

    def get(self, request):
        cycle_id = request.query_params.get('cycle_id')
        if not cycle_id:
            return Response({'error': 'cycle_id required.'}, status=400)

        noms = EOMNomination.objects.filter(
            cycle_id=cycle_id,
            status__in=['hod_approved', 'panel_approved', 'panel_rejected', 'hr_finalized'],
        ).select_related('employee', 'cycle')

        return Response(EOMNominationSerializer(noms, many=True).data)


class EOMPanelReviewView(APIView):
    """PATCH /api/eom/nominations/<nom_id>/panel-review/"""

    def patch(self, request, nom_id):
        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        if nom.status not in ('hod_approved', 'panel_rejected'):
            return Response({'error': f'Cannot review from status: {nom.status}'}, status=400)

        for field in PANEL_SCORE_FIELDS:
            if field in request.data:
                setattr(nom, field, request.data[field])

        action = request.data.get('action')
        if action == 'approved':
            nom.status = 'panel_approved'
        elif action == 'rejected':
            nom.status = 'panel_rejected'

        nom.panel_reviewed_at = timezone.now()
        nom.save()
        return Response(EOMNominationSerializer(nom).data)
