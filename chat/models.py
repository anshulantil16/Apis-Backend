"""Messages between two people at APIS.

Deliberately plain: a conversation, the people in it, and messages with a
timestamp. The date headings the screen shows ("Today", "Yesterday",
"12 October") are drawn from created_at rather than stored, because a stored
day label is wrong for anyone reading it in another timezone and wrong again
the morning after it was written.

On privacy. This is internal messaging between colleagues, not a public
board, and it is the first thing here that holds conversations people expect
to be private. Three decisions follow from that, and they are decisions
rather than oversights:

  * There is no administrator view of other people's messages. A superadmin
    can grant and revoke who may open the tool; they cannot read it. Nothing
    in this app serves a message to anyone who is not in the conversation,
    and the one query that fetches messages filters on membership rather
    than checking it afterwards.
  * Deleting a message blanks its body and keeps the row, so the thread
    still reads sensibly ("This message was deleted") instead of silently
    losing a reply somebody answered.
  * There is no retention rule yet, which means messages are kept forever.
    That is a decision for the company, not for this file, and it is written
    down here so it is chosen rather than defaulted into.
"""
from django.db import models
from django.utils import timezone

from accounts.models import PortalUser


class Conversation(models.Model):
    """A thread between exactly two people.

    Group chats are not modelled yet, but the shape here does not rule them
    out: the members are rows rather than two columns on this table, so a
    third person is a row, not a migration of every message.
    """
    created_at = models.DateTimeField(auto_now_add=True)
    # Touched on every send, so the conversation list can be ordered by
    # "most recent" without a join and a max() across every message.
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-last_message_at']

    def other_member(self, user):
        m = self.members.exclude(user_id=user.id).select_related('user').first()
        return m.user if m else None


class Membership(models.Model):
    """One person's place in a conversation, and how far they have read."""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name='chat_memberships')
    # Everything after this moment is unread. A timestamp rather than a count
    # so that reading on a phone and then on a laptop agree with each other.
    last_read_at = models.DateTimeField(null=True, blank=True)
    # Hidden from the list until the next message arrives. Not a delete: the
    # history belongs to both people and one of them clearing their list
    # must not take the other's thread with it.
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('conversation', 'user')]
        indexes = [models.Index(fields=['user', 'conversation'])]


class Message(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(PortalUser, on_delete=models.SET_NULL, null=True, related_name='chat_messages')
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['conversation', 'id'])]

    @property
    def is_deleted(self):
        return self.deleted_at is not None
