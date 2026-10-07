"""Upload, list and remove documents in the Policies & Guidelines register.

Files are served back from MEDIA_URL on our own origin, so what may be stored
is allowlisted by extension AND checked against the file's opening bytes: a
browser's content_type is whatever the browser claimed, and an .html or .svg
renamed to .pdf is a script on our origin.
"""
import os
from datetime import date

from django.db.models import Q
from rest_framework import status as http
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from accounts.auth import PortalScopedAPIView, optional_user, require_superadmin, require_user
from accounts.moderation import ModerationStatus, log_activity

from .models import BuiltInRemoval, PolicyDocument

MAX_BYTES = 25 * 1024 * 1024        # 25 MB — a scanned policy, not an archive

PDF_MAGIC  = b'%PDF-'
OOXML_MAGIC = b'PK\x03\x04'                          # docx / xlsx / pptx are zips
OLE_MAGIC  = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'      # legacy doc / xls / ppt
MAGIC_FOR_EXT = {
    '.pdf': PDF_MAGIC,
    '.docx': OOXML_MAGIC, '.xlsx': OOXML_MAGIC, '.pptx': OOXML_MAGIC,
    '.doc': OLE_MAGIC, '.xls': OLE_MAGIC, '.ppt': OLE_MAGIC,
}
CATEGORIES = {c[0] for c in PolicyDocument.CATEGORY_CHOICES}


def _serialize(d, request=None, viewer=None):
    # Root-relative on purpose — see roompulse/attachments.py url_for. Behind
    # the QA proxy build_absolute_uri answered with 127.0.0.1:8001, which the
    # browser read as the viewer's own machine.
    url = d.file.url if d.file else ''
    return {
        'id': d.id, 'title': d.title, 'category': d.category,
        'department': d.department, 'version': d.version, 'pages': d.pages,
        'approvedBy': d.doc_approved_by, 'reviewedBy': d.doc_reviewed_by,
        'approvalDate': d.approval_date.isoformat() if d.approval_date else None,
        'file': url, 'fileName': d.original_name, 'sizeBytes': d.size_bytes,
        'moderationStatus': d.moderation_status,
        'reviewNote': d.review_note or '',
        'submittedBy': d.submitted_by_name or '',
        'submittedAt': d.created_at.isoformat() if d.created_at else None,
        'isMine': bool(viewer and d.submitted_by_id == viewer.id),
        'canDelete': d.can_delete(viewer),
    }


def _extension(name):
    return os.path.splitext(name or '')[1].lower()


def _validate(f):
    """Return an error string, or None if the file is acceptable."""
    if f is None:
        return 'Choose a file to upload.'
    if f.size > MAX_BYTES:
        return f'That file is {f.size / 1024 / 1024:.1f} MB. Please keep it under 25 MB.'
    magic = MAGIC_FOR_EXT.get(_extension(f.name))
    if magic is None:
        return 'Documents must be a PDF, Word, Excel or PowerPoint file.'
    try:
        f.seek(0)
        head = f.read(len(magic))
    finally:
        f.seek(0)
    if head != magic:
        return "That file's contents don't match its extension."
    return None


def _count_pdf_pages(f):
    """Pages in an uploaded PDF, or None if it can't be read. A damaged or
    encrypted PDF is still a document worth filing; it just has no count."""
    if _extension(f.name) != '.pdf':
        return None
    try:
        from pypdf import PdfReader
        f.seek(0)
        return len(PdfReader(f).pages)
    except Exception:
        return None
    finally:
        f.seek(0)


def _parse_date(raw):
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


class PolicyDocumentListView(PortalScopedAPIView):
    """GET — the register. POST — file a new document."""

    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        viewer = optional_user(request)
        if viewer and viewer.is_superadmin:
            rows = PolicyDocument.objects.all()
        elif viewer:
            rows = PolicyDocument.objects.filter(
                Q(moderation_status=ModerationStatus.APPROVED) | Q(submitted_by=viewer))
        else:
            rows = PolicyDocument.published()
        return Response([_serialize(d, request, viewer) for d in rows])

    def post(self, request):
        user, err = require_user(request)
        if err:
            return err

        title = (request.data.get('title') or '').strip()
        if not title:
            return Response({'error': 'Give the document a name.'},
                            status=http.HTTP_400_BAD_REQUEST)

        category = request.data.get('category')
        if category not in CATEGORIES:
            return Response({'error': 'Choose what kind of document this is.'},
                            status=http.HTTP_400_BAD_REQUEST)

        # Every field is required: a register entry without its department,
        # sign-off or date is a document nobody can vouch for.
        for field, label in (('department', 'department'), ('approvedBy', 'who approved it'),
                             ('reviewedBy', 'who reviewed it')):
            if not (request.data.get(field) or '').strip():
                return Response({'error': f'Fill in {label}.'},
                                status=http.HTTP_400_BAD_REQUEST)
        if _parse_date(request.data.get('approvalDate')) is None:
            return Response({'error': 'Fill in the approval date.'},
                            status=http.HTTP_400_BAD_REQUEST)

        f = request.FILES.get('file')
        problem = _validate(f)
        if problem:
            return Response({'error': problem}, status=http.HTTP_400_BAD_REQUEST)

        try:
            version = max(1, min(int(request.data.get('version') or 1), 999))
        except (TypeError, ValueError):
            version = 1

        d = PolicyDocument(
            title=title[:200], category=category, version=version,
            department=(request.data.get('department') or '').strip()[:60],
            doc_approved_by=(request.data.get('approvedBy') or '').strip()[:200],
            doc_reviewed_by=(request.data.get('reviewedBy') or '').strip()[:200],
            approval_date=_parse_date(request.data.get('approvalDate')),
            pages=_count_pdf_pages(f),
            file=f, original_name=(f.name or '')[:255], size_bytes=f.size,
        )
        d.attribute_to(user)
        if user.is_superadmin:
            d.set_review(user, ModerationStatus.APPROVED, 'Uploaded by an administrator.')
        d.save()

        log_activity(user, 'created', d, summary=f'{category}: {title}',
                     detail={'size_kb': round(f.size / 1024),
                             'auto_approved': user.is_superadmin},
                     request=request)

        return Response({
            **_serialize(d, request, user),
            'message': (f'{title} added to {category}.' if d.is_published else
                        'Uploaded and sent for approval. It will be listed for '
                        'everyone once an administrator approves it.'),
        }, status=http.HTTP_201_CREATED)


class PolicyDocumentDetailView(PortalScopedAPIView):
    """DELETE — a super admin only. See PolicyDocument.can_delete."""

    def delete(self, request, pk):
        user, err = require_user(request)
        if err:
            return err
        d = PolicyDocument.objects.filter(pk=pk).first()
        if not d:
            return Response({'error': 'Document not found.'}, status=http.HTTP_404_NOT_FOUND)
        if not d.can_delete(user):
            return Response({'error': 'Only an administrator can remove a '
                                      'document from Policies & Guidelines. '
                                      'Ask one if this was added by mistake.'},
                            status=http.HTTP_403_FORBIDDEN)

        title, did, category, by = d.title, d.id, d.category, d.submitted_by_name
        d.delete()          # takes the file with it — see PolicyDocument.delete
        log_activity(user, 'deleted', object_type='policydocument', object_id=did,
                     summary=f'{category}: {title}',
                     detail={'uploaded_by': by}, request=request)
        return Response({'message': f'{title} removed.'})


class BuiltInRemovalView(PortalScopedAPIView):
    """GET — which built-in PDFs are hidden, and whether the caller may hide
    more. POST {file, title} — a superadmin hides one.

    Superadmin only: nobody uploaded these, so there is no uploader to take
    one back, and they are the company's standing policies.
    """

    def get(self, request):
        viewer = optional_user(request)
        return Response({
            'removed': list(BuiltInRemoval.objects.values_list('file', flat=True)),
            'canRemove': bool(viewer and viewer.is_superadmin),
        })

    def post(self, request):
        user, err = require_superadmin(request)
        if err:
            return err
        name = str(request.data.get('file') or '').strip()
        # A bare filename under public/Policies/, never a path.
        if not name or len(name) > 255 or '/' in name or '\\' in name:
            return Response({'error': 'Which document?'}, status=http.HTTP_400_BAD_REQUEST)
        title = str(request.data.get('title') or '').strip()[:200]

        row, created = BuiltInRemoval.objects.get_or_create(
            file=name, defaults={'title': title, 'removed_by': user,
                                 'removed_by_name': user.name or ''})
        if created:
            log_activity(user, 'deleted', object_type='builtinpolicy', object_id=row.id,
                         summary=f'Built-in policy: {title or name}',
                         detail={'file': name}, request=request)
        return Response({'message': f'{title or name} removed.'})
