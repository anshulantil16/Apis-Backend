"""Read a filled-in CTC template into candidate rows.

Headings are matched loosely — case, spacing and stray punctuation are
ignored — because "candidate name ", "Candidate Name" and "CANDIDATE_NAME"
all mean the same thing to the person typing them. Only the first sheet, or
the one named "Candidates", is read.

Nothing is saved here. parse() returns the rows and the problems it found,
and the view decides: any problem and nothing is stored, so a half-imported
sheet never has to be untangled.
"""
import re
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

from .columns import COLUMNS, MAX_CTC, MAX_LENGTH
from .template import SHEET_NAME

MAX_ROWS = 2000


class SheetError(Exception):
    """The workbook as a whole can't be read. Message is for the uploader."""


def _key(text):
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


def _clean(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return re.sub(r'\s+', ' ', str(value)).strip()


def _amount(text):
    """A rupee amount typed any reasonable way — 1200000, 12,00,000,
    ₹ 12,00,000, 1200000.50 — as a Decimal, or None if it isn't one."""
    cleaned = re.sub(r'(?i)(rs\.?|inr|₹|,|\s)', '', text)
    if not cleaned:
        return None
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def parse(fileobj):
    """(rows, errors). Each row is {'row_number': n, <field>: value, ...}."""
    try:
        wb = load_workbook(fileobj, read_only=True, data_only=True)
    except Exception:
        raise SheetError("That file couldn't be read as an Excel workbook. "
                         'Please use the downloaded template (.xlsx).')
    try:
        ws = wb[SHEET_NAME] if SHEET_NAME in wb.sheetnames else wb.worksheets[0]
        lines = ws.iter_rows(values_only=True)
        header = next(lines, None)
        if not header:
            raise SheetError('The sheet is empty. Fill in the template and upload it again.')

        wanted = {_key(heading): field for heading, field, *_ in COLUMNS}
        position = {}
        for i, h in enumerate(header):
            field = wanted.get(_key(h))
            if field and field not in position:
                position[field] = i
        missing = [heading for heading, field, *_ in COLUMNS if field not in position]
        if missing:
            raise SheetError('These columns are missing from the first row: '
                             + ', '.join(missing) + '. Please use the downloaded template.')

        rows, errors = [], []
        for n, values in enumerate(lines, start=2):
            values = values or ()
            row = {field: _clean(values[i] if i < len(values) else None)
                   for field, i in position.items()}
            if not any(row.values()):
                continue                                  # blank line
            if len(rows) >= MAX_ROWS:
                raise SheetError(f'Please upload at most {MAX_ROWS} candidates at a time.')
            if not row['candidate_name']:
                errors.append(f'Row {n}: Candidate Name is empty.')
            ctc_text, row['ctc'] = row['ctc'], None
            if not ctc_text:
                errors.append(f'Row {n}: CTC is empty.')
            else:
                amount = _amount(ctc_text)
                if amount is None:
                    errors.append(f'Row {n}: CTC "{ctc_text}" is not a number.')
                elif amount <= 0 or amount > MAX_CTC:
                    errors.append(f'Row {n}: CTC {ctc_text} is outside the expected range.')
                else:
                    row['ctc'] = amount.quantize(Decimal('0.01'))
            for field, value in row.items():
                if field == 'ctc':
                    continue
                if len(value) > MAX_LENGTH[field]:
                    heading = next(h for h, f, *_ in COLUMNS if f == field)
                    errors.append(f'Row {n}: {heading} is longer than {MAX_LENGTH[field]} characters.')
            rows.append({'row_number': n, **row})
        return rows, errors
    finally:
        wb.close()
