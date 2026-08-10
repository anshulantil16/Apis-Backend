"""Employee-directory Excel template + tolerant upload parsing.

Same alias-matching approach as sales/ingest.py: headers are matched against
known spellings rather than required verbatim, and unrecognised columns are
reported back instead of silently dropped.
"""
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

COLUMN_ALIASES = {
    'employee_code':     ['employee id', 'employee code', 'emp id', 'emp code', 'code'],
    'name':               ['name', 'employee name', 'full name'],
    'email':              ['email', 'email address', 'official email'],
    'department':         ['department', 'dept'],
    'designation':        ['designation', 'title', 'job title'],
    'location':           ['location', 'work location', 'office', 'city'],
    'reporting_manager':  ['reporting manager', 'manager', 'rm'],
}


def _norm(h):
    s = str(h or '').strip().lower().replace('*', '')
    for ch in ('_', '-', '.', '/', '(', ')', ':'):
        s = s.replace(ch, ' ')
    return ' '.join(s.split())


_LOOKUP = {}
for field, aliases in COLUMN_ALIASES.items():
    for a in aliases:
        _LOOKUP[_norm(a)] = field


def map_headers(header_row):
    col_map, unknown = {}, []
    for ci, cell in enumerate(header_row):
        if cell is None or str(cell).strip() == '':
            continue
        key = _norm(cell)
        field = _LOOKUP.get(key)
        if field is None:
            unknown.append(str(cell).strip())
        elif field not in col_map:
            col_map[field] = ci
    return col_map, unknown


def build_template():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Employees'

    headers = ['Employee ID', 'Name *', 'Email *', 'Department', 'Designation',
              'Location', 'Reporting Manager']
    border = Border(*(Side(style='thin'),) * 4)
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        required = '*' in h
        c.fill = PatternFill(start_color='6366F1' if required else 'A5B4FC',
                             end_color='6366F1' if required else 'A5B4FC', fill_type='solid')
        c.font = Font(color='FFFFFF', bold=True, size=10)
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        c.border = border

    samples = [
        ['EMP001', 'Rahul Sharma', 'rahul.sharma@apisindia.com', 'Sales', 'Sales Manager',
         'Delhi HO', 'Vikas Gupta'],
        ['EMP002', 'Priya Singh', 'priya.singh@apisindia.com', 'Operations', 'Executive',
         'Mumbai', 'Anita Desai'],
    ]
    for ri, row in enumerate(samples, 2):
        for ci, v in enumerate(row, 1):
            ws.cell(row=ri, column=ci, value=v).border = border

    for i in range(1, len(headers) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = 22
    ws.freeze_panes = 'A2'

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
