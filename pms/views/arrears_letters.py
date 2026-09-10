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
import uuid
import zipfile
from datetime import datetime

import openpyxl
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import get_connection
from django.db import connections
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from ..arrears_letter import (ARREARS_COMPONENTS, COMPONENT_HEADERS, generate_arrears_pdf,
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


def _filename(letter):
    """Employee code and name, safe for a filesystem and a mail attachment."""
    stem = f'APIS_Arrears_{letter.employee_code}_{letter.employee_name}'.strip('_')
    return _SAFE.sub('_', stem)[:120] + '.pdf'


class ArrearsTemplateView(APIView):
    """The Excel to fill in. Built from the component list rather than typed
    out, so a new component cannot appear on the PDF and be missing here."""

    def get(self, request):
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

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resp = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = 'attachment; filename="Arrears_Template.xlsx"'
        return resp


def _process_batch(rows, batch_id, send_emails):
    """Generate a PDF per row, optionally mail it, and keep the batch updated.

    Its own thread, its own DB connection, one SMTP connection for the lot.
    Every failure is recorded against the row that caused it and the run
    continues - one bad email address must not cost the other 300 people
    their statement.
    """
    batch = ArrearsLetterBatch.objects.get(batch_id=batch_id)
    conn = None
    if send_emails:
        conn = get_connection(
            host=settings.EMAIL_HOST, port=settings.EMAIL_PORT,
            username=settings.OFFER_LETTER_EMAIL_HOST_USER,
            password=settings.OFFER_LETTER_EMAIL_HOST_PASSWORD,
            use_tls=settings.EMAIL_USE_TLS, fail_silently=False)

    try:
        for row in rows:
            letter = None
            try:
                breakup = row.pop('_breakup')
                letter = ArrearsLetter.objects.create(batch_id=batch_id,
                                                      salary_breakup=breakup,
                                                      totals=totals(breakup), **row)
                pdf = generate_arrears_pdf(letter)
                name = _filename(letter)
                letter.pdf_file.save(name, ContentFile(pdf.getvalue()), save=False)
                letter.status = 'generated'
                batch.generated += 1

                if send_emails and letter.email_address:
                    send_arrears_email(letter.email_address, letter.employee_name, pdf,
                                       period=letter.period, connection=conn, filename=name)
                    letter.email_sent = True
                    letter.email_sent_at = timezone.now()
                    letter.status = 'sent'
                    batch.emailed += 1
                letter.save()
            except Exception as e:
                batch.failed += 1
                who = row.get('employee_name') or row.get('employee_code') or 'a row'
                batch.errors = (batch.errors or []) + [f'{who}: {e}']
                if letter and letter.pk:
                    letter.status = 'failed'
                    letter.error_message = str(e)[:500]
                    letter.save(update_fields=['status', 'error_message'])
            finally:
                batch.processed += 1
                batch.save()
        batch.status = 'completed'
    except Exception as e:
        batch.status = 'error'
        batch.errors = (batch.errors or []) + [f'Batch stopped: {e}']
    finally:
        batch.save()
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        # A thread gets its own connection and must hand it back, or the pool
        # leaks one per run.
        connections.close_all()


class ArrearsUploadView(APIView):
    """Read the sheet now, generate in the background, hand back a batch id."""
    parser_classes = (MultiPartParser, FormParser)

    def post(self, request):
        send_emails = str(request.data.get('send_emails', 'false')).lower() == 'true'
        f = request.FILES.get('file')
        if not f:
            return Response({'error': 'Choose a file to upload.'}, status=400)
        try:
            ws = openpyxl.load_workbook(f, data_only=True).active
        except Exception as e:
            return Response({'error': f'Could not read that file: {e}'}, status=400)

        header_map = _header_map()
        headers = [_norm(c.value) for c in ws[1]]
        unknown = [str(c.value) for c, h in zip(ws[1], headers)
                   if h and h not in header_map]
        if not any(h in header_map for h in headers):
            return Response({'error': 'That sheet has none of the expected columns. '
                                      'Download the template and fill that in.'}, status=400)

        rows, problems, skipped = [], [], 0
        for n, raw in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
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
            return Response({'error': 'Nothing to generate from that sheet.',
                             'problems': problems[:50]}, status=400)

        batch_id = f'ARR-{datetime.now():%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}'
        ArrearsLetterBatch.objects.create(batch_id=batch_id, total=len(rows),
                                          send_emails=send_emails)
        threading.Thread(target=_process_batch, args=(rows, batch_id, send_emails),
                         daemon=True).start()

        return Response({'batch_id': batch_id, 'total': len(rows),
                         'skipped_samples': skipped,
                         'ignored_columns': unknown[:10],
                         'problems': problems[:50]})


class ArrearsBatchStatusView(APIView):
    """Polled by the progress bar while the thread works."""

    def get(self, request, batch_id):
        b = ArrearsLetterBatch.objects.filter(batch_id=batch_id).first()
        if not b:
            return Response({'error': 'No such batch.'}, status=404)
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
        letters = ArrearsLetter.objects.filter(batch_id=batch_id) if batch_id \
            else ArrearsLetter.objects.all()[:500]
        letters = [l for l in letters if l.status != 'failed']
        if not letters:
            return Response({'error': 'Nothing to download.'}, status=404)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for l in letters:
                try:
                    if l.pdf_file:
                        l.pdf_file.open('rb')
                        data = l.pdf_file.read()
                        l.pdf_file.close()
                    else:
                        data = generate_arrears_pdf(l).read()
                    z.writestr(_filename(l), data)
                except Exception:
                    # One unreadable file must not cost the whole download.
                    continue
        buf.seek(0)
        resp = HttpResponse(buf.read(), content_type='application/zip')
        resp['Content-Disposition'] = f'attachment; filename="APIS_Arrears_{len(letters)}.zip"'
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
        return Response({
            'total': total,
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
            } for l in qs[:500]],
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
