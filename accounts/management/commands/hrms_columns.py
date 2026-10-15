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
    ./venv/bin/python manage.py hrms_columns --probe CADRE,HOD,Band

The listing comes from the OLD endpoint, which still answers a call with no
EmployeeFields header; the filtered one refuses that with a 400. Asking with
no header also blanks almost every VALUE, so the listing is for names.

--probe is the other half: it asks the FILTERED endpoint -- the one the sync
actually uses -- for the names given, and says which came back and which
carried anything. An unrecognised name is dropped silently with a 200, so
"absent" and "not a real column" look identical and this is the only way to
tell them apart. With no list it tries the names we might want next: the
cadre, the reporting HOD, and the rest of the master data.
"""
from django.core.management.base import BaseCommand

from accounts.services import hrms


# Names worth trying on the filtered endpoint. Spellings included in both
# cases on purpose: GRADE answers and Grade does not, which is what hid the
# column for months.
CANDIDATES = [
    'CADRE', 'Cadre', 'CadreString',
    'HOD', 'HODName', 'ReportingHOD', 'POCKETREPORTINGHOD', 'PocketReportingHOD',
    'Band', 'BAND', 'Level', 'LEVEL',
    'Category', 'CategoryString', 'CATEGORY',
    'Division', 'DivisionString', 'Zone', 'SubZone', 'Branch', 'BranchString',
    'CostCenter', 'CostCentre', 'State', 'StateString',
    'Confirmed', 'ConfirmationDate', 'ProbationEndDate',
    'Gender', 'MaritalStatus', 'Qualification', 'BloodGroup',
    'DateOfLeaving', 'EmployeeType', 'EmploymentType',
]


class Command(BaseCommand):
    help = "Print the employee-master column names Pocket HRMS has for this tenant."

    def add_arguments(self, parser):
        parser.add_argument('--like', default='',
                            help='only columns whose name contains this (case-insensitive)')
        parser.add_argument('--probe', nargs='?', const='', default=None,
                            help='ask the filtered endpoint for these field names '
                                 '(comma-separated), or a built-in list of candidates')
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

        probe = options['probe']
        if probe is not None:
            names = [n.strip() for n in probe.split(',') if n.strip()] or CANDIDATES
            self.stdout.write('')
            self.stdout.write(f'Asking the filtered endpoint for {len(names)} names...')
            rows = hrms.fetch_page(take=25, offset=hrms.FIRST_OFFSET,
                                   emp_status=hrms.ACTIVE_ONLY, fields=names)
            if not rows:
                self.stdout.write('  nothing came back.')
                return
            answered = {k for r in rows if isinstance(r, dict) for k in r}
            carrying = {k for r in rows if isinstance(r, dict)
                        for k, v in r.items() if v not in (None, '', 'NULL')}

            self.stdout.write('')
            self.stdout.write(f'  came back WITH values ({len(carrying)}) '
                              f'-- these are worth storing:')
            for k in sorted(carrying):
                self.stdout.write(f'    {k}')

            empty = answered - carrying
            if empty:
                self.stdout.write('')
                self.stdout.write(f'  came back EMPTY on all {len(rows)} sampled '
                                  f'employees ({len(empty)}) -- a real column that '
                                  f'nobody has filled in:')
                for k in sorted(empty):
                    self.stdout.write(f'    {k}')

            missing = [n for n in names if n not in answered]
            if missing:
                self.stdout.write('')
                self.stdout.write(f'  not answered at all ({len(missing)}) -- not a '
                                  f'column on this tenant, under that spelling:')
                self.stdout.write('    ' + ', '.join(missing))
            return

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
