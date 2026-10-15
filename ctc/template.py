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

from .columns import COLUMNS

SHEET_NAME = 'Candidates'

EXAMPLE = ['Rahul Sharma', 'Sales', 'Area Sales Manager', 'M3', 'Management', 'Delhi HO', 1200000]

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
