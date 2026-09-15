"""Vacancies, behind the approval gate.

Before this, GET returned every row to anyone and POST let anyone — signed in
or not, on a server with a public IP — put a job opening on the company's home
page instantly. Now: you must be signed in to propose one, it is invisible
until a superadmin approves it, and every action is attributed in the activity
log.
"""
from rest_framework.response import Response
from rest_framework import status as http

from accounts.auth import PortalScopedAPIView, optional_user, require_superadmin, require_user
from accounts.moderation import ModerationStatus, log_activity

from .models import Vacancy

REQUIRED_FIELDS = ['title', 'function', 'department', 'location', 'state']

# What a submitter may set. `status` and every moderation column are absent on
# purpose — a submitter does not get to decide whether their own row is
# approved, nor mark a position closed.
WRITABLE = {
    'title': 'title', 'function': 'function', 'department': 'department',
    'grade': 'grade', 'location': 'location', 'state': 'state',
    'reportingManager': 'reporting_manager', 'experience': 'experience',
    'education': 'education',
}


def _approved_or_own(viewer):
    """Approved rows, plus this viewer's own pending/rejected ones."""
    from django.db.models import Q
    return Q(moderation_status=ModerationStatus.APPROVED) | Q(submitted_by=viewer)


def _serialize(v, viewer=None):
    """camelCase-ish shape the frontend's VacancyListing type expects.

    The moderation fields ride along so the popup can grey out a row the
    viewer submitted and is still waiting on, and so the console can render
    the queue from the same endpoint.
    """
    mine = bool(viewer and v.submitted_by_id == viewer.id)
    return {
        'id': v.id, 'function': v.function, 'department': v.department, 'title': v.title,
        'grade': v.grade, 'location': v.location, 'state': v.state,
        'reportingManager': v.reporting_manager, 'type': v.type,
        'experience': v.experience, 'education': v.education, 'status': v.status,
        'moderationStatus': v.moderation_status,
        'submittedBy': v.submitted_by_name or '',
        'submittedByEmail': v.submitted_by_email or '',
        'reviewNote': v.review_note or '',
        'isMine': mine,
    }


class VacancyListView(PortalScopedAPIView):
    """GET — what the caller is allowed to see. POST — propose a new one."""

    def get(self, request):
        viewer = optional_user(request)

        # A superadmin sees everything, because the console and the popup are
        # the same list to them. Everyone else sees what has been approved,
        # plus their own submissions still waiting — without that, a person
        # fills in the form, sees nothing appear, and files it again.
        if viewer and viewer.is_superadmin:
            rows = Vacancy.objects.all()
        elif viewer:
            rows = Vacancy.objects.filter(_approved_or_own(viewer))
        else:
            rows = Vacancy.published()

        rows = rows.select_related('submitted_by')
        return Response([_serialize(v, viewer) for v in rows])

    def post(self, request):
        user, err = require_user(request)
        if err:
            return err

        data = request.data
        missing = [f for f in REQUIRED_FIELDS if not str(data.get(f, '')).strip()]
        if missing:
            return Response({'error': f'Missing required field(s): {", ".join(missing)}'},
                            status=http.HTTP_400_BAD_REQUEST)

        fields = {col: str(data.get(key, '') or '').strip()
                  for key, col in WRITABLE.items()}
        fields['type'] = data.get('type') if data.get('type') in ('New', 'Replacement') else 'New'

        v = Vacancy(**fields)
        v.attribute_to(user)
        # The approving authority does not queue work for itself — but the
        # activity log still records who created it.
        if user.is_superadmin:
            v.set_review(user, ModerationStatus.APPROVED, 'Added by an administrator.')
        v.save()

        log_activity(user, 'created', v, summary=f'Vacancy: {v.moderation_label()}',
                     detail={'auto_approved': user.is_superadmin}, request=request)

        return Response({
            **_serialize(v, user),
            'message': ('Vacancy added and published.' if v.is_published else
                        'Sent to the administrator for approval. It will appear on the '
                        'dashboard once approved.'),
        }, status=http.HTTP_201_CREATED)


class VacancyDetailView(PortalScopedAPIView):
    """Superadmin-only edit and delete.

    Anyone may propose a vacancy; only an administrator may change one after
    the fact or remove it. Both are logged, and a delete records what it
    removed so the log still means something once the row is gone.
    """

    def _get(self, pk):
        return Vacancy.objects.filter(pk=pk).first()

    def patch(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        v = self._get(pk)
        if not v:
            return Response({'error': 'Vacancy not found.'}, status=http.HTTP_404_NOT_FOUND)

        changed = {}
        for key, col in WRITABLE.items():
            if key in request.data:
                new = str(request.data.get(key) or '').strip()
                if new != getattr(v, col):
                    changed[col] = {'from': getattr(v, col), 'to': new}
                    setattr(v, col, new)

        if request.data.get('type') in ('New', 'Replacement') and request.data['type'] != v.type:
            changed['type'] = {'from': v.type, 'to': request.data['type']}
            v.type = request.data['type']

        # Open/closed is a business fact about the position, editable here too.
        new_status = request.data.get('status')
        if new_status in ('Active', 'Closed') and new_status != v.status:
            changed['status'] = {'from': v.status, 'to': new_status}
            v.status = new_status

        if not changed:
            return Response(_serialize(v, user))

        v.save()
        log_activity(user, 'edited', v, summary=f'Vacancy: {v.moderation_label()}',
                     detail={'changed': changed}, request=request)
        return Response(_serialize(v, user))

    def delete(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        v = self._get(pk)
        if not v:
            return Response({'error': 'Vacancy not found.'}, status=http.HTTP_404_NOT_FOUND)

        label, vid = v.moderation_label(), v.id
        snapshot = _serialize(v)
        v.delete()
        log_activity(user, 'deleted', object_type='vacancy', object_id=vid,
                     summary=f'Vacancy: {label}', detail={'removed': snapshot},
                     request=request)
        return Response({'message': 'Vacancy removed.'})


class VacancyStatusView(PortalScopedAPIView):
    """PATCH — flip a vacancy between Active/Closed.

    Superadmin only. Closing a vacancy takes it off the dashboard for everyone,
    which is not something an unauthenticated caller should be able to do — and
    it used to be exactly that.
    """

    def patch(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err

        v = Vacancy.objects.filter(pk=pk).first()
        if not v:
            return Response({'error': 'Vacancy not found.'}, status=http.HTTP_404_NOT_FOUND)

        new_status = request.data.get('status')
        if new_status not in ('Active', 'Closed'):
            return Response({'error': "status must be 'Active' or 'Closed'."},
                            status=http.HTTP_400_BAD_REQUEST)

        if new_status != v.status:
            was = v.status
            v.status = new_status
            v.save(update_fields=['status', 'updated_at'])
            log_activity(user, 'hidden' if new_status == 'Closed' else 'published', v,
                         summary=f'Vacancy {new_status.lower()}: {v.moderation_label()}',
                         detail={'status': {'from': was, 'to': new_status}}, request=request)

        return Response(_serialize(v, user))
