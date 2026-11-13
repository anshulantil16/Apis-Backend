"""Messages between people at APIS.

The date headings the screen shows ("Today", "Yesterday", "12 October") are
drawn from created_at rather than stored, because a stored day label is
wrong for anyone reading it in another timezone and wrong again the morning
after it was written.

On privacy. This holds conversations people expect to be private, and three
decisions follow from that -- decisions rather than oversights:

  * There is no administrator view of other people's messages. A superadmin
    can grant and revoke who may open the tool; they cannot read it. Nothing
    in this app serves a message to anyone who is not in the conversation,
    and the queries that fetch messages filter on membership rather than
    checking it afterwards.
  * Deleting a message blanks its body and keeps the row, so the thread
    still reads sensibly ("This message was deleted") instead of silently
    losing a reply somebody answered.
  * There is no retention rule yet, which means messages and their
    attachments are kept forever. That is a decision for the company, not
    for this file, and it is written down here so it is chosen rather than
    defaulted into.
"""
import os
import uuid

from django.db import models
from django.utils import timezone

from accounts.models import PortalUser


class Conversation(models.Model):
    """A thread between two people, or a named group.

    One table for both: a direct conversation is a group of two with no
    name, and every rule about who may read it is the same either way.
    Splitting them would mean two of every membership check, which is two
    places to forget one.
    """
    is_group = models.BooleanField(default=False)
    title = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey(PortalUser, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='chats_started')
    created_at = models.DateTimeField(auto_now_add=True)
    # Touched on every send, so the conversation list can be ordered by
    # "most recent" without a join and a max() across every message.
    last_message_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-last_message_at']

    def other_member(self, user):
        """The person on the other side of a direct conversation."""
        m = self.members.exclude(user_id=user.id).select_related('user').first()
        return m.user if m else None

    def name_for(self, user):
        """What this thread is called on that person's screen.

        A group shows its name; a direct conversation shows whoever else is
        in it, because "Conversation 41" tells nobody anything.
        """
        if self.is_group:
            return self.title or 'Group'
        other = self.other_member(user)
        return other.name if other else 'Unknown'


class Membership(models.Model):
    """One person's place in a conversation, and how far they have read."""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(PortalUser, on_delete=models.CASCADE, related_name='chat_memberships')
    # Everything after this moment is unread. A timestamp rather than a count
    # so that reading on a phone and then on a laptop agree with each other.
    last_read_at = models.DateTimeField(null=True, blank=True)
    # Hidden from the list until the next message arrives. Not a delete: the
    # history belongs to everyone in it, and one person clearing their list
    # must not take anybody else's thread with it.
    cleared_at = models.DateTimeField(null=True, blank=True)
    # default= rather than auto_now_add=: the field is being added to a
    # table that already has rows, and auto_now_add has no answer for
    # what to put in them.
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [('conversation', 'user')]
        indexes = [models.Index(fields=['user', 'conversation'])]


class Message(models.Model):
    TEXT, SYSTEM = 'text', 'system'
    KIND_CHOICES = [(TEXT, 'Message'), (SYSTEM, 'What happened in the group')]

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(PortalUser, on_delete=models.SET_NULL, null=True, related_name='chat_messages')
    body = models.TextField(blank=True)
    # "Asha added Bhavesh", "Chandra left" -- written by the server, shown
    # in the middle of the thread, and never editable by anyone.
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=TEXT)
    # What this message is answering. SET_NULL rather than CASCADE: deleting
    # the original must not take every reply to it with it.
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                                 related_name='replies')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['conversation', 'id'])]

    @property
    def is_deleted(self):
        return self.deleted_at is not None


def attachment_path(instance, filename):
    """Stored under a generated name, keeping only the extension.

    Uploaded names are whatever the sender's machine had, routinely with
    spaces, brackets and occasionally a path in them. The name people see is
    kept in the `name` column; what lands on disk is a uuid, which settles
    both path traversal and two people sending 'scan.pdf' on the same day.
    """
    ext = os.path.splitext(filename or '')[1].lower()[:10]
    return f'chat/{uuid.uuid4().hex}{ext}'


class Attachment(models.Model):
    """A photo or file sent in a message.

    Never served from /media/. The raw path is blocked in config/urls.py and
    these are read through chat.views.AttachmentView, which checks that the
    caller is in the conversation first -- a file on an intranet share is
    still a file anyone with the link can take, and these are salary
    letters, ID scans and whatever else colleagues send each other.
    """
    IMAGE, FILE = 'image', 'file'

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to=attachment_path)
    # The name the sender saw. Shown on screen and used for the download.
    name = models.CharField(max_length=300)
    size = models.PositiveIntegerField(default=0)
    content_type = models.CharField(max_length=120, blank=True)
    kind = models.CharField(max_length=10, default=FILE)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_image(self):
        return self.kind == self.IMAGE
