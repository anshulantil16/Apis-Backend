"""CTC Calculator — template download, upload, and the uploaded candidates.

    GET    /api/ctc/template/        the blank Excel template
    GET    /api/ctc/uploads/         uploads the caller may see, with their rows
    POST   /api/ctc/uploads/         upload a filled-in template (multipart, "file")
    DELETE /api/ctc/uploads/<id>/    remove an upload and its rows
    POST   /api/ctc/reset/           erase every upload the caller can see
                                     (body: {"confirm": "RESET"})
    GET    /api/ctc/candidates/<id>/breakdown/   the worked-out break-up
    GET    /api/ctc/candidates/<id>/pdf/         the same, as the Annexure A PDF
    GET    /api/ctc/settings/        the editable values; PUT (super admin) to change

Everything needs a signed-in session. A person sees the uploads they made;
a super admin sees everyone's. Candidate names and the grades they are
offered are not for the whole company.
"""
import os
import re
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from accounts.auth import PortalScopedAPIView, require_superadmin, require_user
from accounts.moderation import log_activity

from .calculator import calculate
from .columns import COLUMNS
from .ingest import SheetError, parse
from .models import CtcCandidate, CtcSettings, CtcUpload
from .pdf import build_pdf
from .template import build_template

MAX_BYTES = 5 * 1024 * 1024          # a few thousand rows is well under this
XLSX_MAGIC = b'PK\x03\x04'           # .xlsx is a zip
FIELDS = [field for _h, field, *_ in COLUMNS] + ['gross_earnings']


def _candidate(c):
    return {'id': c.id, 'rowNumber': c.row_number,
            'employeeCode': c.employee_code,
            'dateOfJoining': c.date_of_joining.isoformat() if c.date_of_joining else None,
            'candidateName': c.candidate_name, 'category': c.category, 'department': c.department,
            'designation': c.designation, 'grade': c.grade, 'cadre': c.cadre,
            'workLocation': c.work_location, 'numberOfChildren': c.number_of_children,
            'grossEarnings': c.gross_earnings or {},
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


def _visible_candidate(user, pk):
    """The candidate, if this user may see it (their own upload, or any for
    a super admin) - else None, so other people's rows read as not found."""
    c = CtcCandidate.objects.select_related('upload').filter(pk=pk).first()
    return c if c and c.upload.can_manage(user) else None


def _breakdown(c):
    return calculate(ctc_annual=c.ctc, cadre=c.cadre, grade=c.grade, category=c.category,
                     department=c.department, designation=c.designation,
                     work_location=c.work_location, children=c.number_of_children,
                     options=c.gross_earnings,
                     settings=CtcSettings.current())


class CtcBreakdownView(PortalScopedAPIView):
    def get(self, request, pk):
        user, err = require_user(request)
        if err:
            return err
        c = _visible_candidate(user, pk)
        if not c:
            return Response({'error': 'Candidate not found.'}, status=http.HTTP_404_NOT_FOUND)
        return Response({'candidate': _candidate(c), 'breakdown': _breakdown(c)})


class CtcPdfView(PortalScopedAPIView):
    def get(self, request, pk):
        user, err = require_user(request)
        if err:
            return err
        c = _visible_candidate(user, pk)
        if not c:
            return Response({'error': 'Candidate not found.'}, status=http.HTTP_404_NOT_FOUND)
        b = _breakdown(c)
        if b.get('error'):
            return Response({'error': b['error']}, status=http.HTTP_400_BAD_REQUEST)
        safe = re.sub(r'[^A-Za-z0-9]+', '_', c.candidate_name).strip('_') or 'candidate'
        r = HttpResponse(build_pdf(c, b), content_type='application/pdf')
        r['Content-Disposition'] = f'attachment; filename="CTC_Breakup_{safe}.pdf"'
        return r


SETTING_FIELDS = {
    # api key: (model field, label)
    'ltaD': ('lta_d', 'LTA per month - cadre D'),
    'ltaC': ('lta_c', 'LTA per month - cadre C'),
    'ltaM': ('lta_m', 'LTA per month - cadre M'),
    'ltaO': ('lta_o', 'LTA per month - cadre O'),
    'ltaW': ('lta_w', 'LTA per month - cadre W'),
    'gratuityPercent': ('gratuity_percent', 'Gratuity, % of Basic'),
    'dFuel': ('d_fuel', 'Fuel & vehicle per month - cadre D'),
    'dDriver': ('d_driver', 'Driver per month - cadre D'),
    'dHealth': ('d_health', 'Health & wellness per month - cadre D'),
    'pfWageCeiling': ('pf_wage_ceiling', 'PF wage ceiling per month'),
    'esiBonusGrossLimit': ('esi_bonus_gross_limit', 'ESI & Bonus apply up to gross per month'),
}


def _settings(s, viewer):
    return {
        'values': {k: (str(getattr(s, f)) if f == 'gratuity_percent' else getattr(s, f))
                   for k, (f, _l) in SETTING_FIELDS.items()},
        'labels': {k: label for k, (_f, label) in SETTING_FIELDS.items()},
        'updatedBy': s.updated_by_name,
        'updatedAt': s.updated_at.isoformat() if s.updated_at else None,
        'canEdit': bool(viewer and viewer.is_superadmin),
    }


class CtcSettingsView(PortalScopedAPIView):
    def get(self, request):
        user, err = require_user(request)
        if err:
            return err
        return Response(_settings(CtcSettings.current(), user))

    def put(self, request):
        user, err = require_superadmin(request)
        if err:
            return err
        s = CtcSettings.current()
        for key, (field, label) in SETTING_FIELDS.items():
            if key not in request.data:
                continue
            raw = str(request.data.get(key)).strip() or '0'
            try:
                value = Decimal(raw)
            except InvalidOperation:
                return Response({'error': f'{label}: "{raw}" is not a number.'},
                                status=http.HTTP_400_BAD_REQUEST)
            limit = 100 if field == 'gratuity_percent' else 10_000_000
            if not value.is_finite() or value < 0 or value > limit:
                return Response({'error': f'{label} must be between 0 and {limit:,}.'},
                                status=http.HTTP_400_BAD_REQUEST)
            setattr(s, field, value.quantize(Decimal('0.01')) if field == 'gratuity_percent' else int(value))
        s.updated_by_name = user.name or user.email or ''
        s.save()
        log_activity(user, 'updated', s, summary='CTC calculator settings', request=request)
        return Response({**_settings(s, user), 'message': 'Settings saved.'})
