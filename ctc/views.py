"""CTC Calculator — template download, upload, and the uploaded candidates.

    GET    /api/ctc/template/        the blank Excel template
    GET    /api/ctc/uploads/         uploads the caller may see, with their rows
    POST   /api/ctc/uploads/         upload a filled-in template (multipart, "file")
    DELETE /api/ctc/uploads/<id>/    remove an upload and its rows
    POST   /api/ctc/reset/           erase every upload the caller can see
                                     (body: {"confirm": "RESET"})

Everything needs a signed-in session. A person sees the uploads they made;
a super admin sees everyone's. Candidate names and the grades they are
offered are not for the whole company.
"""
import os

from django.db import transaction
from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from accounts.auth import PortalScopedAPIView, require_user
from accounts.moderation import log_activity

from .columns import COLUMNS
from .ingest import SheetError, parse
from .models import CtcCandidate, CtcUpload
from .template import build_template

MAX_BYTES = 5 * 1024 * 1024          # a few thousand rows is well under this
XLSX_MAGIC = b'PK\x03\x04'           # .xlsx is a zip
FIELDS = [field for _h, field, *_ in COLUMNS]


def _candidate(c):
    return {'id': c.id, 'rowNumber': c.row_number,
            'candidateName': c.candidate_name, 'department': c.department,
            'designation': c.designation, 'grade': c.grade, 'cadre': c.cadre,
            'workLocation': c.work_location,
            'ctc': str(c.ctc) if c.ctc is not None else None}


def _upload(u, viewer):
    return {
        'id': u.id, 'fileName': u.original_name, 'rowCount': u.row_count,
        'uploadedBy': u.uploaded_by_name,
        'uploadedAt': u.created_at.isoformat() if u.created_at else None,
        'canDelete': u.can_manage(viewer),
        'candidates': [_candidate(c) for c in u.candidates.all()],
    }


class CtcTemplateView(PortalScopedAPIView):
    def get(self, request):
        _user, err = require_user(request)
        if err:
            return err
        r = HttpResponse(
            build_template(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        r['Content-Disposition'] = 'attachment; filename="CTC_Calculator_Template.xlsx"'
        return r


class CtcUploadListView(PortalScopedAPIView):
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        user, err = require_user(request)
        if err:
            return err
        rows = CtcUpload.objects.prefetch_related('candidates')
        if not user.is_superadmin:
            rows = rows.filter(uploaded_by=user)
        return Response([_upload(u, user) for u in rows])

    def post(self, request):
        user, err = require_user(request)
        if err:
            return err

        f = request.FILES.get('file')
        if f is None:
            return Response({'error': 'Choose the filled-in template to upload.'},
                            status=http.HTTP_400_BAD_REQUEST)
        if os.path.splitext(f.name or '')[1].lower() != '.xlsx':
            return Response({'error': 'Please upload the template as an .xlsx file.'},
                            status=http.HTTP_400_BAD_REQUEST)
        if f.size > MAX_BYTES:
            return Response({'error': 'That file is over 5 MB. Please split it into smaller uploads.'},
                            status=http.HTTP_400_BAD_REQUEST)
        f.seek(0)
        if f.read(len(XLSX_MAGIC)) != XLSX_MAGIC:
            return Response({'error': "That file isn't a valid .xlsx workbook."},
                            status=http.HTTP_400_BAD_REQUEST)
        f.seek(0)

        try:
            rows, problems = parse(f)
        except SheetError as e:
            return Response({'error': str(e)}, status=http.HTTP_400_BAD_REQUEST)
        if problems:
            return Response({'error': 'Nothing was uploaded — please fix these rows and try again.',
                             'problems': problems[:50]},
                            status=http.HTTP_400_BAD_REQUEST)
        if not rows:
            return Response({'error': 'The template has no candidates in it yet.'},
                            status=http.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            u = CtcUpload.objects.create(
                original_name=(f.name or '')[:255], uploaded_by=user,
                uploaded_by_name=user.name or user.email or '', row_count=len(rows))
            CtcCandidate.objects.bulk_create([
                CtcCandidate(upload=u, row_number=r['row_number'],
                             **{field: r[field] for field in FIELDS})
                for r in rows
            ])

        log_activity(user, 'created', u, summary=f'CTC upload: {len(rows)} candidates',
                     detail={'file': u.original_name}, request=request)
        return Response({**_upload(u, user),
                         'message': f'{len(rows)} candidate{"s" if len(rows) != 1 else ""} uploaded.'},
                        status=http.HTTP_201_CREATED)


class CtcUploadDetailView(PortalScopedAPIView):
    def delete(self, request, pk):
        user, err = require_user(request)
        if err:
            return err
        u = CtcUpload.objects.filter(pk=pk).first()
        if not u:
            return Response({'error': 'Upload not found.'}, status=http.HTTP_404_NOT_FOUND)
        if not u.can_manage(user):
            return Response({'error': 'Only the person who uploaded this, or an administrator, '
                                      'can remove it.'}, status=http.HTTP_403_FORBIDDEN)
        name, count, uid = u.original_name, u.row_count, u.id
        u.delete()
        log_activity(user, 'deleted', object_type='ctcupload', object_id=uid,
                     summary=f'CTC upload: {count} candidates', detail={'file': name},
                     request=request)
        return Response({'message': f'{name or "Upload"} removed.'})


class CtcResetView(PortalScopedAPIView):
    """Danger zone: erase the candidates so a fresh set can be uploaded.

    Removes exactly what the caller sees on the page — their own uploads, or
    everyone's for a super admin — never someone else's behind their back.
    Needs the word RESET in the body as well as the page's own confirmation,
    so a stray request can't wipe anything.
    """

    def post(self, request):
        user, err = require_user(request)
        if err:
            return err
        if str(request.data.get('confirm') or '').strip().upper() != 'RESET':
            return Response({'error': 'Confirmation required: type RESET.'},
                            status=http.HTTP_400_BAD_REQUEST)

        uploads = CtcUpload.objects.all()
        if not user.is_superadmin:
            uploads = uploads.filter(uploaded_by=user)
        n_uploads = uploads.count()
        n_candidates = CtcCandidate.objects.filter(upload__in=uploads).count()
        uploads.delete()                 # candidates go with them (CASCADE)

        log_activity(user, 'deleted', object_type='ctcupload',
                     summary=f'CTC reset: {n_candidates} candidates in {n_uploads} uploads',
                     detail={'everyone': user.is_superadmin}, request=request)
        return Response({
            'message': (f'Reset done — removed {n_candidates} candidate'
                        f'{"s" if n_candidates != 1 else ""} from {n_uploads} upload'
                        f'{"s" if n_uploads != 1 else ""}.'),
            'deleted': {'uploads': n_uploads, 'candidates': n_candidates},
        })
