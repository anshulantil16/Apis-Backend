"""APIS Tree card edits and additions — see models.TreeProfile.

Kept apart from views.py for the same reason as views_moderation.py: a
distinct, self-contained concern that doesn't need to sit in the file that
also carries sign-in and the admin console.
"""
import uuid

from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response

from .auth import PortalScopedAPIView, require_tree_editor, require_user
from .models import ActivityLog, TreeProfile
from .moderation import log_activity

# Same limits and validation as wall.views' photo upload — this field is
# served back from MEDIA_URL exactly like that one, so it gets the same
# checks: a size cap, a declared-type allowlist, and confirmation the bytes
# actually decode as an image, not just a size/extension that looks right.
MAX_PHOTO_BYTES = 8 * 1024 * 1024
ALLOWED_PHOTO_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}


def _validate_photo(f):
    """Return an error string, or None if the file is an acceptable photo."""
    if f.size > MAX_PHOTO_BYTES:
        return f'That photo is {f.size / 1024 / 1024:.1f} MB. Please keep it under 8 MB.'
    if (getattr(f, 'content_type', '') or '').lower() not in ALLOWED_PHOTO_TYPES:
        return 'Photos must be a JPG, PNG, WebP or GIF.'
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


def _brief(p, request=None):
    url = p.photo.url if p.photo else ''
    return {
        'person_id': p.person_id,
        'name': p.name, 'role': p.role, 'department': p.department,
        'photo_url': (request.build_absolute_uri(url) if (url and request) else url),
        'is_new': p.is_new, 'parent_hod_id': p.parent_hod_id, 'is_hidden': p.is_hidden,
        'updated_by_name': p.updated_by_name,
        'updated_at': p.updated_at.isoformat(),
    }


class TreeProfileListView(PortalScopedAPIView):
    """GET: every override and every added person that exists so far. Any
    signed-in employee — same audience as the chart itself; writing is
    gated separately, below.

    POST: add a brand-new person the static chart doesn't know about —
    either a new top-level HOD card (parent_hod_id left blank) or a new
    member of an existing HOD's sub-tree (parent_hod_id set to that HOD's
    own id). Editors only, same gate as an edit.
    """

    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request):
        user, err = require_user(request)
        if err:
            return err
        rows = TreeProfile.objects.all()
        return Response({'profiles': [_brief(p, request) for p in rows]})

    def post(self, request):
        user, err = require_tree_editor(request)
        if err:
            return err
        d = request.data
        name = (d.get('name') or '').strip()[:200]
        if not name:
            return Response({'error': 'A name is required.'}, status=400)

        photo = request.FILES.get('photo')
        if photo:
            problem = _validate_photo(photo)
            if problem:
                return Response({'error': problem}, status=400)

        # Server-generated: nothing on the frontend refers to this id yet,
        # unlike an override's person_id which has to match an existing
        # card. Prefixed by where it lives so it reads sensibly next to
        # override ids in the admin list (roompulse/wall follow the same
        # "readable id" habit for generated identifiers).
        parent_hod_id = (d.get('parent_hod_id') or '').strip()[:150]
        prefix = parent_hod_id or 'hod'
        person_id = f'{prefix}--new-{uuid.uuid4().hex[:10]}'

        p = TreeProfile.objects.create(
            person_id=person_id, name=name,
            role=(d.get('role') or '').strip()[:200],
            department=(d.get('department') or '').strip()[:200],
            photo=photo or None,
            is_new=True, parent_hod_id=parent_hod_id,
            updated_by_name=user.name, updated_by_email=user.email,
        )
        log_activity(user, ActivityLog.Action.CREATED, obj=p,
                     summary=f'Added {p.name} to APIS Tree'
                             + (f' under {parent_hod_id}' if parent_hod_id else ' as a new HOD'),
                     detail={'person_id': person_id, 'parent_hod_id': parent_hod_id}, request=request)
        return Response({'message': f'{p.name} added.', 'profile': _brief(p, request)}, status=201)


class TreeProfileDetailView(PortalScopedAPIView):
    """PATCH: create or update one card's override (multipart, so a photo
    can come with it) — including `hidden`, which removes one of the
    static chart's own people (see TreeProfile.is_hidden). DELETE: clear
    the override, reverting the card back to its baseline value, or for an
    added person, remove them outright. Editors only — Super Admin, or
    anyone granted `can_edit_tree` from Admin Console."""

    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def patch(self, request, person_id):
        user, err = require_tree_editor(request)
        if err:
            return err
        person_id = (person_id or '').strip()[:150]
        if not person_id:
            return Response({'error': 'No card given.'}, status=400)

        # Validated before anything touches the database: get_or_create below
        # would otherwise leave a blank row behind for a first-time edit that
        # fails on the photo alone.
        photo = request.FILES.get('photo')
        if photo:
            problem = _validate_photo(photo)
            if problem:
                return Response({'error': problem}, status=400)

        p, _ = TreeProfile.objects.get_or_create(person_id=person_id)
        d = request.data
        if 'name' in d:
            p.name = (d.get('name') or '').strip()[:200]
        if 'role' in d:
            p.role = (d.get('role') or '').strip()[:200]
        if 'department' in d:
            p.department = (d.get('department') or '').strip()[:200]
        if photo:
            p.photo = photo
        was_hidden = p.is_hidden
        if 'hidden' in d:
            # A multipart value is always a string — 'false' is truthy in
            # Python, so it has to be compared as text, not just bool()'d.
            p.is_hidden = str(d.get('hidden')).strip().lower() in ('1', 'true', 'yes')
        p.updated_by_name = user.name
        p.updated_by_email = user.email
        p.save()

        if p.is_hidden != was_hidden:
            log_activity(user, ActivityLog.Action.HIDDEN if p.is_hidden else ActivityLog.Action.EDITED, obj=p,
                         summary=(f'Removed {p.name or person_id} from APIS Tree' if p.is_hidden
                                 else f'Restored {p.name or person_id} to APIS Tree'),
                         detail={'person_id': person_id}, request=request)
        else:
            log_activity(user, ActivityLog.Action.EDITED, obj=p,
                         summary=f'Edited an APIS Tree card ({p.name or person_id})',
                         detail={'person_id': person_id}, request=request)
        return Response({'message': 'Saved.', 'profile': _brief(p, request)})

    def delete(self, request, person_id):
        """For an override, this reverts the card to its baseline. For an
        added person there is no baseline underneath — this removes them
        from the tree outright."""
        user, err = require_tree_editor(request)
        if err:
            return err
        p = TreeProfile.objects.filter(person_id=(person_id or '').strip()[:150]).first()
        if not p:
            return Response({'message': 'Nothing to remove.'})

        was_new, name = p.is_new, (p.name or p.person_id)
        log_activity(user, ActivityLog.Action.DELETED if was_new else ActivityLog.Action.EDITED, obj=p,
                     summary=(f'Removed {name} from APIS Tree' if was_new
                             else f'Reverted an APIS Tree card ({name}) to its default'),
                     detail={'person_id': p.person_id}, request=request)
        p.delete()
        return Response({'message': f'{name} removed.' if was_new else 'Reverted to default.'})
