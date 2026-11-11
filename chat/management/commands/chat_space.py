"""What Messages is using, and getting some of it back.

Reporting is the default and deleting has to be asked for, because this is
the one command in the project that destroys things people wrote to each
other and cannot be undone from a backup older than last night.
"""
from django.core.management.base import BaseCommand
from django.db.models import Sum

from chat import retention
from chat.models import Attachment, Conversation, Message


def _mb(n):
    return f'{(n or 0) / (1024 * 1024):.1f} MB'


class Command(BaseCommand):
    help = 'Report what Messages is using on disk, and optionally purge.'

    def add_arguments(self, p):
        p.add_argument('--purge-abandoned', action='store_true',
                       help='Destroy threads every member removed over %s days ago.'
                            % retention.GRACE_DAYS)
        p.add_argument('--older-than', type=int, metavar='DAYS',
                       help='Destroy every message older than DAYS. A retention rule -- '
                            'the company picks the number, not this flag.')
        p.add_argument('--grace-days', type=int, default=retention.GRACE_DAYS)
        p.add_argument('--dry-run', action='store_true',
                       help='Say what would go without touching anything.')

    def handle(self, *a, **o):
        bytes_held = Attachment.objects.aggregate(n=Sum('size'))['n']
        self.stdout.write(
            f'{Conversation.objects.count()} conversations, '
            f'{Message.objects.count()} messages, '
            f'{Attachment.objects.count()} files holding {_mb(bytes_held)} on disk.')

        did = False
        if o['purge_abandoned']:
            did = True
            r = retention.purge_abandoned(o['grace_days'], dry_run=o['dry_run'])
            verb = 'would remove' if o['dry_run'] else 'removed'
            self.stdout.write(
                f"Abandoned threads: {verb} {r['conversations']} conversations, "
                f"{r['messages']} messages, {r['attachments']} files.")

        if o['older_than']:
            did = True
            r = retention.purge_older_than(o['older_than'], dry_run=o['dry_run'])
            verb = 'would remove' if o['dry_run'] else 'removed'
            self.stdout.write(
                f"Older than {o['older_than']} days: {verb} {r['messages']} messages, "
                f"{r['attachments']} files.")

        if not did:
            self.stdout.write('Nothing deleted. Pass --purge-abandoned or --older-than DAYS.')
