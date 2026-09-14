from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Vacancy

REQUIRED_FIELDS = ['title', 'function', 'department', 'location', 'state']


def _serialize(v):
    """camelCase-ish shape the frontend's VacancyListing type expects —
    reportingManager rather than reporting_manager, everything else as-is."""
    return {
        'id': v.id, 'function': v.function, 'department': v.department, 'title': v.title,
        'grade': v.grade, 'location': v.location, 'state': v.state,
        'reportingManager': v.reporting_manager, 'type': v.type,
        'experience': v.experience, 'education': v.education, 'status': v.status,
    }


class VacancyListView(APIView):
    """GET — every vacancy, newest first. POST — add a new one (from the Add
    Vacancy form); always starts life as 'Active'. No portal session
    required, same "trust the client" posture as referrals/tada/sales.
    """

    def get(self, request):
        return Response([_serialize(v) for v in Vacancy.objects.all()])

    def post(self, request):
        data = request.data
        missing = [f for f in REQUIRED_FIELDS if not str(data.get(f, '')).strip()]
        if missing:
            return Response({'error': f'Missing required field(s): {", ".join(missing)}'},
                             status=status.HTTP_400_BAD_REQUEST)

        v = Vacancy.objects.create(
            title=data.get('title', ''), function=data.get('function', ''), department=data.get('department', ''),
            grade=data.get('grade', ''), location=data.get('location', ''), state=data.get('state', ''),
            reporting_manager=data.get('reportingManager', ''),
            type=data.get('type') if data.get('type') in ('New', 'Replacement') else 'New',
            experience=data.get('experience', ''), education=data.get('education', ''),
        )
        return Response(_serialize(v), status=status.HTTP_201_CREATED)


class VacancyStatusView(APIView):
    """PATCH — flip a vacancy between Active/Closed (the Close/Reopen button
    on each card). Only the status changes here; everything else about a
    vacancy is set once, at creation."""

    def patch(self, request, pk):
        try:
            v = Vacancy.objects.get(pk=pk)
        except Vacancy.DoesNotExist:
            return Response({'error': 'Vacancy not found.'}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get('status')
        if new_status not in ('Active', 'Closed'):
            return Response({'error': "status must be 'Active' or 'Closed'."}, status=status.HTTP_400_BAD_REQUEST)

        v.status = new_status
        v.save(update_fields=['status', 'updated_at'])
        return Response(_serialize(v))
