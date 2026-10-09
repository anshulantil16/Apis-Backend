"""The recipient list, as a sheet people fill in.

One sheet, one header row, one row per person. The email column is the only
thing to type.

It used to carry a title, a row of hints, a section banner above each block
and a second tab of instructions. All of it was furniture the importer then
had to recognise and skip, and the one time a row of it was misread the
screen filled with errors about rows nobody had typed. A list that is only a
list cannot do that.
"""
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HEAD_BG = '1F6FB2'

COLUMNS = [('role', 11), ('key', 14), ('region', 13), ('name', 28),
           ('email', 34), ('regions_covered', 26)]
BLANK_MANAGER_ROWS = 5


def build(heads, managers):
    """-> a Workbook. `heads` are ReviewRow, `managers` ReportRecipient."""
    wb = Workbook()
    ws = wb.active
    ws.title = 'Recipients'

    for i, (name, width) in enumerate(COLUMNS, start=1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(name='Calibri', size=11, bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor=HEAD_BG)
        c.alignment = Alignment(horizontal='center', vertical='center')
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.freeze_panes = 'A2'

    r = 2
    for h in heads:
        for i, v in enumerate(['head', h.sheet_key, h.region or '',
                               h.head_name, h.email_prefill, ''], start=1):
            ws.cell(row=r, column=i, value=v)
        r += 1

    for m in managers:
        for i, v in enumerate(['manager', '', '', m.name, m.email,
                               'ALL' if m.covers_all
                               else ';'.join(m.regions or [])], start=1):
            ws.cell(row=r, column=i, value=v)
        r += 1

    # A few spare lines so adding a manager does not mean working out what
    # the columns were.
    for _ in range(BLANK_MANAGER_ROWS):
        ws.cell(row=r, column=1, value='manager')
        r += 1

    return wb


def heads_for(rows, existing):
    """Give each head row the key it will be addressed by, and any email
    already on file against it."""
    from .views.recipients import candidates, unique_key

    by_key = {}
    for rec in existing:
        if rec.head_key:
            by_key[rec.head_key.strip().lower()] = rec.email
    for r in rows:
        r.sheet_key = unique_key(r, rows)
        # Looked up by whichever identifier the list was filled in with, not
        # only the one we would hand out today -- a list set up before the
        # key changed still finds its own rows.
        r.email_prefill = ''
        for ident in (r.sheet_key, r.head_code, r.region, r.head_name):
            got = by_key.get((ident or '').strip().lower())
            # Only where that identifier names this row and no other, or a
            # shared region would copy one head's address onto another's row.
            if got and candidates(ident, rows) == [r]:
                r.email_prefill = got
                break
    return rows
