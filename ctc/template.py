"""The CTC Calculator template: download, fill in one row per candidate,
upload back on the same page.

Generated rather than kept as a file, so it always matches what the
uploader expects (see columns.py). The first sheet holds nothing but the
header row — an example row there would be uploaded by mistake sooner or
later — and the help sits on the header cells as comments, with a worked
example on a second sheet.
"""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from .columns import CHOICES, COLUMNS
from .rules import MIN_WAGES

SHEET_NAME = 'Candidates'

EXAMPLE = ['SL00225', 'Rahul Sharma', 'Sales', 'General Trade', 'Area Sales Manager', 'M1', 'M',
           '01-07-2026', 'Delhi', 1200000]

HEADER_FILL = PatternFill('solid', fgColor='F59E0B')     # APIS amber
REQUIRED_FILL = PatternFill('solid', fgColor='D97706')
HEADER_FONT = Font(bold=True, color='FFFFFF')


def build_template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_NAME
    for i, (heading, _field, width, required, help_text) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=i, value=heading)
        cell.font = HEADER_FONT
        cell.fill = REQUIRED_FILL if required else HEADER_FILL
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.comment = Comment(help_text, 'APIS Intranet')
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = 'A2'

    # Dropdowns, so Category and Cadre can only be typed one way.
    for i, (_h, field, *_rest) in enumerate(COLUMNS, start=1):
        if field in CHOICES:
            dv = DataValidation(type='list', formula1='"' + ','.join(CHOICES[field]) + '"',
                                allow_blank=True, showErrorMessage=True,
                                errorTitle='Pick from the list',
                                error='Choose one of: ' + ', '.join(CHOICES[field]))
            col = get_column_letter(i)
            dv.add(f'{col}2:{col}2001')
            ws.add_data_validation(dv)

    # Work Location: a dropdown of states, kept on a hidden sheet because the
    # list is longer than Excel allows inline. A suggestion, not a rule — a
    # city typed instead still uploads and is matched to its state.
    lists = wb.create_sheet('Lists')
    for r, state in enumerate(MIN_WAGES, start=1):
        lists.cell(row=r, column=1, value=state)
    lists.sheet_state = 'hidden'
    loc_col = next(get_column_letter(i) for i, (_h, f, *_r) in enumerate(COLUMNS, start=1)
                   if f == 'work_location')
    loc = DataValidation(type='list', formula1=f'Lists!$A$1:$A${len(MIN_WAGES)}',
                         allow_blank=True, showErrorMessage=False)
    loc.add(f'{loc_col}2:{loc_col}2001')
    ws.add_data_validation(loc)

    ex = wb.create_sheet('Example')
    ex.cell(row=1, column=1, value='How a filled-in row looks. This sheet is never uploaded — '
                                   'type your candidates on the "Candidates" sheet.').font = Font(italic=True, color='64748B')
    for i, (heading, _field, width, _req, _help) in enumerate(COLUMNS, start=1):
        h = ex.cell(row=3, column=i, value=heading)
        h.font = HEADER_FONT
        h.fill = HEADER_FILL
        ex.cell(row=4, column=i, value=EXAMPLE[i - 1])
        ex.column_dimensions[get_column_letter(i)].width = width

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
