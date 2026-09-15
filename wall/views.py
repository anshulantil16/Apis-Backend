"""Upload, review and serve the APIS Wall.

Uploads are the one place here that accepts a file rather than a JSON body,
so this module does the validation the rest of the app can take for granted:
size, declared type, and that the bytes really are a decodable image.
"""
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework import status as http

from accounts.auth import PortalScopedAPIView, optional_user, require_superadmin, require_user
from accounts.moderation import ModerationStatus, log_activity

from .models import WallPhoto

MAX_BYTES = 8 * 1024 * 1024          # 8 MB — a phone photo, not a raw file
ALLOWED_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}
CATEGORIES = {c[0] for c in WallPhoto.CATEGORY_CHOICES}


def _serialize(p, request=None, viewer=None):
    url = p.image.url if p.image else ''
    if request is not None and url:
        url = request.build_absolute_uri(url)
    return {
        'id': p.id, 'title': p.title, 'category': p.category, 'caption': p.caption,
        'src': url,
        'moderationStatus': p.moderation_status,
        'submittedBy': p.submitted_by_name or '',
        'submittedByEmail': p.submitted_by_email or '',
        'submittedAt': p.created_at.isoformat() if p.created_at else None,
        'reviewNote': p.review_note or '',
        'isMine': bool(viewer and p.submitted_by_id == viewer.id),
    }


def _validate(f):
    """Return an error string, or None if the file is an acceptable image."""
    if f is None:
        return 'Choose a photo to upload.'
    if f.size > MAX_BYTES:
        return f'That photo is {f.size / 1024 / 1024:.1f} MB. Please keep it under 8 MB.'
    if (getattr(f, 'content_type', '') or '').lower() not in ALLOWED_TYPES:
        return 'Photos must be a JPG, PNG, WebP or GIF.'

    # content_type is whatever the browser claimed. Decode the header to check
    # the bytes actually are an image before storing and later serving it.
    try:
        from PIL import Image
        f.seek(0)
        Image.open(f).verify()
    except Exception:
        return "That file doesn't look like a valid image."
    finally:
        try:
            f.seek(0)
        except Exception:
            pass
    return None


class WallPhotoListView(PortalScopedAPIView):
    """GET — the wall. POST — upload a photo for approval."""

    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        viewer = optional_user(request)
        if viewer and viewer.is_superadmin:
            rows = WallPhoto.objects.all()
        elif viewer:
            from django.db.models import Q
            rows = WallPhoto.objects.filter(
                Q(moderation_status=ModerationStatus.APPROVED) | Q(submitted_by=viewer))
        else:
            rows = WallPhoto.published()
        return Response([_serialize(p, request, viewer)
                         for p in rows.select_related('submitted_by')])

    def post(self, request):
        user, err = require_user(request)
        if err:
            return err

        title = (request.data.get('title') or '').strip()
        if not title:
            return Response({'error': 'Give the photo a title.'},
                            status=http.HTTP_400_BAD_REQUEST)

        f = request.FILES.get('image')
        problem = _validate(f)
        if problem:
            return Response({'error': problem}, status=http.HTTP_400_BAD_REQUEST)

        category = request.data.get('category') or 'Other'
        if category not in CATEGORIES:
            category = 'Other'

        p = WallPhoto(
            title=title[:200], category=category,
            caption=(request.data.get('caption') or '').strip()[:300],
            image=f, original_name=(getattr(f, 'name', '') or '')[:255],
            size_bytes=f.size,
        )
        p.attribute_to(user)
        if user.is_superadmin:
            p.set_review(user, ModerationStatus.APPROVED, 'Uploaded by an administrator.')
        p.save()

        log_activity(user, 'created', p, summary=f'Wall photo: {title}',
                     detail={'category': category, 'size_kb': round(f.size / 1024),
                             'auto_approved': user.is_superadmin},
                     request=request)

        return Response({
            **_serialize(p, request, user),
            'message': ('Photo published to the wall.' if p.is_published else
                        'Uploaded and sent for approval. It will appear on the wall '
                        'once an administrator approves it.'),
        }, status=http.HTTP_201_CREATED)


class WallPhotoDetailView(PortalScopedAPIView):
    """Superadmin edit (title/category/caption) and delete."""

    def patch(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        p = WallPhoto.objects.filter(pk=pk).first()
        if not p:
            return Response({'error': 'Photo not found.'}, status=http.HTTP_404_NOT_FOUND)

        changed = {}
        for field, cap in (('title', 200), ('caption', 300)):
            if field in request.data:
                new = str(request.data.get(field) or '').strip()[:cap]
                if new != getattr(p, field):
                    changed[field] = {'from': getattr(p, field), 'to': new}
                    setattr(p, field, new)

        cat = request.data.get('category')
        if cat in CATEGORIES and cat != p.category:
            changed['category'] = {'from': p.category, 'to': cat}
            p.category = cat

        if changed:
            p.save()
            log_activity(user, 'edited', p, summary=f'Wall photo: {p.title}',
                         detail={'changed': changed}, request=request)
        return Response(_serialize(p, request, user))

    def delete(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        p = WallPhoto.objects.filter(pk=pk).first()
        if not p:
            return Response({'error': 'Photo not found.'}, status=http.HTTP_404_NOT_FOUND)

        title, pid, by = p.title, p.id, p.submitted_by_name
        p.delete()          # takes the file with it — see WallPhoto.delete
        log_activity(user, 'deleted', object_type='wallphoto', object_id=pid,
                     summary=f'Wall photo: {title}',
                     detail={'uploaded_by': by}, request=request)
        return Response({'message': 'Photo removed from the wall.'})
