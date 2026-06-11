from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser

from ..models import EOMEmployee, EOMCycle, EOMNomination
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
    """GET /api/eom/cycles/active/"""

    def get(self, request):
        return Response(EOMCycleSerializer(EOMCycle.objects.filter(status='open'), many=True).data)


class EOMNominationView(APIView):
    """GET/POST /api/eom/nominations/<employee_id>/<cycle_id>/"""

    def get(self, request, employee_id, cycle_id):
        try:
            nom = EOMNomination.objects.get(employee__employee_id=employee_id, cycle_id=cycle_id)
            return Response(EOMNominationSerializer(nom, context={'request': request}).data)
        except EOMNomination.DoesNotExist:
            return Response({'detail': 'No nomination yet.'}, status=404)

    def post(self, request, employee_id, cycle_id):
        try:
            emp   = EOMEmployee.objects.get(employee_id=employee_id, is_active=True)
            cycle = EOMCycle.objects.get(id=cycle_id)
        except (EOMEmployee.DoesNotExist, EOMCycle.DoesNotExist) as e:
            return Response({'error': str(e)}, status=404)

        if cycle.status != 'open':
            return Response({'error': 'This cycle is closed for nominations.'}, status=403)

        nom, _ = EOMNomination.objects.get_or_create(employee=emp, cycle=cycle)

        if nom.status not in ('draft', 'hod_rejected'):
            return Response({'error': f'Cannot edit a nomination with status: {nom.status}'}, status=400)

        for field in EMPLOYEE_FORM_FIELDS:
            if field in request.data:
                setattr(nom, field, request.data[field])
        nom.save()
        return Response(EOMNominationSerializer(nom, context={'request': request}).data)


class EOMDocumentUploadView(APIView):
    """POST /api/eom/nominations/<nom_id>/upload-document/"""
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request, nom_id):
        import os
        from django.conf import settings

        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        if nom.status not in ('draft', 'hod_rejected'):
            return Response({'error': 'Cannot upload document at this stage.'}, status=400)

        file = request.FILES.get('document')
        if not file:
            return Response({'error': 'No file provided.'}, status=400)

        # Ensure upload directory exists
        upload_dir = os.path.join(settings.MEDIA_ROOT, 'eom_docs')
        os.makedirs(upload_dir, exist_ok=True)

        # Delete old file if exists
        if nom.support_document:
            nom.support_document.delete(save=False)

        nom.support_document      = file
        nom.support_document_name = file.name
        nom.save()
        return Response(EOMNominationSerializer(nom, context={'request': request}).data)

    def delete(self, request, nom_id):
        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        if nom.support_document:
            nom.support_document.delete(save=False)
            nom.support_document      = None
            nom.support_document_name = ''
            nom.save()
        return Response({'message': 'Document removed.'})


class EOMSubmitNominationView(APIView):
    """PATCH /api/eom/nominations/<nom_id>/submit/"""

    def patch(self, request, nom_id):
        try:
            nom = EOMNomination.objects.get(id=nom_id)
        except EOMNomination.DoesNotExist:
            return Response({'error': 'Nomination not found.'}, status=404)

        if nom.status not in ('draft', 'hod_rejected'):
            return Response({'error': f'Cannot submit from status: {nom.status}'}, status=400)

        nom.status       = 'submitted'
        nom.submitted_at = timezone.now()
        nom.save()
        return Response(EOMNominationSerializer(nom, context={'request': request}).data)
