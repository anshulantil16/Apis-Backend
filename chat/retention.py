"""Getting space back.

Two different questions get muddled together, so they are two functions:

  * A thread nobody can see any more. Once every member has removed it from
    their list and nothing new has been said, the rows are serving nobody --
    not the people in it, not an administrator (there is no admin view of
    messages), not a report. Those can go entirely.
  * A thread that is simply old. That is a retention rule, and the number of
    days in it is the company's decision, not this file's. Nothing calls it
    on a schedule until somebody picks the number.

Both delete the files from disk as well as the rows. A row removed from the
database while its photo stays on the disk is not deleted in any sense
somebody asking for a delete would recognise, and the disk is where nearly
all of the space actually is -- a year of text from everybody at APIS is
about a gigabyte; a year of the photos people attach to it is not.
"""
from datetime import timedelta

from django.db.models import Count, F, Min, Q
from django.utils import timezone

from .models import Attachment, Conversation, Message

# How long an abandoned thread is left alone before it is destroyed. Clearing
# a thread is one click and people do it by accident; a month is long enough
# for somebody to say "where did that go" while it can still be answered.
GRACE_DAYS = 30


def _drop_files(message_qs):
    """Delete the stored files for these messages, then their rows.

    One at a time rather than a bulk delete: FileField.delete is the only
    thing that touches the disk, and a queryset delete skips it, which is how
    a database shrinks while the disk does not.
    """
    n = 0
    for a in Attachment.objects.filter(message__in=message_qs).iterator():
        a.file.delete(save=False)
        a.delete()
        n += 1
    return n


def abandoned(grace_days=GRACE_DAYS, now=None):
    """Conversations every member has removed, untouched since.

    One query rather than a loop over conversations. The test has three
    parts and all three matter: as many cleared memberships as there are
    memberships, nothing said since the earliest of those clears, and that
    clear long enough ago to be past the grace period.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(days=grace_days)
    return (Conversation.objects
            .annotate(people=Count('members', distinct=True),
                      gone=Count('members', distinct=True,
                                 filter=Q(members__cleared_at__isnull=False)),
                      first_cleared=Min('members__cleared_at'))
            .filter(people__gt=0, gone=F('people'),
                    last_message_at__lte=F('first_cleared'),
                    first_cleared__lte=cutoff))


def purge_abandoned(grace_days=GRACE_DAYS, dry_run=False):
    """-> dict of what went, or what would go."""
    rows = list(abandoned(grace_days).values_list('id', flat=True))
    if not rows:
        return {'conversations': 0, 'messages': 0, 'attachments': 0}
    msgs = Message.objects.filter(conversation_id__in=rows)
    out = {'conversations': len(rows), 'messages': msgs.count(),
           'attachments': Attachment.objects.filter(message__in=msgs).count()}
    if dry_run:
        return out
    _drop_files(msgs)
    # Messages and memberships cascade from the conversation.
    Conversation.objects.filter(id__in=rows).delete()
    return out


def purge_older_than(days, dry_run=False):
    """Destroy every message older than `days`, in every conversation.

    Deliberately not called from anywhere. It exists so that when the
    company does set a retention period there is one tested way to apply it,
    rather than somebody writing a delete in a shell at the time.
    """
    if days < 1:
        raise ValueError('A retention period of less than a day would delete the conversation people are having.')
    cutoff = timezone.now() - timedelta(days=days)
    msgs = Message.objects.filter(created_at__lt=cutoff)
    out = {'messages': msgs.count(),
           'attachments': Attachment.objects.filter(message__in=msgs).count()}
    if dry_run:
        return out
    _drop_files(msgs)
    # Reply pointers into the deleted range are SET_NULL, so a surviving
    # reply loses its quote rather than being deleted with the original.
    Message.objects.filter(id__in=list(msgs.values_list('id', flat=True))).delete()
    return out
