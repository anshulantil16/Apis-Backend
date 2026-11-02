"""The messaging API.

Every endpoint here is reached with a portal session token and is gated on
the `chat` app key, so who may use it is the same Admin Console decision as
every other tool.

The rule this file exists to keep: a message is served to the people in its
conversation and to nobody else. That is enforced by looking conversations
up THROUGH the caller's memberships rather than by fetching one and then
asking whether they belong -- the second shape is one forgotten `if` away
from handing somebody another pair's thread.
"""
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework.response import Response

from accounts.auth import PortalScopedAPIView, require_app
from accounts.models import PortalUser

from .models import Conversation, Membership, Message

APP = 'chat'
MAX_BODY = 4000
PAGE = 50


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


def serialize_message(m, viewer):
    return {
        'id': m.id,
        'body': '' if m.is_deleted else m.body,
        'deleted': m.is_deleted,
        'edited': m.edited_at is not None,
        'sent_at': m.created_at.isoformat(),
        'sender_id': m.sender_id,
        'mine': m.sender_id == viewer.id,
        'sender_name': m.sender.name if m.sender else 'Someone who has left',
    }


def _unread_for(me):
    """How much of this thread the member has not seen."""
    unread = me.conversation.messages.exclude(sender_id=me.user_id)
    if me.last_read_at:
        unread = unread.filter(created_at__gt=me.last_read_at)
    return unread.count()


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
            conv = me.conversation
            # A thread cleared from this person's list stays gone until the
            # other person says something new.
            if me.cleared_at and conv.last_message_at <= me.cleared_at:
                continue
            other = conv.other_member(user)
            last = conv.messages.order_by('-id').first()
            out.append({
                'id': conv.id,
                'person': _person(other) if other else None,
                'last_message': serialize_message(last, user) if last else None,
                'unread': _unread_for(me),
                'last_message_at': conv.last_message_at.isoformat(),
            })
        return Response({'conversations': out, 'total_unread': sum(c['unread'] for c in out)})

    def post(self, request):
        """Open the thread with one person, making it if it is their first.

        Idempotent on purpose: tapping a colleague twice must land in the
        same conversation, not make a second one with half the history.
        """
        user, err = require_app(request, APP)
        if err:
            return err
        code = str(request.data.get('employee_code') or '').strip()
        other = PortalUser.objects.filter(employee_code=code, is_active=True).first() if code else None
        if not other:
            return Response({'error': 'No such employee.'}, status=404)
        if other.id == user.id:
            return Response({'error': 'You cannot start a conversation with yourself.'}, status=400)
        if not (other.is_superadmin or other.can_open(APP)):
            return Response({'error': f'{other.name} has not been given access to Messages yet. '
                                      'Ask an administrator to grant it.'}, status=400)

        existing = (Conversation.objects
                    .filter(members__user_id=user.id)
                    .filter(members__user_id=other.id)
                    .first())
        if existing:
            return Response({'id': existing.id, 'person': _person(other), 'created': False})
        with transaction.atomic():
            conv = Conversation.objects.create()
            Membership.objects.create(conversation=conv, user=user)
            Membership.objects.create(conversation=conv, user=other)
        return Response({'id': conv.id, 'person': _person(other), 'created': True}, status=201)


class MessagesView(PortalScopedAPIView):
    """Read and write one thread."""

    def get(self, request, conversation_id):
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me:
            # 404 rather than 403: whether a conversation exists between two
            # other people is itself none of the caller's business.
            return Response({'error': 'No such conversation.'}, status=404)

        qs = me.conversation.messages.select_related('sender')
        after = request.query_params.get('after')
        if after:
            # The polling path: only what has arrived since.
            try:
                since = int(after)
            except (TypeError, ValueError):
                return Response({'error': 'after must be a message id.'}, status=400)
            rows = list(qs.filter(id__gt=since).order_by('id')[:200])
        else:
            # The opening path: the newest page, put back in reading order.
            rows = list(qs.order_by('-id')[:PAGE])[::-1]

        other = me.conversation.other_member(user)
        return Response({
            'conversation_id': me.conversation.id,
            'person': _person(other) if other else None,
            'messages': [serialize_message(m, user) for m in rows],
            'has_more': not after and me.conversation.messages.count() > len(rows),
        })

    def post(self, request, conversation_id):
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me:
            return Response({'error': 'No such conversation.'}, status=404)
        body = str(request.data.get('body') or '').strip()
        if not body:
            return Response({'error': 'Type something first.'}, status=400)
        if len(body) > MAX_BODY:
            return Response({'error': f'That message is too long (limit {MAX_BODY} characters).'}, status=400)

        with transaction.atomic():
            m = Message.objects.create(conversation=me.conversation, sender=user, body=body)
            now = timezone.now()
            Conversation.objects.filter(id=me.conversation_id).update(last_message_at=now)
            # Sending is reading: the sender's own message must never come
            # back to them as unread, and a thread they had cleared comes
            # back the moment they use it again.
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
        # The row stays so the thread still reads sensibly around it.
        m.body, m.deleted_at = '', timezone.now()
        m.save(update_fields=['body', 'deleted_at'])
        return Response({'message': serialize_message(m, user)})

    @staticmethod
    def _own(user, conversation_id, message_id):
        """Only your own message, and only in a thread you are in.

        Both halves matter: without the sender check anybody in the thread
        could rewrite what the other person said, which is worse than
        reading it.
        """
        me = _membership(user, conversation_id)
        if not me:
            return None
        return me.conversation.messages.filter(id=message_id, sender_id=user.id,
                                               deleted_at__isnull=True).first()


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
    """Hide a thread from your own list. The other person keeps theirs."""

    def post(self, request, conversation_id):
        user, err = require_app(request, APP)
        if err:
            return err
        me = _membership(user, conversation_id)
        if not me:
            return Response({'error': 'No such conversation.'}, status=404)
        Membership.objects.filter(id=me.id).update(cleared_at=timezone.now())
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
        rows = [_person(p) for p in people.order_by('name')[:500]
                if p.is_superadmin or p.can_open(APP)]
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
