"""Reading the noticeboard, and administering it.

Reads are open to any signed-in employee and return only what is live.
Writes are superadmin-only and every one of them is logged — an announcement
is a statement made to the whole company in its own name, and a holiday list
is what people plan leave around.
"""
from django.utils import timezone
from rest_framework.response import Response
from rest_framework import status as http

from accounts.auth import PortalScopedAPIView, optional_user, require_superadmin, require_user
from accounts.moderation import ModerationStatus, log_activity

from wall.views import _validate as validate_image

from .models import Announcement, Holiday, HolidayZone, NewsItem, NewsSource


def _announcement(a, viewer=None):
    return {
        'id': a.id, 'title': a.title, 'body': a.body, 'tone': a.tone,
        'date': a.announced_on.isoformat(),
        'expiresOn': a.expires_on.isoformat() if a.expires_on else None,
        'pinned': a.pinned,
        'moderationStatus': a.moderation_status,
        'submittedBy': a.submitted_by_name or '',
        'reviewNote': a.review_note or '',
        'isMine': bool(viewer and a.submitted_by_id == viewer.id),
    }


class AnnouncementListView(PortalScopedAPIView):
    """GET — what to show on the card. POST — propose a new notice."""

    def get(self, request):
        viewer = optional_user(request)
        if viewer and viewer.is_superadmin:
            # The console needs expired and pending ones too, to manage them.
            rows = Announcement.objects.all()
        elif viewer:
            # Their own proposal too, so it does not look like it vanished.
            rows = (Announcement.published() |
                    Announcement.objects.filter(submitted_by=viewer)).distinct()
        else:
            rows = Announcement.published()
        return Response([_announcement(a, viewer) for a in rows.select_related('submitted_by')])

    def post(self, request):
        user, err = require_user(request)
        if err:
            return err

        title = (request.data.get('title') or '').strip()
        body = (request.data.get('body') or '').strip()
        if not title or not body:
            return Response({'error': 'An announcement needs a title and something to say.'},
                            status=http.HTTP_400_BAD_REQUEST)

        a = Announcement(
            title=title[:200], body=body[:2000],
            tone=_tone(request.data.get('tone')),
            announced_on=_date(request.data.get('date')) or timezone.localdate(),
            expires_on=_date(request.data.get('expiresOn')),
            pinned=bool(request.data.get('pinned')) and user.is_superadmin,
        )
        a.attribute_to(user)
        if user.is_superadmin:
            a.set_review(user, ModerationStatus.APPROVED, 'Posted by an administrator.')
        a.save()

        log_activity(user, 'created', a, summary=f'Announcement: {a.title}',
                     detail={'auto_approved': user.is_superadmin}, request=request)
        return Response({
            **_announcement(a, user),
            'message': ('Announcement posted.' if a.is_published else
                        'Sent to the administrator for approval.'),
        }, status=http.HTTP_201_CREATED)


class AnnouncementDetailView(PortalScopedAPIView):
    """Superadmin edit and delete."""

    def patch(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        a = Announcement.objects.filter(pk=pk).first()
        if not a:
            return Response({'error': 'Announcement not found.'}, status=http.HTTP_404_NOT_FOUND)

        changed = {}
        for field, cap in (('title', 200), ('body', 2000)):
            if field in request.data:
                new = str(request.data.get(field) or '').strip()[:cap]
                if new and new != getattr(a, field):
                    changed[field] = {'from': getattr(a, field)[:80], 'to': new[:80]}
                    setattr(a, field, new)

        if 'tone' in request.data:
            tone = _tone(request.data.get('tone'))
            if tone != a.tone:
                changed['tone'] = {'from': a.tone, 'to': tone}
                a.tone = tone

        for key, field in (('date', 'announced_on'), ('expiresOn', 'expires_on')):
            if key in request.data:
                new = _date(request.data.get(key))
                if new != getattr(a, field):
                    changed[field] = {'from': str(getattr(a, field)), 'to': str(new)}
                    setattr(a, field, new)

        if 'pinned' in request.data:
            new = bool(request.data.get('pinned'))
            if new != a.pinned:
                changed['pinned'] = {'from': a.pinned, 'to': new}
                a.pinned = new

        if changed:
            a.save()
            log_activity(user, 'edited', a, summary=f'Announcement: {a.title}',
                         detail={'changed': changed}, request=request)
        return Response(_announcement(a, user))

    def delete(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        a = Announcement.objects.filter(pk=pk).first()
        if not a:
            return Response({'error': 'Announcement not found.'}, status=http.HTTP_404_NOT_FOUND)
        title, aid = a.title, a.id
        a.delete()
        log_activity(user, 'deleted', object_type='announcement', object_id=aid,
                     summary=f'Announcement: {title}', request=request)
        return Response({'message': 'Announcement removed.'})


# ── holidays ────────────────────────────────────────────────────────────────
class HolidayListView(PortalScopedAPIView):
    """GET — every zone with its holidays, in the shape the dashboard's zone
    picker already expects. POST — add one holiday to a zone (superadmin)."""

    def get(self, request):
        zones = HolidayZone.objects.prefetch_related('holidays')
        return Response([{
            'id': z.key, 'label': z.label,
            'holidays': [{'id': h.id, 'date': h.date.isoformat(), 'name': h.name, 'type': h.type}
                         for h in z.holidays.all()],
        } for z in zones])

    def post(self, request):
        user, err = require_superadmin(request)
        if err:
            return err

        zone = HolidayZone.objects.filter(key=request.data.get('zone')).first()
        date = _date(request.data.get('date'))
        name = (request.data.get('name') or '').strip()
        if not zone or not date or not name:
            return Response({'error': 'Pick a zone, a date and a name.'},
                            status=http.HTTP_400_BAD_REQUEST)

        h, created = Holiday.objects.get_or_create(
            zone=zone, date=date, name=name[:120],
            defaults={'type': 'National' if request.data.get('type') == 'National' else 'State'})
        if not created:
            return Response({'error': 'That zone already lists that holiday.'},
                            status=http.HTTP_400_BAD_REQUEST)

        log_activity(user, 'created', h, summary=f'Holiday: {name} ({zone.label})',
                     detail={'date': str(date), 'zone': zone.key}, request=request)
        return Response({'id': h.id, 'date': h.date.isoformat(), 'name': h.name,
                         'type': h.type, 'zone': zone.key}, status=http.HTTP_201_CREATED)


class HolidayDetailView(PortalScopedAPIView):
    """Superadmin edit and delete of one holiday."""

    def patch(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        h = Holiday.objects.select_related('zone').filter(pk=pk).first()
        if not h:
            return Response({'error': 'Holiday not found.'}, status=http.HTTP_404_NOT_FOUND)

        changed = {}
        name = (request.data.get('name') or '').strip()
        if name and name != h.name:
            changed['name'] = {'from': h.name, 'to': name}
            h.name = name[:120]
        date = _date(request.data.get('date'))
        if date and date != h.date:
            changed['date'] = {'from': str(h.date), 'to': str(date)}
            h.date = date
        if request.data.get('type') in ('National', 'State') and request.data['type'] != h.type:
            changed['type'] = {'from': h.type, 'to': request.data['type']}
            h.type = request.data['type']

        if changed:
            h.save()
            log_activity(user, 'edited', h, summary=f'Holiday: {h.name} ({h.zone.label})',
                         detail={'changed': changed}, request=request)
        return Response({'id': h.id, 'date': h.date.isoformat(), 'name': h.name,
                         'type': h.type, 'zone': h.zone.key})

    def delete(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        h = Holiday.objects.select_related('zone').filter(pk=pk).first()
        if not h:
            return Response({'error': 'Holiday not found.'}, status=http.HTTP_404_NOT_FOUND)
        label, hid, zone = h.name, h.id, h.zone.label
        h.delete()
        log_activity(user, 'deleted', object_type='holiday', object_id=hid,
                     summary=f'Holiday: {label} ({zone})', request=request)
        return Response({'message': 'Holiday removed.'})


# ── Daily News ──────────────────────────────────────────────
def _news(n, request=None, viewer=None):
    """One story, with its picture resolved to a single usable URL.

    The card should not have to know whether a picture was uploaded here or
    linked from elsewhere, so the choice is made once, on the way out: our own
    file wins when there is one, because it cannot rot.
    """
    src = ''
    if n.image:
        src = n.image.url
        if request is not None:
            src = request.build_absolute_uri(src)
    elif n.image_url:
        src = n.image_url

    return {
        'id': n.id, 'title': n.title, 'summary': n.summary,
        'category': n.category, 'categoryLabel': n.get_category_display(),
        'sourceName': n.source_name or '', 'sourceUrl': n.source_url or '',
        'image': src,
        'publishedOn': n.published_on.isoformat(),
        'expiresOn': n.expires_on.isoformat() if n.expires_on else None,
        'pinned': n.pinned,
        'createdAt': n.created_at.isoformat() if n.created_at else None,
        'moderationStatus': n.moderation_status,
        'submittedBy': n.submitted_by_name or '',
        'reviewNote': n.review_note or '',
        'isMine': bool(viewer and n.submitted_by_id == viewer.id),
    }


def _category(value):
    allowed = {c[0] for c in NewsItem.CATEGORY_CHOICES}
    return value if value in allowed else 'industry'


def _read_news_fields(data):
    """The fields a create and an edit share, validated the same way in both.

    Returns (values, error). Kept together so an edit cannot quietly accept
    something a create would have refused.
    """
    title = (data.get('title') or '').strip()
    summary = (data.get('summary') or '').strip()
    if not title or not summary:
        return None, 'A story needs a headline and a line or two about it.'

    url = (data.get('sourceUrl') or '').strip()
    if url and not url.lower().startswith(('http://', 'https://')):
        return None, 'The source link should start with http:// or https://'
    image_url = (data.get('imageUrl') or '').strip()
    if image_url and not image_url.lower().startswith(('http://', 'https://')):
        return None, 'The image link should start with http:// or https://'

    return {
        'title': title[:200], 'summary': summary[:600],
        'category': _category(data.get('category')),
        'source_name': (data.get('sourceName') or '').strip()[:120],
        'source_url': url[:2000], 'image_url': image_url[:2000],
        'published_on': _date(data.get('publishedOn')) or timezone.localdate(),
        'expires_on': _date(data.get('expiresOn')),
    }, None


class NewsListView(PortalScopedAPIView):
    """GET — the strip. POST — put a story forward."""

    def get(self, request):
        viewer = optional_user(request)
        everything = (request.query_params.get('scope') or '') == 'all'

        if everything and viewer and viewer.is_superadmin:
            # The console manages what exists, so it sees all of it.
            rows = NewsItem.objects.all()
        elif everything:
            rows = (NewsItem.published() |
                    NewsItem.objects.filter(submitted_by=viewer)).distinct() \
                if viewer else NewsItem.published()
        else:
            # The dashboard strip. Recent only, for everyone including the
            # super admin -- the point of looking at the dashboard is to see
            # what the company sees.
            rows = NewsItem.for_strip()

        rows = rows.select_related('submitted_by')
        if not everything:
            rows = rows[:12]
        return Response([_news(n, request, viewer) for n in rows])

    def post(self, request):
        user, err = require_user(request)
        if err:
            return err

        values, problem = _read_news_fields(request.data)
        if problem:
            return Response({'error': problem}, status=http.HTTP_400_BAD_REQUEST)

        f = request.FILES.get('image')
        if f is not None:
            bad = validate_image(f)
            if bad:
                return Response({'error': bad}, status=http.HTTP_400_BAD_REQUEST)

        n = NewsItem(**values)
        if f is not None:
            n.image = f
        n.pinned = bool(request.data.get('pinned')) and user.is_superadmin
        n.attribute_to(user)
        if user.is_superadmin:
            n.set_review(user, ModerationStatus.APPROVED, 'Posted by an administrator.')
        n.save()

        log_activity(user, 'created', n, summary='News: ' + n.title,
                     detail={'auto_approved': user.is_superadmin,
                             'category': n.category}, request=request)
        return Response({
            **_news(n, request, user),
            'message': ('Story published.' if n.is_published else
                        'Sent to the administrator for approval.'),
        }, status=http.HTTP_201_CREATED)


class NewsDetailView(PortalScopedAPIView):
    """Superadmin edit and delete."""

    def patch(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        n = NewsItem.objects.filter(pk=pk).first()
        if not n:
            return Response({'error': 'That story is no longer here.'},
                            status=http.HTTP_404_NOT_FOUND)

        values, problem = _read_news_fields(request.data)
        if problem:
            return Response({'error': problem}, status=http.HTTP_400_BAD_REQUEST)

        f = request.FILES.get('image')
        if f is not None:
            bad = validate_image(f)
            if bad:
                return Response({'error': bad}, status=http.HTTP_400_BAD_REQUEST)

        changed = [k for k, v in values.items() if getattr(n, k) != v]
        for k, v in values.items():
            setattr(n, k, v)
        if f is not None:
            n.image = f
            changed.append('image')
        n.pinned = bool(request.data.get('pinned'))
        n.save()

        log_activity(user, 'edited', n, summary='News: ' + n.title,
                     detail={'fields': ', '.join(changed) or 'no change'},
                     request=request)
        return Response(_news(n, request, user))

    def delete(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        n = NewsItem.objects.filter(pk=pk).first()
        if not n:
            return Response({'error': 'That story is no longer here.'},
                            status=http.HTTP_404_NOT_FOUND)
        title = n.title
        log_activity(user, 'deleted', n, summary='News: ' + title, request=request)
        n.delete()
        return Response({'message': 'Story removed.'})


# ── News sources ────────────────────────────────────────────
def _source(x):
    return {
        'id': x.id, 'name': x.name, 'kind': x.kind,
        'query': x.query, 'feedUrl': x.feed_url,
        'category': x.category, 'categoryLabel': x.get_category_display(),
        'autoPublish': x.auto_publish, 'isActive': x.is_active,
        'maxPerRun': x.max_per_run,
        'lastFetchedAt': x.last_fetched_at.isoformat() if x.last_fetched_at else None,
        'lastStatus': x.last_status, 'lastError': x.last_error,
        'lastFound': x.last_found, 'lastAdded': x.last_added,
        'resolvedUrl': x.resolved_url(),
    }


def _read_source_fields(data):
    name = (data.get('name') or '').strip()
    if not name:
        return None, 'Give the source a name you will recognise in a list.'

    kind = data.get('kind') if data.get('kind') in {'google_news', 'rss'} else 'google_news'
    query = (data.get('query') or '').strip()
    feed_url = (data.get('feedUrl') or '').strip()

    # Each kind needs its own one field, and neither is useful empty.
    if kind == 'google_news' and not query:
        return None, 'A Google News source needs something to search for.'
    if kind == 'rss':
        if not feed_url:
            return None, 'An RSS source needs the address of the feed.'
        if not feed_url.lower().startswith(('http://', 'https://')):
            return None, 'The feed address should start with http:// or https://'

    try:
        per_run = int(data.get('maxPerRun') or 5)
    except (TypeError, ValueError):
        per_run = 5

    return {
        'name': name[:120], 'kind': kind,
        'query': query[:300], 'feed_url': feed_url[:500],
        'category': _category(data.get('category')),
        'auto_publish': bool(data.get('autoPublish')),
        'is_active': data.get('isActive') is not False,
        'max_per_run': max(1, min(per_run, 20)),
    }, None


class NewsSourceListView(PortalScopedAPIView):
    """The feeds the strip pulls from. Superadmin only — this is the list that
    decides what the company home page can fill itself with."""

    def get(self, request):
        user, err = require_superadmin(request)
        if err:
            return err
        return Response([_source(x) for x in NewsSource.objects.all()])

    def post(self, request):
        user, err = require_superadmin(request)
        if err:
            return err
        values, problem = _read_source_fields(request.data)
        if problem:
            return Response({'error': problem}, status=http.HTTP_400_BAD_REQUEST)
        x = NewsSource.objects.create(**values)
        log_activity(user, 'created', x, summary='News source: ' + x.name,
                     detail={'kind': x.kind, 'auto_publish': x.auto_publish},
                     request=request)
        return Response({**_source(x), 'message': 'Source added.'},
                        status=http.HTTP_201_CREATED)


class NewsSourceDetailView(PortalScopedAPIView):

    def patch(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        x = NewsSource.objects.filter(pk=pk).first()
        if not x:
            return Response({'error': 'That source is no longer here.'},
                            status=http.HTTP_404_NOT_FOUND)
        values, problem = _read_source_fields(request.data)
        if problem:
            return Response({'error': problem}, status=http.HTTP_400_BAD_REQUEST)
        for k, v in values.items():
            setattr(x, k, v)
        x.save()
        log_activity(user, 'edited', x, summary='News source: ' + x.name, request=request)
        return Response(_source(x))

    def delete(self, request, pk):
        user, err = require_superadmin(request)
        if err:
            return err
        x = NewsSource.objects.filter(pk=pk).first()
        if not x:
            return Response({'error': 'That source is no longer here.'},
                            status=http.HTTP_404_NOT_FOUND)
        name = x.name
        # Stories already carried stay. They were reviewed on their own merits
        # and some are on the dashboard right now; dropping a feed is a
        # decision about what to fetch next, not a retraction of what it found.
        log_activity(user, 'deleted', x, summary='News source: ' + name, request=request)
        x.delete()
        return Response({'message': 'Source removed. Stories it already found are kept.'})


class NewsFetchView(PortalScopedAPIView):
    """POST — run the feeds now, instead of waiting for the scheduled run."""

    def post(self, request):
        user, err = require_superadmin(request)
        if err:
            return err
        if not NewsSource.objects.filter(is_active=True).exists():
            return Response({'error': 'No active sources to fetch from yet.'},
                            status=http.HTTP_400_BAD_REQUEST)

        from .newsfeed import fetch_all
        results = fetch_all()
        added = sum(r['added'] for r in results)
        failed = [r for r in results if r['error']]

        log_activity(user, 'edited', None, summary='Fetched the news feeds',
                     detail={'added': added, 'sources': len(results),
                             'failed': len(failed)}, request=request)

        if added:
            note = f'{added} new stor{"y" if added == 1 else "ies"}.'
        elif failed and len(failed) == len(results):
            note = 'Could not reach any of the feeds.'
        else:
            note = 'Nothing new — every story in those feeds is already here.'

        return Response({'results': results, 'added': added,
                         'failed': len(failed), 'message': note})


class NewsApproveView(PortalScopedAPIView):
    """POST — approve or reject waiting stories, several at a time.

    The queue exists so nothing publishes itself, but reviewing a morning's
    headlines one modal at a time is how a queue stops being used. This takes
    a list of ids.
    """

    def post(self, request):
        user, err = require_superadmin(request)
        if err:
            return err

        ids = request.data.get('ids') or []
        if not isinstance(ids, list) or not ids:
            return Response({'error': 'Pick at least one story.'},
                            status=http.HTTP_400_BAD_REQUEST)
        decision = request.data.get('decision')
        if decision not in {'approve', 'reject'}:
            return Response({'error': 'Say whether to approve or reject them.'},
                            status=http.HTTP_400_BAD_REQUEST)

        status_value = (ModerationStatus.APPROVED if decision == 'approve'
                        else ModerationStatus.REJECTED)
        rows = list(NewsItem.objects.filter(pk__in=ids[:100]))
        for n in rows:
            n.set_review(user, status_value, (request.data.get('note') or '')[:500])
            n.save()

        action = 'approved' if decision == 'approve' else 'rejected'
        log_activity(user, action, None,
                     summary=f'{len(rows)} news stor'
                             f'{"y" if len(rows) == 1 else "ies"} {action}',
                     request=request)
        word = 'published' if decision == 'approve' else 'rejected'
        return Response({'changed': len(rows),
                         'message': f'{len(rows)} stor{"y" if len(rows) == 1 else "ies"} {word}.'})


# ── helpers ─────────────────────────────────────────────────────────────────
def _tone(value):
    allowed = {c[0] for c in Announcement.TONE_CHOICES}
    return value if value in allowed else 'general'


def _date(value):
    """Parse an ISO date, or None. A blank string is 'not provided', which
    Django's own parser rejects outright rather than treating as null."""
    if not value:
        return None
    from django.utils.dateparse import parse_date
    try:
        return parse_date(str(value))
    except (TypeError, ValueError):
        return None
