"""What Pocket HRMS actually calls each column, for THIS tenant.

Writes nothing and changes nothing: it asks for a couple of rows with no
EmployeeFields header, which is what their support said to do, and prints
back the column names the tenant answers with.

It exists because guessing column names has cost this project months. GRADE
was read as GradeString and came through empty; PocketReportingManager was
written off as "not available on this tenant" when it was the endpoint that
was wrong. Anything else we want -- cadre, the reporting HOD -- gets mapped
from this list rather than from a guess.

    ./venv/bin/python manage.py hrms_columns
    ./venv/bin/python manage.py hrms_columns --like cadre
    ./venv/bin/python manage.py hrms_columns --values     # one sample row

Asking with no header blanks almost every VALUE, so --values is for seeing
which columns carry anything at all on a normal fetch, not for reading data.
"""
from django.core.management.base import BaseCommand

from accounts.services import hrms


class Command(BaseCommand):
    help = "Print the employee-master column names Pocket HRMS has for this tenant."

    def add_arguments(self, parser):
        parser.add_argument('--like', default='',
                            help='only columns whose name contains this (case-insensitive)')
        parser.add_argument('--values', action='store_true',
                            help='also fetch one row with the columns we normally ask for, '
                                 'and show which of them came back with anything in it')

    def handle(self, *args, **options):
        if not hrms.is_configured():
            self.stderr.write('No Pocket HRMS token configured on this server.')
            return

        columns, _ = hrms.discover_fields(sample_size=2)
        needle = (options['like'] or '').lower()
        shown = [c for c in columns if needle in c.lower()] if needle else columns

        self.stdout.write(f'{len(columns)} columns on this tenant'
                          + (f', {len(shown)} matching "{options["like"]}"' if needle else '')
                          + ':')
        for c in shown:
            asked = ' (already asked for)' if c.lower() in {f.lower() for f in hrms.EMPLOYEE_FIELDS} else ''
            self.stdout.write(f'  {c}{asked}')

        if not options['values']:
            return

        rows = hrms.fetch_page(take=1, offset=hrms.FIRST_OFFSET,
                               emp_status=hrms.ACTIVE_ONLY)
        if not rows:
            self.stdout.write('\nNo rows came back for a values check.')
            return
        row = rows[0]
        filled = [k for k, v in row.items() if v not in (None, '', 'NULL')]
        empty = [k for k in row if k not in filled]
        self.stdout.write(f'\nOn one live employee, {len(filled)} of the columns we ask '
                          f'for carry a value:')
        for k in sorted(filled):
            self.stdout.write(f'  {k}')
        if empty:
            self.stdout.write(f'\nEmpty on that row ({len(empty)}) — may be empty for '
                              f'everyone, or just for this person:')
            for k in sorted(empty):
                self.stdout.write(f'  {k}')
