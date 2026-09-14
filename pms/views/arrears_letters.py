"""Arrears Compensation Structure - template, upload, generate, mail, download.

Same flow as the offer letters next door, and deliberately so: download a
template, fill it, upload it, watch a progress bar, then pull the PDFs down as
a zip or let the system mail them out.

The generation runs in a background thread on one shared SMTP connection. A
few hundred people is a few hundred PDFs and a few hundred emails, and doing
that inside the request would time out well before it finished.
"""
import io
import re
import threading
import time
import uuid
import zipfile
from datetime import datetime, timedelta
from queue import Queue
from tempfile import SpooledTemporaryFile

import openpyxl
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import get_connection
from django.db import connections
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from ..arrears_letter import (ARREARS_COMPONENTS, COMPONENT_HEADERS, generate_arrears_pdf,
                              totals_from_months,
                              send_arrears_email, totals)
from ..models import ArrearsLetter, ArrearsLetterBatch

# The identity columns, in the order they appear in the template. Anything not
# listed here that looks like a component header is matched separately.
IDENTITY_COLUMNS = [
    ('SR NO', 'sr_no'),
    ('Employee ID *', 'employee_code'),
    ('Employee Name *', 'employee_name'),
    ('Email *', 'email_address'),
    ('Department', 'department'),
    ('Designation', 'designation'),
    ('Cadre', 'cadre'),
    ('Grade', 'grade'),
    ('Paid Days (Monthly)', 'paid_days'),
    ('Arrears Period', 'period'),
]

_SAFE = re.compile(r'[^A-Za-z0-9._-]+')


def _norm(h):
    """Header text down to a comparable key: case, spaces and the required
    asterisk all removed, so "Employee ID *" and "employee id" both match."""
    return re.sub(r'\s+', ' ', str(h or '').replace('*', '').strip()).lower()


def _header_map():
    """Every accepted column header -> the field it fills."""
    m = {_norm(label): field for label, field in IDENTITY_COLUMNS}
    for key, _section, _label in ARREARS_COMPONENTS:
        m[_norm(COMPONENT_HEADERS[key])] = f'component:{key}'
    return m


# The Old/New pair sheets. Both the master structure and each month are the
# same shape of data - a before and an after for every component - so they
# share one header vocabulary and one parser.
MASTER_SHEET = 'Master'
MONTHLY_SHEET = 'Monthly'


def _old_new_headers():
    """'Basic Salary Old' -> ('basic', 'old'), for the Master/Monthly sheets."""
    m = {}
    for key, _section, _label in ARREARS_COMPONENTS:
        label = COMPONENT_HEADERS[key]
        m[_norm(f'{label} Old')] = (key, 'old')
        m[_norm(f'{label} New')] = (key, 'new')
    return m


def _sheet_rows(ws):
    """(normalised headers, iterator over the data rows below them).

    Everything reads a sheet through here so nothing indexes `ws[1]` or
    touches `ws.max_column`. Both are cheap on a normal workbook and both
    are traps on a read-only one, where the sheet is a forward-only stream
    and random access silently re-reads it.
    """
    it = ws.iter_rows(values_only=True)
    try:
        header = next(it)
    except StopIteration:
        return [], iter(())
    return [_norm(v) for v in header], it


def _read_old_new(ws, extra_columns):
    """Rows of {employee_code, <extra>, components:{key:{old,new}}} from a sheet.

    `extra_columns` maps a header to the plain field it fills (Month, Present
    Days). Returns a list in sheet order - the order the arrears period runs
    in is information, and sorting it would throw that away.
    """
    if ws is None:
        return []
    pairs = _old_new_headers()
    extra = {_norm(h): f for h, f in extra_columns.items()}
    headers, body = _sheet_rows(ws)
    out = []
    for raw in body:
        if not any(v not in (None, '') for v in raw):
            continue
        rec, comps = {}, {}
        for h, value in zip(headers, raw):
            if h in pairs:
                key, side = pairs[h]
                comps.setdefault(key, {})[side] = value
            elif h in extra:
                rec[extra[h]] = str(value).strip() if value is not None else ''
            elif h in ('employee id', 'employee code'):
                rec['employee_code'] = str(value).strip() if value is not None else ''
        code = rec.get('employee_code', '')
        # Same rule as the main sheet: the shipped example is skipped, which
        # is what makes leaving it in harmless rather than a trap.
        if not code or code.upper().startswith('SAMPLE-'):
            continue
        rec['components'] = comps
        out.append(rec)
    return out


def _filename(letter):
    """Employee code and name, safe for a filesystem and a mail attachment."""
    stem = f'APIS_Arrears_{letter.employee_code}_{letter.employee_name}'.strip('_')
    return _SAFE.sub('_', stem)[:120] + '.pdf'


class ArrearsTemplateView(APIView):
    """The Excel to fill in. Built from the component list rather than typed
    out, so a new component cannot appear on the PDF and be missing here."""

    def get(self, request):
        from openpyxl.comments import Comment
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Arrears'

        identity = [label for label, _f in IDENTITY_COLUMNS]
        components = [COMPONENT_HEADERS[k] for k, _s, _l in ARREARS_COMPONENTS]
        headers = identity + components

        required = PatternFill('solid', fgColor='2E75B6')
        optional = PatternFill('solid', fgColor='FFEB9C')
        money = PatternFill('solid', fgColor='C6E0B4')
        thin = Side(style='thin')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)

        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            is_required = '*' in h
            c.fill = required if is_required else (money if ci > len(identity) else optional)
            c.font = Font(color='FFFFFF' if is_required else '000000', bold=True, size=10)
            c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
            c.border = border
            ws.column_dimensions[c.column_letter].width = max(14, min(len(h) + 4, 34))
        ws.row_dimensions[1].height = 42

        # One filled example, so the shape of a row is obvious. SAMPLE- is
        # skipped on upload, which is what makes leaving it in harmless.
        sample = {
            'SR NO': 1, 'Employee ID *': 'SAMPLE-001',
            'Employee Name *': 'Sample Employee', 'Email *': 'sample@apisindia.com',
            'Department': 'Sales', 'Designation': 'Manager', 'Cadre': 'M',
            'Grade': 'M5', 'Paid Days (Monthly)': '30',
            'Arrears Period': 'Apr 2026 to Aug 2026',
        }
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=2, column=ci, value=sample.get(h, 0 if ci > len(identity) else ''))
            c.border = border
        ws.freeze_panes = 'A2'

        # ── Master and Monthly: the before-and-after figures ────────────────
        # Kept on their own sheets rather than bolted onto the first one. Two
        # columns per component for the master and two more per month would
        # put this sheet past sixty columns, at which point nobody can see
        # which employee they are typing against.
        pair_heads = []
        for key, _s, _l in ARREARS_COMPONENTS:
            pair_heads += [f'{COMPONENT_HEADERS[key]} Old', f'{COMPONENT_HEADERS[key]} New']

        def pair_sheet(name, lead, note, samples):
            sh = wb.create_sheet(name)
            head = lead + pair_heads
            for ci, h in enumerate(head, 1):
                c = sh.cell(row=1, column=ci, value=h)
                is_req = '*' in h
                c.fill = required if is_req else (money if ci > len(lead) else optional)
                c.font = Font(color='FFFFFF' if is_req else '000000', bold=True, size=10)
                c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
                c.border = border
                sh.column_dimensions[c.column_letter].width = max(13, min(len(h) + 3, 26))
            sh.row_dimensions[1].height = 42
            for ri, row in enumerate(samples, start=2):
                for ci, h in enumerate(head, 1):
                    sh.cell(row=ri, column=ci,
                            value=row.get(h, 0 if ci > len(lead) else '')).border = border
            sh.freeze_panes = 'B2'
            # A note on the header cell, not a line of prose in the grid. Put
            # in a cell below the samples it was read straight back as an
            # employee row on upload, and reported as an employee who was
            # missing from the Arrears sheet.
            sh['A1'].comment = Comment(note, 'APIS Intranet', height=110, width=340)
            return sh

        pair_sheet(
            MASTER_SHEET, ['Employee ID *'],
            'One row per employee: the monthly salary structure before and after '
            'the revision. Used as the reference column on the distribution page.',
            [{'Employee ID *': 'SAMPLE-001'}])

        pair_sheet(
            MONTHLY_SHEET, ['Employee ID *', 'Month *', 'Present Days'],
            'One row per employee per month, in the order the arrears period runs. '
            'Enter what was actually earned old and new; the difference and every '
            'total are worked out from these, so they are never typed twice.',
            [{'Employee ID *': 'SAMPLE-001', 'Month *': 'Apr 2026', 'Present Days': 30},
             {'Employee ID *': 'SAMPLE-001', 'Month *': 'May 2026', 'Present Days': 30}])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="Arrears_Template.xlsx"'
        return resp


def _mail_connection():
    """One SMTP connection, configured the way this product's mail is."""
    return get_connection(
        host=settings.EMAIL_HOST, port=settings.EMAIL_PORT,
        username=settings.OFFER_LETTER_EMAIL_HOST_USER,
        password=settings.OFFER_LETTER_EMAIL_HOST_PASSWORD,
        use_tls=settings.EMAIL_USE_TLS, fail_silently=False)


# How many statements to send at once. Sending used to sit strictly behind PDF
# generation: a thousand people meant a thousand SMTP round trips one after
# another, each waiting on a server across the internet, and the run cost
# generation time PLUS sending time. They overlap now, so it costs roughly the
# longer of the two.
#
# Six, not sixty. The ceiling here belongs to the mail provider, not to us -
# Google caps simultaneous connections per account and answers a burst with a
# temporary block, which would fail the very batch this is meant to speed up.
MAIL_WORKERS = 6

# Enough to keep every sender busy through a slow patch without holding the
# whole run in memory. A statement is around 10 KB, so this is a couple of
# hundred kilobytes rather than the ~11 MB a thousand of them would be.
MAIL_QUEUE = 24


def _read_pair_sheets(blob):
    """(master_by_code, monthly_by_code, warnings) from the uploaded workbook.

    Runs in the background thread, not the request - see the note in the
    upload view. Returns empty maps for a workbook with neither sheet, which
    is the file this feature shipped with and must keep working.
    """
    master_by_code, monthly_by_code, warnings = {}, {}, []
    try:
        wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True, read_only=True)
    except Exception as e:
        return {}, {}, [f'Could not re-read the workbook for the monthly detail: {e}']
    try:
        for rec in _read_old_new(
                wb[MASTER_SHEET] if MASTER_SHEET in wb.sheetnames else None, {}):
            master_by_code[rec['employee_code']] = rec['components']
        for rec in _read_old_new(
                wb[MONTHLY_SHEET] if MONTHLY_SHEET in wb.sheetnames else None,
                {'Month *': 'month', 'Present Days': 'present_days'}):
            monthly_by_code.setdefault(rec['employee_code'], []).append({
                'month': rec.get('month', ''),
                'present_days': rec.get('present_days', ''),
                'components': rec['components'],
            })
    finally:
        wb.close()
    return master_by_code, monthly_by_code, warnings


def _process_batch(rows, batch_id, send_emails, blob=b''):
    """Generate a PDF per row, optionally mail it, and keep the batch updated.

    Generation runs here, on one thread, because it is CPU work and racing it
    against itself buys nothing. Sending runs on a small pool of threads, each
    holding its own SMTP connection, fed through a bounded queue - so the mail
    for statement 1 is in flight while statement 2 is still being drawn.

    Every failure is recorded against the row that caused it and the run
    continues - one bad email address must not cost the other 999 people
    their statement.
    """
    batch = ArrearsLetterBatch.objects.get(batch_id=batch_id)
    lock = threading.Lock()
    outbox = Queue(maxsize=MAIL_QUEUE)
    # Counters live here and are published periodically. Saving the batch row
    # after every one of a thousand statements is a thousand writes to say
    # almost nothing, and the progress bar cannot show that much detail anyway.
    tally = {'processed': 0, 'generated': 0, 'emailed': 0, 'failed': 0, 'errors': []}
    last_flush = [0.0]

    def note(**deltas):
        with lock:
            for k, v in deltas.items():
                if k == 'error':
                    tally['errors'].append(v)
                else:
                    tally[k] += v

    def flush(force=False):
        """Publish progress. Rate-limited, but never so slow that the stall
        detector in the status view mistakes a healthy run for a dead one."""
        now = time.monotonic()
        with lock:
            if not force and now - last_flush[0] < 1.5:
                return
            last_flush[0] = now
            batch.processed = tally['processed']
            batch.generated = tally['generated']
            batch.emailed = tally['emailed']
            batch.failed = tally['failed']
            batch.errors = tally['errors'][:200]
        batch.save()

    def sender():
        """One SMTP connection, draining the queue until told to stop."""
        conn = None
        try:
            conn = _mail_connection()
            while True:
                item = outbox.get()
                try:
                    if item is None:
                        return
                    letter, pdf, name = item
                    try:
                        send_arrears_email(letter.email_address, letter.employee_name,
                                           pdf, period=letter.period,
                                           connection=conn, filename=name)
                        letter.email_sent = True
                        letter.email_sent_at = timezone.now()
                        letter.status = 'sent'
                        letter.save(update_fields=['email_sent', 'email_sent_at', 'status'])
                        note(emailed=1)
                    except Exception as e:
                        # The statement itself was generated and is kept. Only
                        # delivery failed, and it says so against that person.
                        letter.status = 'failed'
                        letter.error_message = f'Generated, but the email failed: {e}'[:500]
                        letter.save(update_fields=['status', 'error_message'])
                        note(failed=1, error=f'{letter.employee_name}: email failed - {e}')
                finally:
                    outbox.task_done()
        except Exception as e:
            note(error=f'A mail sender stopped: {e}')
            # Drain rather than leave the generator blocked on a full queue for
            # ever, waiting for a worker that is no longer there.
            while True:
                item = outbox.get()
                outbox.task_done()
                if item is None:
                    return
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            # A thread gets its own DB connection and must hand it back, or the
            # pool leaks one per worker per run.
            connections.close_all()

    # The heavy sheets, read here rather than in the request.
    master_by_code, monthly_by_code, warnings = _read_pair_sheets(blob)
    # Month or master rows for somebody who never appears on the employee sheet
    # produce nothing at all. Silently dropping them is how a distribution page
    # goes missing and nobody knows why.
    known = {r.get('employee_code') for r in rows}
    for sheet, seen in ((MONTHLY_SHEET, monthly_by_code), (MASTER_SHEET, master_by_code)):
        for code in seen:
            if code not in known:
                warnings.append(f'{sheet} sheet: {code} has rows there but is not on '
                                f'the Arrears sheet, so nothing was generated for them.')
    for w in warnings:
        note(error=w)
    flush(force=True)

    workers = []
    if send_emails:
        workers = [threading.Thread(target=sender, daemon=True)
                   for _ in range(MAIL_WORKERS)]
        for w in workers:
            w.start()

    try:
        for row in rows:
            row['_master'] = master_by_code.get(row.get('employee_code'), {})
            row['_monthly'] = monthly_by_code.get(row.get('employee_code'), [])
            letter = None
            try:
                breakup = row.pop('_breakup')
                master = row.pop('_master', {}) or {}
                monthly = row.pop('_monthly', []) or []
                # Where a month-wise breakdown exists it IS the arrears: each
                # component's total is its monthly differences added up. The
                # figures typed on the main sheet are the fallback for rows
                # that have no monthly detail, not a second opinion on rows
                # that do - one number must have one source.
                effective = totals_from_months(monthly) or breakup
                letter = ArrearsLetter.objects.create(batch_id=batch_id,
                                                      salary_breakup=effective,
                                                      master_breakup=master,
                                                      monthly_breakup=monthly,
                                                      totals=totals(effective), **row)
                pdf = generate_arrears_pdf(letter)
                name = _filename(letter)
                letter.pdf_file.save(name, ContentFile(pdf.getvalue()), save=False)
                letter.status = 'generated'
                letter.save()
                note(generated=1)

                if send_emails and letter.email_address:
                    # Blocks once the queue is full, which is what stops a fast
                    # generator running a thousand PDFs ahead of a slow mail
                    # server and holding every one of them in memory.
                    outbox.put((letter, pdf, name))
            except Exception as e:
                who = row.get('employee_name') or row.get('employee_code') or 'a row'
                note(failed=1, error=f'{who}: {e}')
                if letter and letter.pk:
                    letter.status = 'failed'
                    letter.error_message = str(e)[:500]
                    letter.save(update_fields=['status', 'error_message'])
            finally:
                note(processed=1)
                flush()

        # Everything is drawn; wait for the mail still in flight.
        for _ in workers:
            outbox.put(None)
        for w in workers:
            w.join()
        batch.status = 'completed'
    except Exception as e:
        with lock:
            tally['errors'].append(f'Batch stopped: {e}')
        batch.status = 'error'
    finally:
        flush(force=True)
        batch.save()
        connections.close_all()


class ArrearsUploadView(APIView):
    """Read the sheet now, generate in the background, hand back a batch id."""
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        send_emails = str(request.data.get('send_emails', 'false')).lower() == 'true'
        f = request.FILES.get('file')
        if not f:
            return Response({'error': 'Choose a file to upload.'}, status=400)
        # The whole file, once. A few megabytes at any realistic headcount, and
        # it has to outlive this request: only the Arrears sheet is read here.
        blob = f.read()

        # read_only, and only the first sheet. The Monthly sheet is one row per
        # employee per month, so a thousand people over eight months is eight
        # thousand rows of forty-odd columns - about 340,000 cells, which
        # openpyxl needs roughly twelve seconds to walk however it is asked.
        # Against gunicorn's default thirty-second timeout that is comfortable
        # at a thousand people and fatal somewhere past two thousand, and a
        # request killed mid-parse looks to the user like the upload failed.
        #
        # So the request reads the employee rows - what every per-row complaint
        # below is about, and what makes immediate feedback worth having - and
        # the Master and Monthly sheets are parsed in the background thread,
        # where nothing is waiting on a socket.
        try:
            wb = openpyxl.load_workbook(io.BytesIO(blob), data_only=True, read_only=True)
        except Exception as e:
            return Response({'error': f'Could not read that file: {e}'}, status=400)

        try:
            header_map = _header_map()
            raw_header, body = _sheet_rows(wb[wb.sheetnames[0]])
            unknown = [h for h in raw_header if h and h not in header_map]
            headers = raw_header
            if not any(h in header_map for h in headers):
                return Response({'error': 'That sheet has none of the expected columns. '
                                          'Download the template and fill that in.'}, status=400)

            rows, problems, skipped = [], [], 0
            for n, raw in enumerate(body, start=2):
                if not any(v not in (None, '') for v in raw):
                    continue
                rec, breakup = {}, {}
                for h, value in zip(headers, raw):
                    field = header_map.get(h)
                    if not field or field == 'sr_no':
                        continue
                    if field.startswith('component:'):
                        breakup[field.split(':', 1)[1]] = value
                    else:
                        rec[field] = str(value).strip() if value is not None else ''

                code = rec.get('employee_code', '')
                if code.upper().startswith('SAMPLE-'):
                    skipped += 1
                    continue
                if not code or not rec.get('employee_name'):
                    problems.append(f'Row {n}: needs an Employee ID and a name.')
                    continue
                if send_emails and not rec.get('email_address'):
                    problems.append(f'Row {n}: {rec["employee_name"]} has no email address, '
                                    f'so nothing can be sent to them.')
                    continue
                rec['_breakup'] = breakup
                rows.append(rec)

            if not rows:
                # "Nothing to generate" is true but useless. The overwhelmingly
                # likely cause is the template uploaded as downloaded, with only
                # the example row in it - so say that, and say what to do.
                if skipped and not problems:
                    msg = ('That is the template with only the example row in it. '
                           'Add your employees on the rows below it, then upload again.')
                elif problems:
                    msg = ('No row in that sheet could be used - see below.')
                else:
                    msg = ('That sheet has no employee rows in it.')
                return Response({'error': msg, 'problems': problems[:50]}, status=400)

        finally:
            # A read-only workbook keeps the uploaded zip open until it is
            # told otherwise, and nothing below needs the sheet again.
            wb.close()

        batch_id = f'ARR-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}'
        ArrearsLetterBatch.objects.create(batch_id=batch_id, total=len(rows),
                                          send_emails=send_emails)
        threading.Thread(target=_process_batch,
                         args=(rows, batch_id, send_emails, blob),
                         daemon=True).start()

        return Response({'batch_id': batch_id, 'total': len(rows),
                         'skipped_samples': skipped,
                         'ignored_columns': unknown[:10],
                         'problems': problems[:50]})


class ArrearsBatchStatusView(APIView):
    """Polled by the progress bar while the thread works."""

    # The worker saves the batch after every single row, so updated_at is a
    # real heartbeat. Generous enough that a slow SMTP handshake on one row is
    # not mistaken for death.
    STALL_AFTER = timedelta(minutes=5)

    def get(self, request, batch_id):
        b = ArrearsLetterBatch.objects.filter(batch_id=batch_id).first()
        if not b:
            return Response({'error': 'No such batch.'}, status=404)

        # A daemon thread dies with the process. Restart gunicorn mid-run and
        # the batch stayed 'running' for ever, which the page reads as "still
        # working" - so it polled every 1.2 seconds, indefinitely, for a run
        # that had already stopped. The offer and warning letters have guarded
        # against this since they were written; this one was copied from them
        # without it.
        if b.status == 'running' and b.updated_at < timezone.now() - self.STALL_AFTER:
            b.status = 'error'
            b.errors = (b.errors or []) + [
                'Generation stopped unexpectedly - the server most likely '
                'restarted mid-run. The statements already generated are kept; '
                'upload the remaining rows again.']
            b.save(update_fields=['status', 'errors'])

        return Response({
            'batch_id': b.batch_id, 'status': b.status, 'total': b.total,
            'processed': b.processed, 'generated': b.generated,
            'emailed': b.emailed, 'failed': b.failed,
            'send_emails': b.send_emails, 'errors': (b.errors or [])[:50],
        })


class ArrearsPDFView(APIView):
    """One statement, by id."""

    def get(self, request, letter_id):
        letter = ArrearsLetter.objects.filter(id=letter_id).first()
        if not letter:
            return Response({'error': 'No such statement.'}, status=404)
        if letter.pdf_file:
            letter.pdf_file.open('rb')
            data = letter.pdf_file.read()
            letter.pdf_file.close()
        else:
            # Regenerated rather than 404ing: the stored breakup is the source
            # of truth, and a missing file is a storage problem, not a data one.
            data = generate_arrears_pdf(letter).read()
        resp = HttpResponse(data, content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="{_filename(letter)}"'
        return resp


class ArrearsDownloadAllView(APIView):
    """Every statement in a batch, as one zip."""

    def get(self, request):
        batch_id = request.query_params.get('batch_id')
        qs = ArrearsLetter.objects.exclude(status='failed')
        qs = qs.filter(batch_id=batch_id) if batch_id else qs[:500]

        # Only the columns the zip actually uses. The full row carries the
        # month-wise breakup - hundreds of numbers of JSON each - and pulling a
        # thousand of those into memory to read a filename and a file path off
        # them was most of the cost of this endpoint. A row that has to be
        # regenerated fetches its own deferred fields, which is one extra query
        # on the rare row that needs it rather than a tax on every row.
        qs = qs.only('id', 'pdf_file', 'employee_code', 'employee_name', 'status')

        # Spooled: a small batch is assembled in memory, a big one rolls over
        # to disk on its own. Buffering a thousand statements in a BytesIO and
        # then copying the whole thing again into the response held two copies
        # of the zip at once, for no benefit.
        tmp = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
        count = 0
        with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as z:
            for l in qs.iterator(chunk_size=100):
                try:
                    if l.pdf_file:
                        l.pdf_file.open('rb')
                        try:
                            data = l.pdf_file.read()
                        finally:
                            l.pdf_file.close()
                    else:
                        data = generate_arrears_pdf(l).read()
                    z.writestr(_filename(l), data)
                    count += 1
                except Exception:
                    # One unreadable file must not cost the whole download.
                    continue

        if not count:
            return Response({'error': 'Nothing to download.'}, status=404)

        tmp.seek(0)
        # FileResponse streams in chunks and closes the handle when the
        # response is finished, so the zip is never resident in full twice.
        resp = FileResponse(tmp, content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="APIS_Arrears_{count}.zip"'
        return resp


class ArrearsHistoryView(APIView):
    """What has been generated, newest first - and the way to clear it out."""

    def get(self, request):
        qs = ArrearsLetter.objects.all()
        q = (request.query_params.get('q') or '').strip()
        if q:
            from django.db.models import Q
            qs = qs.filter(Q(employee_name__icontains=q) | Q(employee_code__icontains=q)
                           | Q(department__icontains=q) | Q(email_address__icontains=q))
        if request.query_params.get('batch_id'):
            qs = qs.filter(batch_id=request.query_params['batch_id'])

        total = qs.count()
        # Capped like the offer and warning histories next door, but the cap is
        # now reported rather than silent. At a few hundred people a batch this
        # fills up in two or three runs, and a list that quietly stops at 500
        # reads as "that statement was never generated" - which is the one
        # conclusion it must never invite on a salary document.
        try:
            limit = max(1, min(500, int(request.query_params.get('limit', 500))))
        except (TypeError, ValueError):
            limit = 500
        try:
            offset = max(0, int(request.query_params.get('offset', 0)))
        except (TypeError, ValueError):
            offset = 0
        page = list(qs[offset:offset + limit])

        return Response({
            'total': total,
            'returned': len(page),
            'offset': offset,
            'limit': limit,
            'truncated': total > offset + len(page),
            # Shape the Letters Generator hub reads for its live card counts -
            # same keys the offer and warning endpoints return, so the three
            # cards report activity the same way.
            'summary': {
                'total': ArrearsLetter.objects.count(),
                'sent': ArrearsLetter.objects.filter(email_sent=True).count(),
            },
            'letters': [{
                'id': l.id, 'employee_code': l.employee_code,
                'employee_name': l.employee_name, 'email_address': l.email_address,
                'department': l.department, 'designation': l.designation,
                'cadre': l.cadre, 'grade': l.grade, 'period': l.period,
                'totals': l.totals or {},
                'status': l.status, 'email_sent': l.email_sent,
                'error_message': l.error_message,
                'batch_id': l.batch_id,
                'created_at': timezone.localtime(l.created_at).strftime('%d-%m-%Y %H:%M'),
            } for l in page],
        })

    def delete(self, request):
        """Clear generated statements.

        Takes the count as confirmation rather than a yes/no - these carry
        salary figures, and "are you sure" is not a question anyone reads.
        """
        batch_id = request.data.get('batch_id')
        qs = ArrearsLetter.objects.filter(batch_id=batch_id) if batch_id \
            else ArrearsLetter.objects.all()
        count = qs.count()
        if str(request.data.get('confirm_count')) != str(count):
            return Response({'error': f'Type {count} to confirm deleting '
                                      f'{count} statement(s).'}, status=400)
        for l in qs:
            if l.pdf_file:
                l.pdf_file.delete(save=False)
        qs.delete()
        if batch_id:
            ArrearsLetterBatch.objects.filter(batch_id=batch_id).delete()
        return Response({'deleted': count, 'message': f'{count} statement(s) removed.'})
