"""Who gets which report, and who may load the morning file.

Three screens' worth of endpoints:

  * upload access -- one more person allowed to load the daily workbook;
  * recipients    -- the heads, each getting their own territory;
  * managers      -- one rolled-up report across the territories they cover,
                     plus each of those heads' own files.

Nothing here sends anything. Composing the list and sending to sixteen real
people are separate decisions, and the second one is the owner's to make
deliberately rather than as a side effect of pasting a spreadsheet column.
"""
import csv
import io
import re

import openpyxl

from django.http import HttpResponse
from rest_framework.response import Response

from .auth import SalesIQView, SalesIQAdminView, SalesIQOwnerView
from ..models import ReportRecipient, UploaderGrant, ReviewSnapshot
from .. import report as REPORT
from .. import recipients_sheet as SHEET
from .. import mail as MAIL


def _rows(snap):
    return [r for r in snap.rows.all() if not r.is_total] if snap else []


def _latest():
    return ReviewSnapshot.objects.first()


def recipient_json(r):
    return {
        'id': r.id, 'role': r.role, 'name': r.name, 'email': r.email,
        'head_key': r.head_key, 'regions': r.regions or [],
        'covers_all': r.covers_all, 'is_active': r.is_active,
        'note': r.note,
        'last_sent_at': r.last_sent_at.isoformat() if r.last_sent_at else None,
    }


# What separates one region from the next in that cell. Semicolon,
# comma and line break: Alt+Enter inside one Excel cell is the natural
# way to write a list of twelve regions. Not whitespace -- a region
# code here is "GTR04 A", and splitting on that space would invent a
# region called A.
SPLIT_ON = re.compile(r'[;,\r\n]+')


def regions_from_cell(text, known):
    """-> ([region, ...], [not on the sheet, ...]).

    Split on semicolons, commas AND line breaks, because all three are what
    people actually type. Alt+Enter inside one Excel cell is the natural way
    to write a list of twelve regions, and it is the one the importer used to
    refuse -- the whole block came back as a single region name, no such
    region existed, and the manager was silently left off the list.

    Not split on whitespace: a region code here is "GTR04 A", and splitting
    on the space inside it would invent a region called A.
    """
    raw = [p.strip() for p in SPLIT_ON.split(text or '')]
    out, unknown, seen = [], [], set()
    for r in raw:
        if not r or r.lower() in seen:
            continue
        seen.add(r.lower())
        match = next((k for k in known if k.lower() == r.lower()), None)
        # Matched case-insensitively but stored as the sheet spells it, so a
        # territory typed "gtr04 a" still lines up with the rows.
        (out.append(match) if match else unknown.append(r))
    return out, unknown


def candidates(key, rows):
    """Every row a recipient's key could mean.

    APIS ID first, then region, then the name on the sheet. An ID because a
    name is not an identity -- the same person is spelled several ways across
    a year of exports, and a report addressed to a spelling goes to nobody.
    """
    low = (key or '').strip().lower()
    if not low:
        return []
    for attr in ('head_code', 'region', 'head_name'):
        hits = [r for r in rows
                if (getattr(r, attr) or '').strip().lower() == low]
        if hits:
            return hits
    return []


def match_head(key, rows):
    """The one row a key means, or None if it means none -- or several.

    A key matching two rows used to resolve to whichever came first. REGION
    is not unique on this sheet: the handover line carries GTR04 A, merged
    down from the row above it, so "GTR04 A" names two people and the second
    of them would have been sent the first one's numbers. Ambiguity is not a
    match; it is reported.
    """
    hits = candidates(key, rows)
    return hits[0] if len(hits) == 1 else None


def unique_key(row, rows):
    """The shortest identifier that names this row and no other.

    The template used to hand out the APIS ID, or the region where there was
    no ID. That is fine for fifteen of sixteen rows and wrong for the one
    that matters -- so the key is checked against the sheet as it is built,
    and falls back to the name where the region is shared.
    """
    for v in (row.head_code, row.region, row.head_name):
        v = (v or '').strip()
        if v and len(candidates(v, rows)) == 1:
            return v
    return (row.head_name or row.region or '').strip()


def _csv_stream(text):
    """A stream csv.reader can read without choking on line endings.

    csv.reader requires its source to be opened with newline='' -- the
    documentation says so, and the failure when it is not is this, verbatim:

        new-line character seen in unquoted field -- do you need to open the
        file with newline=''?

    A CSV saved by Excel on Windows ends its lines with CRLF. Without
    newline='' the stream translates them, csv sees a stray carriage return
    inside a field, and the whole upload is refused -- a file that was
    perfectly correct.
    """
    return io.StringIO(text, newline='')


# What a spreadsheet writes between cells, in the order we try them. Excel
# uses the list separator from the machine's locale when it saves a CSV --
# comma here, semicolon across much of Europe -- and "Text (Tab delimited)"
# writes tabs. Copying a block to the clipboard always writes tabs.
DELIMITERS = (',', ';', '	', '|')


def rows_from_text(text):
    """-> [[cell, ...], ...], working out the separator from the text itself.

    The file path used to assume a comma while the paste path looked for
    tabs, so a semicolon or tab separated FILE came back as one cell per
    line and every row was reported as "needs at least four columns" -- for
    a list that was filled in perfectly.

    Chosen on the line that yields the most columns rather than on counts of
    the character, so a comma inside a name cannot outvote the real
    separator.
    """
    lines = [l for l in text.splitlines() if l.strip()][:12]
    if not lines:
        return []
    best, best_cols = ',', 0
    for d in DELIMITERS:
        try:
            cols = max(len(r) for r in csv.reader(lines, delimiter=d))
        except csv.Error:
            continue
        if cols > best_cols:
            best, best_cols = d, cols
    return list(csv.reader(_csv_stream(text), delimiter=best))


# A .xlsx is a zip, and every zip starts with these four bytes. Decided on
# the content rather than the name: a workbook renamed .csv is still a
# workbook, and -- the bug this replaces -- an .xlsx that openpyxl stumbled on
# used to fall through to the text reader, which duly parsed the compressed
# bytes and reported every line of a correctly filled list as malformed.
XLSX = b'PK'


def _read_workbook(f):
    """-> [[cell, ...], ...] from an uploaded .xlsx or .csv.

    Read by position, not by header name: the file people fill in is the one
    we generated and its columns are in a known order.
    """
    f.seek(0)
    if f.read(4) == XLSX:
        f.seek(0)
        # Deliberately not caught here. A workbook we cannot open is a thing
        # to say out loud, with the reason; the caller turns it into one
        # message. Guessing at it as text is what produced the wall of
        # nonsense this replaces.
        wb = openpyxl.load_workbook(f, data_only=True, read_only=True)
        out = []
        for row in wb.worksheets[0].iter_rows(values_only=True):
            cells = ['' if v is None else str(v) for v in row]
            if any(c.strip() for c in cells):
                out.append(cells)
        return out

    f.seek(0)
    # utf-8-sig, because Excel's "CSV UTF-8" writes a byte order mark and it
    # lands on the first cell -- the header row then stops looking like one.
    data = f.read()
    if isinstance(data, bytes):
        data = data.decode('utf-8-sig', errors='replace')
    return rows_from_text(data)


class SalesRecipientsView(SalesIQView):
    """The list, and what it does and does not cover."""

    def get(self, request):
        snap = _latest()
        rows = _rows(snap)
        regions = [r.region for r in rows if r.region]
        seen = list(dict.fromkeys(regions))

        out, covered = [], set()
        for rec in ReportRecipient.objects.all():
            j = recipient_json(rec)
            if rec.role == ReportRecipient.ROLE_HEAD:
                hits = candidates(rec.head_key, rows)
                row = hits[0] if len(hits) == 1 else None
                j['matched'] = bool(row)
                j['matched_to'] = f'{row.region} — {row.head_name}' if row else None
                # Said, not swallowed. A key naming two people used to resolve
                # to the first of them, so the second was sent somebody else's
                # numbers and the screen showed nothing wrong.
                j['ambiguous'] = ([h.head_name for h in hits]
                                  if len(hits) > 1 else None)
                if row:
                    covered.add(row.id)
            else:
                j['matched'] = True
                j['covers'] = rec.covered_regions(seen)
            out.append(j)

        # Said plainly rather than left to be noticed: a head with nobody
        # against them is a head whose report is built every morning and sent
        # to no one.
        missing = [{'region': r.region, 'head_name': r.head_name,
                    'head_code': r.head_code, 'key': unique_key(r, rows)}
                   for r in rows if r.id not in covered]

        return Response({
            'recipients': out,
            'regions': seen,
            'heads_without_a_recipient': missing,
            'snapshot': {'id': snap.id,
                         'as_of_date': snap.as_of_date.isoformat() if snap and snap.as_of_date else None,
                         'row_count': snap.row_count} if snap else None,
        })


class SalesRecipientsEditView(SalesIQAdminView):
    """Add, change and remove. Whoever loads the file keeps the list."""

    def post(self, request):
        d = request.data or {}
        email = (d.get('email') or '').strip().lower()
        if not email or '@' not in email:
            return Response({'error': 'A real email address is needed.'}, status=400)

        role = d.get('role') or ReportRecipient.ROLE_HEAD
        if role not in dict(ReportRecipient.ROLES):
            return Response({'error': f'Unknown role "{role}".'}, status=400)

        head_key = (d.get('head_key') or '').strip()
        if role == ReportRecipient.ROLE_HEAD and not head_key:
            return Response({'error': 'A head needs an APIS ID or a region, so the '
                                      'report knows whose territory to build.'},
                            status=400)

        rec, made = ReportRecipient.objects.update_or_create(
            email=email, role=role, head_key=head_key,
            defaults={'name': (d.get('name') or '').strip()[:200],
                      'regions': d.get('regions') or [],
                      'covers_all': bool(d.get('covers_all')),
                      'note': (d.get('note') or '').strip()[:300],
                      'is_active': d.get('is_active', True)})
        return Response(recipient_json(rec), status=201 if made else 200)

    def patch(self, request, pk):
        rec = ReportRecipient.objects.filter(id=pk).first()
        if not rec:
            return Response({'error': 'Not on the list.'}, status=404)
        for f in ('name', 'note'):
            if f in request.data:
                setattr(rec, f, (request.data[f] or '').strip())
        for f in ('is_active', 'covers_all'):
            if f in request.data:
                setattr(rec, f, bool(request.data[f]))
        if 'regions' in request.data:
            rec.regions = request.data['regions'] or []
        rec.save()
        return Response(recipient_json(rec))

    def delete(self, request, pk=None):
        if pk is None:
            # Clearing the whole list is the owner's call, not an uploader's.
            self.require_owner(request)
            n, _ = ReportRecipient.objects.all().delete()
            return Response({'deleted': n})
        n, _ = ReportRecipient.objects.filter(id=pk).delete()
        if not n:
            return Response({'error': 'Not on the list.'}, status=404)
        return Response({'deleted': pk})


class SalesRecipientsTemplateView(SalesIQView):
    """The list to fill in, as a workbook.

    Generated from the sales sheet rather than typed out, so the keys the
    reports are actually built on are already in it and cannot be mistyped.
    See recipients_sheet for why it is a workbook and not a CSV.
    """

    def get(self, request):
        snap = _latest()
        existing = list(ReportRecipient.objects.all())
        heads = SHEET.heads_for(
            _rows(snap),
            [r for r in existing if r.role == ReportRecipient.ROLE_HEAD])
        managers = [r for r in existing
                    if r.role == ReportRecipient.ROLE_MANAGER]

        buf = io.BytesIO()
        SHEET.build(heads, managers).save(buf)
        buf.seek(0)
        stamp = (snap.as_of_date.isoformat() if snap and snap.as_of_date
                 else 'latest')
        out = HttpResponse(
            buf.read(),
            content_type='application/vnd.openxmlformats-officedocument.'
                         'spreadsheetml.sheet')
        out['Content-Disposition'] = (
            f'attachment; filename="report recipients {stamp}.xlsx"')
        return out


class SalesRecipientsImportView(SalesIQAdminView):
    """Paste the filled-in CSV back.

    Reported line by line rather than accepted or refused as a block: one bad
    address in sixteen should not throw away the other fifteen, and a silent
    partial import is worse than either.
    """

    def post(self, request):
        f = request.FILES.get('file')
        if f:
            try:
                lines = _read_workbook(f)
            except Exception as e:
                return Response({'error': f'Cannot read that file: {e}'},
                                status=400)
            if not lines:
                return Response({'error': 'That file has no rows in it.'},
                                status=400)
        else:
            text = request.data.get('text') or ''
            if not text.strip():
                return Response({'error': 'Nothing was uploaded or pasted.'},
                                status=400)
            lines = rows_from_text(text)

        snap = _latest()
        rows = _rows(snap)
        known = list(dict.fromkeys(r.region for r in rows if r.region))

        added, updated, skipped, shape = 0, 0, [], []
        for n, line in enumerate(lines, start=1):
            cells = [c.strip() for c in line]
            if not any(cells) or cells[0].startswith('#'):
                continue
            if cells[0].lower() in ('role', 'r'):
                continue                      # the header row
            if len(cells) < 4:
                shape.append(n)
                continue

            # Two shapes are accepted. The workbook has one column per job --
            # role, key, region, name, email, regions_covered -- because a
            # column that means two things is how a manager ends up with an
            # APIS ID in their coverage. The older four-column CSV is still
            # read, so a list filled in before this change is not wasted.
            if len(cells) >= 6:
                role, key, _region, name = (cells[0].lower(), cells[1],
                                            cells[2], cells[3])
                email, covered = cells[4].lower(), cells[5]
            else:
                role, key, name = cells[0].lower(), cells[1], cells[2]
                email, covered = cells[3].lower(), cells[1]

            # The role decides whether this is a data row at all, so it is
            # checked before the address. A row with neither is a spare line
            # somebody did not use, and saying nothing is the right answer.
            if role not in ('head', 'manager'):
                # But a row carrying an address is somebody's real line with
                # the role mistyped, and that has to be said.
                if '@' in ' '.join(cells):
                    skipped.append(f'line {n}: role must be head or manager, '
                                   f'not "{cells[0]}"')
                continue
            if not email:
                continue                      # not filled in yet, not an error
            if '@' not in email:
                skipped.append(f'line {n}: "{email}" is not an email address')
                continue

            if role == 'manager':
                if not covered.strip():
                    skipped.append(f'line {n}: {name or email} covers no '
                                   f'territory. Put their regions in '
                                   f'regions_covered, or the word ALL.')
                    continue
                all_of_it = covered.strip().upper() == 'ALL'
                regions, unknown = ([], []) if all_of_it else regions_from_cell(
                    covered, known)
                if unknown:
                    skipped.append(
                        f'line {n}: {name or email} — no region named '
                        + ', '.join('"%s"' % u for u in unknown[:3])
                        + (' and %d more' % (len(unknown) - 3)
                           if len(unknown) > 3 else '')
                        + ' on the latest sheet. It carries: '
                        + ', '.join(known[:14])
                        + ('…' if len(known) > 14 else '') + '.')
                    continue
                if not regions and not all_of_it:
                    skipped.append(f'line {n}: {name or email} covers no '
                                   f'territory.')
                    continue
                _, made = ReportRecipient.objects.update_or_create(
                    email=email, role='manager', head_key='',
                    defaults={'name': name, 'regions': regions,
                              'covers_all': all_of_it,
                              'is_active': True})
            else:
                hits = candidates(key, rows)
                if len(hits) > 1:
                    skipped.append(
                        f'line {n}: "{key}" is on {len(hits)} rows of the '
                        f'sheet (' + ', '.join(h.head_name for h in hits)
                        + '), so it does not say who this is. Download the '
                          'list again — it now carries a key that names one '
                          'row only.')
                    continue
                if not hits:
                    skipped.append(f'line {n}: no head matching "{key}" '
                                   f'on the latest sheet')
                    continue
                _, made = ReportRecipient.objects.update_or_create(
                    email=email, role='head', head_key=key,
                    defaults={'name': name, 'is_active': True})
            added, updated = added + bool(made), updated + (not made)

        # Every row coming out the wrong shape is not sixteen mistakes, it
        # is one: the file was not read the way it was written. Said once,
        # rather than left to be inferred from a wall of identical lines.
        if shape and not added and not updated:
            return Response({
                'error': 'That file did not come through as a table — none of '
                         'its rows had the six columns the list carries. '
                         'Upload it as the .xlsx you downloaded.',
                'added': 0, 'updated': 0, 'skipped': []}, status=400)
        if shape:
            skipped.append(str(len(shape)) + ' row(s) were not in the right '
                           'shape and were left alone: line '
                           + ', '.join(str(x) for x in shape[:8]))

        return Response({'added': added, 'updated': updated, 'skipped': skipped})


# -- upload access --------------------------------------------------------
class SalesUploaderView(SalesIQView):
    """Who may load the morning workbook."""

    def get(self, request):
        from .auth import SALESIQ_SUPER_ADMIN
        return Response({
            'owner': SALESIQ_SUPER_ADMIN,
            'you': {'email': getattr(request, 'salesiq_email', ''),
                    'role': getattr(request, 'salesiq_role', '')},
            'uploaders': [
                {'id': g.id, 'email': g.email, 'name': g.name,
                 'granted_by': g.granted_by, 'is_active': g.is_active,
                 'created_at': g.created_at.isoformat()}
                for g in UploaderGrant.objects.all()],
        })


class SalesUploaderEditView(SalesIQOwnerView):
    """Granting and revoking stay with the one owner.

    An uploader who could grant upload access could grant it to anybody,
    which makes the distinction between the two roles decorative.
    """

    def post(self, request):
        email = (request.data.get('email') or '').strip().lower()
        if '@' not in email:
            return Response({'error': 'A real email address is needed.'}, status=400)
        g, made = UploaderGrant.objects.update_or_create(
            email=email,
            defaults={'name': (request.data.get('name') or '').strip()[:200],
                      'granted_by': getattr(request, 'salesiq_email', ''),
                      'is_active': True})
        return Response({'id': g.id, 'email': g.email, 'created': made},
                        status=201 if made else 200)

    def delete(self, request, pk):
        n, _ = UploaderGrant.objects.filter(id=pk).delete()
        if not n:
            return Response({'error': 'Not on the list.'}, status=404)
        return Response({'deleted': pk})


# -- the manager's rolled-up report ---------------------------------------
class SalesTeamReportView(SalesIQView):
    """One report across several territories.

    Figures are added from the head rows rather than read off the sheet's own
    GT Total line, because a manager covering four of twelve regions has no
    subtotal on the sheet at all -- and taking the one that is there would
    quietly hand them the whole channel.
    """

    def get(self, request):
        snap = _latest()
        if not snap:
            return Response({'error': 'No review sheet has been uploaded yet.'},
                            status=404)

        wanted = [r.strip() for r in
                  (request.query_params.get('regions') or '').split(',') if r.strip()]
        rows = _rows(snap)
        mine = [r for r in rows if r.region in wanted] if wanted else rows
        if not mine:
            return Response({'error': 'None of those regions are on the latest sheet.'},
                            status=404)

        def total(attr):
            return round(sum(float(getattr(r, attr)) for r in mine), 2)

        from .review import row_json, snapshot_json, fy_index
        idx = fy_index(snap.as_of_month)
        remaining = (12 - idx) if idx else 0
        fy_backlog = total('fy_target') - total('fy_actual')

        return Response({
            'snapshot': snapshot_json(snap),
            'name': request.query_params.get('name') or '',
            'regions': [r.region for r in mine],
            'heads': [row_json(r) for r in mine],
            'totals': {
                'sfo_count': sum(r.sfo_count for r in mine),
                'lmtd': total('lmtd'), 'month_target': total('month_target'),
                'mtd_primary': total('mtd_primary'),
                'mtd_secondary': total('mtd_secondary'),
                'yesterday_billing': total('yesterday_billing'),
                'ytd_target': total('ytd_target'), 'ytd_actual': total('ytd_actual'),
                'fy_target': total('fy_target'), 'fy_actual': total('fy_actual'),
                'month_pct': (round(total('mtd_primary') / total('month_target') * 100, 1)
                              if total('month_target') else None),
                'ytd_pct': (round(total('ytd_actual') / total('ytd_target') * 100, 1)
                            if total('ytd_target') else None),
                'fy_pct': (round(total('fy_actual') / total('fy_target') * 100, 1)
                           if total('fy_target') else None),
                'growth_pct': (round((total('mtd_primary') / total('lmtd') - 1) * 100, 1)
                               if total('lmtd') else None),
                'month_backlog': round(total('month_target') - total('mtd_primary'), 2),
                'ytd_backlog': round(total('ytd_target') - total('ytd_actual'), 2),
                'fy_backlog': round(fy_backlog, 2),
            },
            'year': {'months_elapsed': idx or 0, 'months_remaining': remaining,
                     'required_monthly': (round(fy_backlog / remaining, 2)
                                          if remaining else None)},
        })


class SalesTeamReportHtmlView(SalesIQView):
    def get(self, request):
        resp = SalesTeamReportView.as_view()(request._request)
        if resp.status_code != 200:
            return HttpResponse(resp.data.get('error', 'Not found'),
                                status=resp.status_code, content_type='text/plain')
        html = REPORT.render_team(resp.data)
        out = HttpResponse(html, content_type='text/html; charset=utf-8')
        if request.query_params.get('download'):
            snap = resp.data['snapshot']
            stamp = snap.get('as_of_date') or 'latest'
            name = REPORT._safe(resp.data.get('name') or 'team')
            out['Content-Disposition'] = f'attachment; filename="{name}_{stamp}.html"'
        return out


class SalesMailPreviewView(SalesIQView):
    """The mail as it will arrive, for one recipient.

    Rendered rather than described: the table inside it is cut to the reader,
    so the only way to be sure somebody is not about to be shown another
    territory is to look at the one they will get.
    """

    def get(self, request):
        rid = request.query_params.get('recipient')
        rec = ReportRecipient.objects.filter(id=rid).first() if rid else None
        if not rec:
            return HttpResponse('No such recipient.', status=404,
                                content_type='text/plain')

        req = request._request
        req.GET = req.GET.copy()

        if rec.role == ReportRecipient.ROLE_HEAD:
            req.GET['head'] = rec.head_key
            from .review import SalesReviewReportView
            resp = SalesReviewReportView.as_view()(req)
            if resp.status_code != 200:
                return HttpResponse(resp.data.get('error', 'Not found'),
                                    status=resp.status_code,
                                    content_type='text/plain')
            subject, html = MAIL.for_head(resp.data)
        else:
            snap = _latest()
            covers = rec.covered_regions(
                list(dict.fromkeys(r.region for r in _rows(snap) if r.region)))
            if not covers:
                return HttpResponse(
                    'No regions are set against this manager yet, so there is '
                    'nothing to send them. An empty coverage list means no '
                    'territories, never all of them.',
                    status=400, content_type='text/plain')
            req.GET['regions'] = ','.join(covers)
            req.GET['name'] = rec.name or 'Group'
            resp = SalesTeamReportView.as_view()(req)
            if resp.status_code != 200:
                return HttpResponse(resp.data.get('error', 'Not found'),
                                    status=resp.status_code,
                                    content_type='text/plain')
            subject, html = MAIL.for_manager(resp.data)

        # The subject shown above the body rather than only in a header, so
        # what is being checked is the whole of what arrives.
        head = (
            '<div style="font-family:Calibri,Arial,sans-serif;font-size:12px;'
            'border:1px solid #D8DEE7;background:#F6F8FB;padding:10px 12px;'
            'margin-bottom:14px">'
            '<b>To</b> ' + REPORT.e(rec.email) + '<br>'
            '<b>Subject</b> ' + REPORT.e(subject) + '</div>')
        return HttpResponse(head + html,
                            content_type='text/html; charset=utf-8')
