"""Who is in the directory but cannot sign in.

These people are not a data error. They work here, they are in headcount,
they appear on org charts and reporting lines -- Pocket HRMS simply holds
no address for them. The intranet signs people in by emailing a one-time
code, so an address is the one field without which nobody can get in.

The output is a CSV because the people who can fix this are P&C, and what
they need is a list they can split by department and chase. It carries the
reporting manager and the HOD for exactly that reason: an employee code on
its own tells an HR executive nothing about who to ask.
"""
import csv

from django.core.management.base import BaseCommand

from accounts.models import PortalUser

COLUMNS = [
    ('employee_code',          'Employee Code'),
    ('name',                   'Name'),
    ('designation',            'Designation'),
    ('department',             'Department'),
    ('location',               'Location'),
    ('office_mobile',          'Office Mobile'),
    ('reporting_manager_name', 'Reporting Manager'),
    ('reporting_manager_code', 'Manager Code'),
    ('hod_name',               'HOD'),
    ('hod_code',               'HOD Code'),
]


class Command(BaseCommand):
    help = 'List the people in the directory who have no email address.'

    def add_arguments(self, p):
        p.add_argument('--out', metavar='FILE',
                       help='Write the CSV here instead of to the screen.')
        p.add_argument('--all', action='store_true',
                       help='Include people marked inactive. Off by default: '
                            'somebody who has left needs no sign-in.')
        p.add_argument('--count', action='store_true',
                       help='Just the number, and the split by department.')

    def handle(self, *a, **o):
        rows = PortalUser.objects.filter(email__isnull=True)
        if not o['all']:
            rows = rows.filter(is_active=True)
        # By department then name: the list gets handed over one department
        # at a time, not read end to end.
        rows = rows.order_by('department', 'name')

        total = rows.count()
        if not total:
            self.stdout.write('Everybody in the directory has an email address.')
            return

        if o['count']:
            self.stdout.write(f'{total} people have no email address and cannot sign in.\n')
            by_dept = {}
            for r in rows:
                by_dept[r.department or '(no department)'] = \
                    by_dept.get(r.department or '(no department)', 0) + 1
            for dept, n in sorted(by_dept.items(), key=lambda kv: (-kv[1], kv[0])):
                self.stdout.write(f'{n:4}  {dept}')
            return

        # self.stdout, not sys.stdout: the command's own stream is the one
        # that can be redirected, which is how this gets tested at all.
        handle = open(o['out'], 'w', newline='', encoding='utf-8-sig') if o['out'] else self.stdout
        try:
            # utf-8-sig above, so Excel opens Indian names correctly instead
            # of showing them as mojibake -- this file is for P&C, and a file
            # they cannot read is a file they will not act on.
            w = csv.writer(handle, lineterminator='\n')
            w.writerow([label for _, label in COLUMNS])
            for r in rows:
                w.writerow([getattr(r, field, '') or '' for field, _ in COLUMNS])
        finally:
            if o['out']:
                handle.close()

        if o['out']:
            self.stdout.write(f'{total} people written to {o["out"]}')
