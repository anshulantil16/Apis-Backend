"""Seed the report recipients from the addresses the morning mail already uses.

Run once. It adds nobody who is already on the list and sends nothing.

The addresses are the ones on the circulated mail. Matching them to GTR lines
is the part that cannot be guessed, so only the ones the business's own mail
makes unambiguous are attached to a territory -- a name on the To line beside
a region in the sheet. Everyone else is added as a manager with no coverage
yet, which is visible on the Data tab and harmless: a manager covering no
region receives nothing until somebody says what they cover.

Guessing a reporting line from a spreadsheet is how a person receives a
territory that is not theirs, and that is worse than an empty row.
"""
from django.core.management.base import BaseCommand

from sales.models import ReportRecipient, ReviewSnapshot


# Matched by name to the GTR HEAD column on the sheet.
HEADS = [
    ('GTR02', 'Ramesh Singh',          'ramesh.singh@apisindia.com'),
    ('GTR06 B', 'Vinod Shah',          'Shah.vinod@apisindia.com'),
    ('GTR09', 'Vasanthkumar D',        'vasanthkumar.d@apisindia.com'),
]

# On the mail, but not resolvable to a GTR line from the mail alone. Added so
# the addresses are in the system and visible, with no territory attached.
UNASSIGNED = [
    ('K Vinay', 'vinay@apisindia.com'),
    ('APIS RSM', 'RSM@apisindia.com'),
    ('APIS ASM GT', 'asm@apisindia.com'),
    ('Vaibhav Mishra', 'vaibhav.mishra@apisindia.com'),
    ('Shri Prakash Chaubey', 'shriprakash@apisindia.com'),
    ('Heera Polandeappnn', 'heera@apisindia.com'),
    ('Pradeep Krishali', 'pradeep@apisindia.com'),
    ('Sunetro Banarjee', 'sunetro@apisindia.com'),
    ('Gaurav Dabral', 'gaurav@apisindia.com'),
    ('Naagesh Mishra', 'naagesh@apisindia.com'),
    ('Tapan Kumar Behera', 'tapan@apisindia.com'),
    ('Sanjeev Lamba', 'sanjeev@apisindia.com'),
    ('Kundan Singh', 'kundan@apisindia.com'),
    ('Madan Amit', 'madan@apisindia.com'),
    ('Krishna Kumar', 'krishna.kumar@apisindia.com'),
    ('Rajesh Sharma', 'rajesh@apisindia.com'),
    ('Pooja Arora', 'pooja.arora@apisindia.com'),
    ('Vishal Mahaur', 'mahaur.vishal@apisindia.com'),
    ('Hemant Tripathi', 'hemant.tripathi@apisindia.com'),
    ('Gouri Shankar', 'gouri.shankar@apisindia.com'),
    ('Deepak Kumar Mishra', 'mishra.deepak@apisindia.com'),
    ('Pankaj Tripathi', 'pankaj.tripathi@apisindia.com'),
    ('Sujat Alam', 'sujat@apisindia.com'),
    ('Hari Om Solanki', 'hariom@apisindia.com'),
    ('Rajeev Saxena', 'rajeev.saxena@apisindia.com'),
    # The three on Cc. Flagged as such rather than silently folded in with
    # the rest, because who was on Cc is a decision somebody made.
    ('Amit Anand', 'amit@apisindia.com'),
    ('Arun Kumar Mishra', 'ARUN.MISHRA@apisindia.com'),
    ('R Manigandan', 'r.manigandan@apisindia.com'),
]
CC = {'amit@apisindia.com', 'arun.mishra@apisindia.com',
      'r.manigandan@apisindia.com'}


class Command(BaseCommand):
    help = 'Add the morning mail\'s addresses to the report recipient list.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **opts):
        snap = ReviewSnapshot.objects.first()
        rows = [r for r in snap.rows.all() if not r.is_total] if snap else []
        by_name = {(r.head_name or '').strip().lower(): r for r in rows}

        added = skipped = 0
        for region, name, email in HEADS:
            row = by_name.get(name.lower())
            if not row:
                self.stdout.write(f'  no row for {name} on the latest sheet '
                                  f'-- added without a territory')
            key = (row.head_code or row.region) if row else region
            if opts['dry_run']:
                self.stdout.write(f'  head    {key:10} {name:24} {email}')
                continue
            _, made = ReportRecipient.objects.get_or_create(
                email=email.lower(), role=ReportRecipient.ROLE_HEAD,
                head_key=key, defaults={'name': name})
            added, skipped = added + made, skipped + (not made)

        for name, email in UNASSIGNED:
            note = 'on Cc of the circulated mail' if email.lower() in CC else \
                   'on the circulated mail; territory not yet set'
            if opts['dry_run']:
                self.stdout.write(f'  manager {"-":10} {name:24} {email}')
                continue
            _, made = ReportRecipient.objects.get_or_create(
                email=email.lower(), role=ReportRecipient.ROLE_MANAGER,
                head_key='', defaults={'name': name, 'regions': [],
                                       'covers_all': False, 'note': note})
            added, skipped = added + made, skipped + (not made)

        if opts['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run -- nothing written.'))
            return
        self.stdout.write(self.style.SUCCESS(
            f'{added} added, {skipped} already there. Nothing was sent.'))
        self.stdout.write(
            'Managers receive nothing until their regions are set on the '
            'Data tab -- an empty coverage list means no territories, never '
            'all of them.')
