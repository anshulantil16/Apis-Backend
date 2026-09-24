"""The superadmin's control surface over everything on the dashboard.

One queue and one activity feed across every content type, rather than an
approval screen per app. Adding a new kind of dashboard content means adding
one line to CONTENT_TYPES — the console picks it up without further change.
"""
from django.apps import apps
from rest_framework.response import Response

from .auth import PortalScopedAPIView, require_superadmin
from .models import ActivityLog
from .moderation import ModerationStatus, log_activity

# Everything a superadmin moderates. label is what the console shows as the
# filter chip; the model must inherit ModeratedContent.
CONTENT_TYPES = {
    'vacancy':   {'model': ('vacancies', 'Vacancy'),  'label': 'Vacancies'},
    'wallphoto': {'model': ('wall', 'WallPhoto'),     'label': 'APIS Wall'},
    'announcement': {'model': ('noticeboard', 'Announcement'), 'label': 'Announcements'},
    'news':      {'model': ('noticeboard', 'NewsItem'),   'label': 'Daily News'},
    'referral':  {'model': ('referrals', 'EmployeeReferral'), 'label': 'Referrals'},
}

PAGE_SIZE = 100


def _model_for(kind):
    spec = CONTENT_TYPES.get(kind)
    if not spec:
        return None
    return apps.get_model(*spec['model'])


class AdminModerationView(PortalScopedAPIView):
    """GET — the queue. POST — approve or reject one item.

    The queue defaults to everything still pending, across all types, oldest
    first: a submission that has been waiting three days should be the first
    thing an administrator sees, not the newest one.
    """
    def get(self, request):
        user, err = require_superadmin(request)
        if err:
            return err

        wanted = (request.query_params.get('status') or 'pending').lower()
        kind = (request.query_params.get('type') or '').lower()

        items, counts = [], {}
        for key in CONTENT_TYPES:
            model = _model_for(key)
            if model is None:
                continue

            counts[key] = model.objects.filter(
                moderation_status=ModerationStatus.PENDING).count()

            if kind and kind != key:
                continue

            rows = model.objects.select_related('submitted_by', 'reviewed_by')
            if wanted != 'all':
                rows = rows.filter(moderation_status=wanted)
            for obj in rows.order_by('created_at')[:PAGE_SIZE]:
                items.append(obj.moderation_payload(request))

        # Oldest first across the merged list, so the ordering means the same
        # thing whether one type is filtered or all of them are shown.
        items.sort(key=lambda r: r['submitted_at'] or '')

        return Response({
            'items': items[:PAGE_SIZE],
            'truncated': len(items) > PAGE_SIZE,
            'pending_counts': counts,
            'pending_total': sum(counts.values()),
            'types': [{'key': k, 'label': v['label']} for k, v in CONTENT_TYPES.items()],
        })

    def post(self, request):
        user, err = require_superadmin(request)
        if err:
            return err

        kind = (request.data.get('type') or '').lower()
        decision = (request.data.get('decision') or '').lower()
        note = (request.data.get('note') or '').strip()
        raw_ids = request.data.get('ids')
        ids = raw_ids if isinstance(raw_ids, list) else [request.data.get('id')]
        ids = [i for i in ids if i is not None]

        model = _model_for(kind)
        if model is None:
            return Response({'error': 'Unknown content type.'}, status=400)
        if decision not in (ModerationStatus.APPROVED, ModerationStatus.REJECTED):
            return Response({'error': "decision must be 'approved' or 'rejected'."},
                            status=400)
        if not ids:
            return Response({'error': 'Nothing selected.'}, status=400)

        done = 0
        for obj in model.objects.filter(pk__in=ids):
            obj.set_review(user, decision, note)
            obj.save(update_fields=['moderation_status', 'reviewed_by', 'reviewed_by_name',
                                    'reviewed_at', 'review_note'])
            log_activity(user, decision, obj,
                         summary=f'{model._meta.verbose_name.title()}: {obj.moderation_label()}',
                         detail={'note': note, 'submitted_by': obj.submitted_by_name},
                         request=request)
            done += 1

        if not done:
            return Response({'error': 'Those items no longer exist.'}, status=404)

        verb = 'approved' if decision == ModerationStatus.APPROVED else 'rejected'
        return Response({
            'message': f'{done} item{"" if done == 1 else "s"} {verb}.',
            'updated': done,
        })


class AdminActivityView(PortalScopedAPIView):
    """GET — who did what, newest first.

    Read-only by design: there is no endpoint that edits or deletes a log
    entry, including for a superadmin. See ActivityLog's docstring.
    """
    def get(self, request):
        user, err = require_superadmin(request)
        if err:
            return err

        rows = ActivityLog.objects.all()

        q = (request.query_params.get('q') or '').strip()
        if q:
            from django.db.models import Q
            rows = rows.filter(Q(actor_name__icontains=q) | Q(actor_email__icontains=q)
                               | Q(summary__icontains=q))

        for param, field in (('action', 'action'), ('type', 'object_type')):
            value = (request.query_params.get(param) or '').strip()
            if value:
                rows = rows.filter(**{field: value})

        try:
            limit = min(int(request.query_params.get('limit', PAGE_SIZE)), 500)
            offset = max(int(request.query_params.get('offset', 0)), 0)
        except (TypeError, ValueError):
            limit, offset = PAGE_SIZE, 0

        total = rows.count()
        page = rows[offset:offset + limit]

        return Response({
            'items': [{
                'id': r.id,
                'actor': r.actor_name or 'System',
                'actor_email': r.actor_email,
                'action': r.action,
                'object_type': r.object_type,
                'object_id': r.object_id,
                'summary': r.summary,
                'detail': r.detail,
                'ip_address': r.ip_address,
                'at': r.created_at.isoformat(),
            } for r in page],
            'total': total,
            'offset': offset,
            'limit': limit,
            'returned': len(page),
            'truncated': offset + len(page) < total,
        })
