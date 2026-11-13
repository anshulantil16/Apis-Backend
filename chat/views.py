"""The messaging API.

Every endpoint here is reached with a portal session token and is gated on
the `chat` app key, so who may use it is the same Admin Console decision as
every other tool.

The rule this file exists to keep: a message, and anything attached to it,
is served to the people in its conversation and to nobody else. That is
enforced by looking conversations up THROUGH the caller's memberships
rather than by fetching one and then asking whether they belong -- the
second shape is one forgotten `if` away from handing somebody another
pair's thread.
"""
from django.db import transaction
from django.db.models import Max, Q
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from accounts.auth import PortalScopedAPIView, require_app
from accounts.models import PortalUser

from . import files
from .models import Attachment, Conversation, Membership, Message

APP = 'chat'
MAX_BODY = 4000
CHANGED_CAP = 200
PAGE = 50
MAX_GROUP = 50


def _membership(user, conversation_id):
    """The caller's membership of that conversation, or None.

    The only way this module reaches a Conversation. See the module
    docstring.
    """
    return (Membership.objects
            .select_related('conversation')
            .filter(user_id=user.id, conversation_id=conversation_id)
            .first())


def _person(u):
    return {
        'id': u.id, 'name': u.name or u.employee_code,
        'employee_code': u.employee_code, 'email': u.email or '',
        'designation': u.designation or '', 'department': u.department or '',
    }


def _can_chat(u):
    return u.is_superadmin or u.can_open(APP)


def serialize_attachment(a):
    return {
        'id': a.id, 'name': a.name, 'size': a.size,
        'kind': a.kind, 'content_type': a.content_type,
    }


def serialize_message(m, viewer, seen_cutoff=None):
    """One message as the screen needs it.

    `seen_cutoff` is the earliest last_read_at among everybody else in the
    conversation: your own message counts as seen once all of them have
    read past it. Passed in rather than looked up here, so a page of fifty
    messages does not run fifty membership queries.
    """
    reply = m.reply_to
    return {
        'id': m.id,
        'body': '' if m.is_deleted else m.body,
        'kind': m.kind,
        'deleted': m.is_deleted,
        'edited': m.edited_at is not None,
        'sent_at': m.created_at.isoformat(),
        'sender_id': m.sender_id,
        'mine': m.sender_id == viewer.id,
        'sender_name': m.sender.name if m.sender else 'Someone who has left',
        'attachments': [serialize_attachment(a) for a in m.attachments.all()],
        'reply_to': None if not reply else {
            'id': reply.id,
            'sender_name': reply.sender.name if reply.sender else 'Someone who has left',
            'body': ('Message deleted' if reply.is_deleted
                     else (reply.body or _attachment_summary(reply)))[:160],
        },
        'seen': (m.sender_id == viewer.id and seen_cutoff is not None
                 and seen_cutoff >= m.created_at),
    }


def _attachment_summary(m):
    first = m.attachments.first()
    if not first:
        return ''
    extra = m.attachments.count() - 1
    return f'{first.name}' + (f' and {extra} more' if extra > 0 else '')


def _seen_cutoff(conversation, user):
    """How far everybody ELSE has read. None while somebody has read nothing."""
    others = conversation.members.exclude(user_id=user.id).values_list('last_read_at', flat=True)
    stamps = list(others)
    if not stamps or any(s is None for s in stamps):
        return None
    return min(stamps)


def _unread_for(me):
    unread = me.conversation.messages.exclude(sender_id=me.user_id).exclude(kind=Message.SYSTEM)
    if me.last_read_at:
        unread = unread.filter(created_at__gt=me.last_read_at)
    return unread.count()


def _row(me, user):
    conv = me.conversation
    last = conv.messages.order_by('-id').first()
    members = [_person(m.user) for m in conv.members.select_related('user')]
    return {
        'id': conv.id,
        'is_group': conv.is_group,
        'title': conv.name_for(user),
        'person': _person(conv.other_member(user)) if not conv.is_group and conv.other_member(user) else None,
        'members': members,
        'last_message': serialize_message(last, user) if last else None,
        'unread': _unread_for(me),
        'last_message_at': conv.last_message_at.isoformat(),
    }


class ConversationsView(PortalScopedAPIView):
    """The caller's threads, most recent first, with what is unread."""

    def get(self, request):
        user, err = require_app(request, APP)
        if err:
            return err
        mine = (Membership.objects
                .select_related('conversation')
                .filter(user_id=user.id)
                .order_by('-conversation__last_message_at'))

        out = []
        for me in mine:
            # A thread cleared from this person's list stays gone until
            # somebody says something new.
            if me.cleared_at and me.conversation.last_message_at <= me.cleared_at:
                continue
            out.append(_row(me, user))
        return Response({'conversations': out, 'total_unread': sum(c['unread'] for c in out)})

    def post(self, request):
        """Open the thread with one person, or start a group.

        The one-to-one case is idempotent on purpose: tapping a colleague
        twice must land in the same conversation, not make a second one
        with half the history. A group is not -- two groups with the same
        people are a normal thing to want.
        """
        user, err = require_app(request, APP)
        if err:
            return err
        # Asked for by the presence of the key, not by its truthiness: an
        # empty list is a group with nobody in it, which deserves "add at
        # least one colleague" rather than falling through to the
        # one-to-one path and answering "no such employee".
        if 'employee_codes' in request.data:
            return self._group(request, user, request.data.get('employee_codes') or [])

        code = str(request.data.get('employee_code') or '').strip()
        other = PortalUser.objects.filter(employee_code=code, is_active=True).first() if code else None
        if not other:
            return Response({'error': 'No such employee.'}, status=404)
        if other.id == user.id:
            return Response({'error': 'You cannot start a conversation with yourself.'}, status=400)
        if not _can_chat(other):
            return Response({'error': f'{other.name} has not been given access to Messages yet. '
                                      'Ask an administrator to grant it.'}, status=400)

        existing = (Conversation.objects
                    .filter(is_group=False)
                    .filter(members__user_id=user.id)
                    .filter(members__user_id=other.id)
                    .first())
        if existing:
            return Response({'id': existing.id, 'person': _person(other), 'created': False})
        with transaction.atomic():
            conv = Conversation.objects.create(created_by=user)
            Membership.objects.create(conversation=conv, user=user)
            Membership.objects.create(conversation=conv, user=other)
        return Response({'id': conv.id, 'person': _person(other), 'created': True}, status=201)

    def _group(self, request, user, codes):
        title = str(request.data.get('title') or '').strip()[:120]
        if not title:
            return Response({'error': 'Give the group a name.'}, status=400)
        wanted = [str(c).strip() for c in codes if str(c).strip()]
        people = list(PortalUser.objects.filter(employee_code__in=wanted, is_active=True))
        people = [p for p in people if p.id != user.id and _can_chat(p)]
        if not people:
            return Response({'error': 'Add at least one colleague who has Messages.'}, status=400)
        if len(people) + 1 > MAX_GROUP:
            return Response({'error': f'A group can hold {MAX_GROUP} people.'}, status=400)

        with transaction.atomic():
            conv = Conversation.objects.create(is_group=True, title=title, created_by=user)
            Membership.objects.create(conversation=conv, user=user)
            for p in people:
                Membership.objects.create(conversation=conv, user=p)
            Message.objects.create(conversation=conv, sender=user, kind=Message.SYSTEM,
                                   body=f'{user.name} created "{title}"')
        return Response({'id': conv.id, 'created': True, 'is_group': True, 'title': title}, status=201)


class MessagesView(PortalScopedAPIView):
    """Read and write one thread."""
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get(self, request, conversation_id):
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me:
            # 404 rather than 403: whether a conversation exists between
            # other people is itself none of the caller's business.
            return Response({'error': 'No such conversation.'}, status=404)

        qs = (me.conversation.messages
              .select_related('sender', 'reply_to', 'reply_to__sender')
              .prefetch_related('attachments'))
        truncated = False          # only a `since` poll can set this
        after = request.query_params.get('after')
        since = request.query_params.get('since')
        q = (request.query_params.get('q') or '').strip()
        before = request.query_params.get('before')

        if since:
            # Everything that CHANGED, not everything that is new.
            #
            # `after` asks for messages with a higher id, which is the wrong
            # question for a delete or an edit: those alter a row that is
            # already on the reader's screen and never raise the highest id.
            # So a deleted message sat there looking normal until the reader
            # happened to reload. Asking by time instead catches all three
            # kinds of change in one request.
            t = parse_datetime(since)
            if t is None:
                return Response({'error': 'since must be a timestamp.'}, status=400)
            if timezone.is_naive(t):
                t = timezone.make_aware(t)
            rows = list(qs.filter(Q(created_at__gt=t) | Q(edited_at__gt=t)
                                  | Q(deleted_at__gt=t)).order_by('id')[:CHANGED_CAP + 1])
            # More changed than one answer carries -- a laptop shut on
            # Friday asking on Monday. Say so rather than quietly handing
            # back the first 200 and letting the client move its mark past
            # everything it was not told about.
            truncated = len(rows) > CHANGED_CAP
            rows = rows[:CHANGED_CAP]
            has_more = False
        elif after:
            try:
                rows = list(qs.filter(id__gt=int(after)).order_by('id')[:200])
            except (TypeError, ValueError):
                return Response({'error': 'after must be a message id.'}, status=400)
            has_more = False
        elif q:
            # Searching a thread reads newest-first, like looking back
            # through it, and shows no "load older" -- the results are the
            # whole answer, not a window onto it.
            rows = list(qs.filter(body__icontains=q).exclude(deleted_at__isnull=False)
                        .order_by('-id')[:PAGE])[::-1]
            has_more = False
        else:
            page = qs.order_by('-id')
            if before:
                try:
                    page = page.filter(id__lt=int(before))
                except (TypeError, ValueError):
                    return Response({'error': 'before must be a message id.'}, status=400)
            rows = list(page[:PAGE])[::-1]
            has_more = bool(rows) and qs.filter(id__lt=rows[0].id).exists()

        cutoff = _seen_cutoff(me.conversation, user)
        other = me.conversation.other_member(user)
        return Response({
            'conversation_id': me.conversation.id,
            # The moment this answer was true, by this server's clock. The
            # client sends it back as `since`, so a browser whose clock is
            # off never asks about a window that does not exist here.
            'now': timezone.now().isoformat(),
            # Only ever true for a `since` poll: more changed than this
            # answer holds, so the client must re-read the thread.
            'truncated': truncated,
            'is_group': me.conversation.is_group,
            'title': me.conversation.name_for(user),
            'person': _person(other) if not me.conversation.is_group and other else None,
            'members': [_person(m.user) for m in me.conversation.members.select_related('user')],
            'messages': [serialize_message(m, user, cutoff) for m in rows],
            'has_more': has_more,
        })

    def post(self, request, conversation_id):
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me:
            return Response({'error': 'No such conversation.'}, status=404)

        body = str(request.data.get('body') or '').strip()
        uploads = request.FILES.getlist('files') if hasattr(request, 'FILES') else []
        if not body and not uploads:
            return Response({'error': 'Type something, or attach a file.'}, status=400)
        if len(body) > MAX_BODY:
            return Response({'error': f'That message is too long (limit {MAX_BODY} characters).'}, status=400)
        if len(uploads) > files.MAX_PER_MESSAGE:
            return Response({'error': f'Up to {files.MAX_PER_MESSAGE} files at a time.'}, status=400)

        # Everything is checked before anything is written, so a message is
        # never left half-sent with two of three files on it.
        checked = []
        for up in uploads:
            try:
                kind, content_type, name = files.check(up)
            except files.RejectedFile as e:
                return Response({'error': str(e)}, status=400)
            checked.append((up, kind, content_type, name))

        reply_to = None
        raw_reply = request.data.get('reply_to')
        if raw_reply:
            # Only within this thread: a reply pointing at another
            # conversation would quote text the reader may not be allowed
            # to see.
            reply_to = me.conversation.messages.filter(id=raw_reply).first()
            if reply_to is None:
                return Response({'error': 'That message is not in this conversation.'}, status=400)

        with transaction.atomic():
            m = Message.objects.create(conversation=me.conversation, sender=user,
                                       body=body, reply_to=reply_to)
            for up, kind, content_type, name in checked:
                Attachment.objects.create(message=m, file=up, name=name, size=up.size,
                                          content_type=content_type, kind=kind)
            now = timezone.now()
            Conversation.objects.filter(id=me.conversation_id).update(last_message_at=now)
            # Sending is reading: your own message must never come back to
            # you as unread, and a thread you had cleared returns the
            # moment you use it again.
            Membership.objects.filter(id=me.id).update(last_read_at=now, cleared_at=None)
        return Response({'message': serialize_message(m, user)}, status=201)


class MessageDetailView(PortalScopedAPIView):
    """Edit or delete something you sent."""

    def patch(self, request, conversation_id, message_id):
        user, err = require_app(request, APP)
        if err:
            return err
        m = self._own(user, conversation_id, message_id)
        if m is None:
            return Response({'error': 'No such message.'}, status=404)
        body = str(request.data.get('body') or '').strip()
        if not body:
            return Response({'error': 'Type something first.'}, status=400)
        m.body, m.edited_at = body[:MAX_BODY], timezone.now()
        m.save(update_fields=['body', 'edited_at'])
        return Response({'message': serialize_message(m, user)})

    def delete(self, request, conversation_id, message_id):
        user, err = require_app(request, APP)
        if err:
            return err
        m = self._own(user, conversation_id, message_id)
        if m is None:
            return Response({'error': 'No such message.'}, status=404)
        # The row stays so the thread still reads sensibly around it, but
        # the files go: an unsent photo that is still downloadable is not
        # unsent in any sense the sender meant.
        for a in m.attachments.all():
            a.file.delete(save=False)
            a.delete()
        m.body, m.deleted_at = '', timezone.now()
        m.save(update_fields=['body', 'deleted_at'])
        return Response({'message': serialize_message(m, user)})

    @staticmethod
    def _own(user, conversation_id, message_id):
        """Only your own message, and only in a thread you are in.

        Both halves matter: without the sender check anybody in the thread
        could rewrite what somebody else said, which is worse than reading
        it. System messages belong to nobody and are never editable.
        """
        me = _membership(user, conversation_id)
        if not me:
            return None
        return me.conversation.messages.filter(id=message_id, sender_id=user.id,
                                               kind=Message.TEXT,
                                               deleted_at__isnull=True).first()


class AttachmentView(PortalScopedAPIView):
    """The file itself, to whoever is in the conversation it was sent to.

    Not served from /media/, which is blocked for this folder in
    config/urls.py. A photo of somebody's payslip on an intranet share is
    still a file anyone holding the link can take.
    """

    def get(self, request, attachment_id):
        user, err = require_app(request, APP)
        if err:
            return err
        a = (Attachment.objects
             .select_related('message')
             .filter(id=attachment_id,
                     message__conversation__members__user_id=user.id)
             .first())
        if not a or a.message.is_deleted:
            raise Http404
        try:
            handle = a.file.open('rb')
        except (FileNotFoundError, OSError):
            raise Http404

        r = FileResponse(handle, content_type=a.content_type or 'application/octet-stream')
        # Photos render in the thread; everything else is downloaded rather
        # than opened in a tab, so nothing stored here is ever rendered as
        # a document by this origin.
        disposition = 'inline' if a.is_image else 'attachment'
        safe = a.name.replace('"', '').replace('\\', '')
        r['Content-Disposition'] = f'{disposition}; filename="{safe}"'
        # Whatever is on disk, the browser treats it as the type we decided
        # from the extension and nothing else.
        r['X-Content-Type-Options'] = 'nosniff'
        r['X-Robots-Tag'] = 'noindex, nofollow, noarchive'
        r['Cache-Control'] = 'private, no-store'
        return r


class ReadView(PortalScopedAPIView):
    """Mark a thread read up to now."""

    def post(self, request, conversation_id):
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me:
            return Response({'error': 'No such conversation.'}, status=404)
        Membership.objects.filter(id=me.id).update(last_read_at=timezone.now())
        return Response({'ok': True})


class ClearView(PortalScopedAPIView):
    """Hide a thread from your own list. Everyone else keeps theirs."""

    def post(self, request, conversation_id):
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me:
            return Response({'error': 'No such conversation.'}, status=404)
        Membership.objects.filter(id=me.id).update(cleared_at=timezone.now())
        return Response({'ok': True})


class MembersView(PortalScopedAPIView):
    """Add somebody to a group, or leave one."""

    def post(self, request, conversation_id):
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me or not me.conversation.is_group:
            return Response({'error': 'No such group.'}, status=404)
        code = str(request.data.get('employee_code') or '').strip()
        p = PortalUser.objects.filter(employee_code=code, is_active=True).first() if code else None
        if not p:
            return Response({'error': 'No such employee.'}, status=404)
        if not _can_chat(p):
            return Response({'error': f'{p.name} has not been given access to Messages yet.'}, status=400)
        if me.conversation.members.filter(user_id=p.id).exists():
            return Response({'error': f'{p.name} is already in this group.'}, status=400)
        if me.conversation.members.count() >= MAX_GROUP:
            return Response({'error': f'A group can hold {MAX_GROUP} people.'}, status=400)

        with transaction.atomic():
            Membership.objects.create(conversation=me.conversation, user=p)
            # Said out loud in the thread. Somebody joining a conversation
            # part-way through is something the others should see happen,
            # not discover later.
            Message.objects.create(conversation=me.conversation, sender=user, kind=Message.SYSTEM,
                                   body=f'{user.name} added {p.name}')
            Conversation.objects.filter(id=me.conversation_id).update(last_message_at=timezone.now())
        return Response({'members': [_person(m.user) for m in
                                     me.conversation.members.select_related('user')]}, status=201)

    def delete(self, request, conversation_id):
        """Leave the group. The messages stay; they belong to the thread."""
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me or not me.conversation.is_group:
            return Response({'error': 'No such group.'}, status=404)
        conv = me.conversation
        with transaction.atomic():
            me.delete()
            Message.objects.create(conversation=conv, sender=user, kind=Message.SYSTEM,
                                   body=f'{user.name} left')
            Conversation.objects.filter(id=conv.id).update(last_message_at=timezone.now())
        return Response({'ok': True})


class PeopleView(PortalScopedAPIView):
    """Who you can message: colleagues who have been granted Messages too.

    Somebody without the tool cannot read what you send them, so offering
    them in the picker would produce a message that is never seen and never
    answered -- which reads as being ignored.
    """

    def get(self, request):
        user, err = require_app(request, APP)
        if err:
            return err
        q = (request.query_params.get('q') or '').strip()
        people = PortalUser.objects.filter(is_active=True).exclude(id=user.id)
        if q:
            people = people.filter(Q(name__icontains=q) | Q(employee_code__icontains=q)
                                   | Q(email__icontains=q) | Q(department__icontains=q))
        rows = [_person(p) for p in people.order_by('name')[:500] if _can_chat(p)]
        return Response({'people': rows[:200], 'total': len(rows)})


class UnreadView(PortalScopedAPIView):
    """One small number for the badge, cheap enough to poll."""

    def get(self, request):
        user, err = require_app(request, APP)
        if err:
            return err
        mine = Membership.objects.select_related('conversation').filter(user_id=user.id)
        total = sum(_unread_for(me) for me in mine)
        latest = mine.aggregate(at=Max('conversation__last_message_at'))['at']
        return Response({'unread': total, 'latest': latest.isoformat() if latest else None})
