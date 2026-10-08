"""The recipient list, as a workbook people can actually fill in.

It was a CSV: one column doing two different jobs depending on the row's
role, four bare headers, and the instructions as comment lines that Excel
shows as text spilling across empty cells. Correct, and unreadable at a
glance.

What changed, and why each one:

  * **one column, one job.** `key_or_regions` held an APIS ID on a head row
    and a semicolon-separated region list on a manager row. Columns that mean
    two things are how a manager ends up with an APIS ID in their coverage.
    There are now separate columns and the one that does not apply is greyed.
  * **the region is shown.** The key is SL04492, which identifies the person
    exactly and tells a reader nothing. GTR01 beside it is what anybody here
    recognises, and it is not editable -- it is there to be read.
  * **the instructions are a second tab**, not rows above the table.
  * **what to type is the only thing left white.** Everything generated from
    the sheet is shaded, so the empty white cells are the work.
"""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

INK = '1F2933'
HEAD_BG = '1F6FB2'
FIXED_BG = 'EEF2F7'      # generated from the sheet -- read, do not edit
TYPE_BG = 'FFFFFF'       # yours to fill in
SECTION_BG = 'DCE6F1'
NOTE = '6B7489'

THIN = Side(style='thin', color='C9D2DD')
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

COLUMNS = [
    ('role', 10, 'head or manager'),
    ('key', 14, 'the head this report is built for'),
    ('region', 13, 'which territory that is'),
    ('name', 28, 'who they are'),
    ('email', 34, 'WHERE TO SEND IT'),
    ('regions_covered', 30, 'managers only'),
]
BLANK_MANAGER_ROWS = 8


def _cell(ws, r, c, value='', *, bg=TYPE_BG, bold=False, colour=INK,
          align='left', size=11):
    cell = ws.cell(row=r, column=c, value=value)
    cell.font = Font(name='Calibri', size=size, bold=bold, color=colour)
    cell.fill = PatternFill('solid', fgColor=bg)
    cell.alignment = Alignment(horizontal=align, vertical='center',
                               wrap_text=False)
    cell.border = BOX
    return cell


def build(heads, managers):
    """-> a Workbook. `heads` are ReviewRow, `managers` ReportRecipient."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'Recipients'
    ws.sheet_view.showGridLines = False

    # -- title ------------------------------------------------------------
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=6)
    t = ws.cell(row=1, column=1, value='Who gets which report')
    t.font = Font(name='Calibri', size=15, bold=True, color=INK)
    t.alignment = Alignment(vertical='center')
    ws.row_dimensions[1].height = 26

    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=6)
    s = ws.cell(row=2, column=1,
                value='Fill in the white cells only. The shaded ones come off '
                      'the sales sheet — read them, do not change them. '
                      'See "How to fill this in".')
    s.font = Font(name='Calibri', size=10, color=NOTE)
    ws.row_dimensions[2].height = 18

    # -- header -----------------------------------------------------------
    HDR = 4
    for i, (name, width, hint) in enumerate(COLUMNS, start=1):
        c = _cell(ws, HDR, i, name, bg=HEAD_BG, bold=True, colour='FFFFFF',
                  align='center')
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[get_column_letter(i)].width = width
        _cell(ws, HDR + 1, i, hint, bg=FIXED_BG, colour=NOTE, size=9,
              align='center')
    ws.freeze_panes = ws.cell(row=HDR + 2, column=1)

    r = HDR + 2

    # -- the heads --------------------------------------------------------
    _cell(ws, r, 1, 'HEADS — each gets their own territory only',
          bg=SECTION_BG, bold=True, size=10)
    for c in range(2, 7):
        _cell(ws, r, c, '', bg=SECTION_BG)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    r += 1

    first_email_row = r
    for h in heads:
        rec = managers  # placeholder so the signature stays obvious
        _cell(ws, r, 1, 'head', bg=FIXED_BG)
        _cell(ws, r, 2, h.head_code or h.region, bg=FIXED_BG)
        _cell(ws, r, 3, h.region or '—', bg=FIXED_BG)
        _cell(ws, r, 4, h.head_name, bg=FIXED_BG)
        _cell(ws, r, 5, h.email_prefill, bg=TYPE_BG)          # yours
        # Not applicable on a head row, and greyed rather than left white so
        # it does not read as something waiting to be filled in.
        _cell(ws, r, 6, '', bg=FIXED_BG)
        r += 1
    last_email_row = r - 1

    # -- the managers -----------------------------------------------------
    r += 1
    _cell(ws, r, 1, 'MANAGERS — one report across the territories they cover, '
                    'plus each of those heads\' own files',
          bg=SECTION_BG, bold=True, size=10)
    for c in range(2, 7):
        _cell(ws, r, c, '', bg=SECTION_BG)
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    r += 1

    mgr_from = r
    for m in managers:
        _cell(ws, r, 1, 'manager', bg=FIXED_BG)
        _cell(ws, r, 2, '', bg=FIXED_BG)
        _cell(ws, r, 3, '', bg=FIXED_BG)
        _cell(ws, r, 4, m.name, bg=TYPE_BG)
        _cell(ws, r, 5, m.email, bg=TYPE_BG)
        _cell(ws, r, 6, 'ALL' if m.covers_all else ';'.join(m.regions or []),
              bg=TYPE_BG)
        r += 1
    for _ in range(BLANK_MANAGER_ROWS):
        _cell(ws, r, 1, 'manager', bg=FIXED_BG)
        _cell(ws, r, 2, '', bg=FIXED_BG)
        _cell(ws, r, 3, '', bg=FIXED_BG)
        _cell(ws, r, 4, '', bg=TYPE_BG)
        _cell(ws, r, 5, '', bg=TYPE_BG)
        _cell(ws, r, 6, '', bg=TYPE_BG)
        r += 1
    mgr_to = r - 1

    # Excel will not let a role be typed wrongly, which is the one field the
    # import cannot guess its way around.
    dv = DataValidation(type='list', formula1='"head,manager"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f'A{first_email_row}:A{mgr_to}')

    # -- the instructions, on their own tab -------------------------------
    doc = wb.create_sheet('How to fill this in')
    doc.sheet_view.showGridLines = False
    doc.column_dimensions['A'].width = 18
    doc.column_dimensions['B'].width = 96

    def line(row, left, right, bold=False, size=11, colour=INK):
        a = doc.cell(row=row, column=1, value=left)
        a.font = Font(name='Calibri', size=size, bold=True, color=INK)
        a.alignment = Alignment(vertical='top')
        b = doc.cell(row=row, column=2, value=right)
        b.font = Font(name='Calibri', size=size, bold=bold, color=colour)
        b.alignment = Alignment(vertical='top', wrap_text=True)

    t = doc.cell(row=1, column=1, value='How to fill this in')
    t.font = Font(name='Calibri', size=15, bold=True, color=INK)

    rows = [
        ('', ''),
        ('The job', 'Put an email address against every person who should '
                    'receive a report each morning. Nothing is guessed: a row '
                    'with no email is simply not set up, and nobody is sent '
                    'anything until you fill it in.'),
        ('', ''),
        ('head', 'Gets their own territory and nobody else\'s. The key, region '
                 'and name are already filled in from the sales sheet — you add '
                 'only the email.'),
        ('manager', 'Gets one report covering several territories, plus each of '
                    'those heads\' own files. You add the name, the email, and '
                    'which territories they cover.'),
        ('', ''),
        ('regions_covered', 'Managers only. Type the region codes separated by '
                            'semicolons:'),
        ('', 'GTR01;GTR02;GTR03 A'),
        ('', 'Or the single word ALL for somebody who should see every '
             'territory. ALL has to be typed — leaving it blank means no '
             'territories, never all of them.'),
        ('', ''),
        ('Shaded cells', 'Come off the sales sheet. They are what the report is '
                         'built from, so changing one points it at the wrong '
                         'person. Read them; do not edit them.'),
        ('White cells', 'Yours to fill in.'),
        ('', ''),
        ('When you are done', 'Save the file and upload it on the same screen '
                              'you downloaded it from. It reports back line by '
                              'line what it took and what it could not — one '
                              'bad address does not throw away the rest.'),
        ('', ''),
        ('Doing it in stages', 'Fine. Upload it as often as you like; rows that '
                               'are already set up are updated, not duplicated.'),
    ]
    for i, (left, right) in enumerate(rows, start=3):
        line(i, left, right)

    return wb


def heads_for(rows, existing):
    """Attach whatever email is already on file to each head row."""
    by_key = {}
    for rec in existing:
        if rec.head_key:
            by_key[rec.head_key.strip().lower()] = rec.email
    for r in rows:
        r.email_prefill = (by_key.get((r.head_code or '').strip().lower())
                           or by_key.get((r.region or '').strip().lower()) or '')
    return rows
