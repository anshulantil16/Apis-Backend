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


def match_head(key, rows):
    """The row a recipient's key points at.

    APIS ID first, then region, then name. An ID because a name is not an
    identity -- the same person is spelled several ways across a year of
    exports, and a report addressed to a spelling goes to nobody.
    """
    low = (key or '').strip().lower()
    if not low:
        return None
    for attr in ('head_code', 'region', 'head_name'):
        for r in rows:
            if (getattr(r, attr) or '').strip().lower() == low:
                return r
    return None


def _read_workbook(f):
    """-> [[cell, ...], ...] from an uploaded .xlsx, or from a .csv.

    Read as rows of text rather than by header name on purpose: the file
    people fill in is the one we generated, its columns are in a known order,
    and matching by position means a renamed header does not silently drop a
    column. Anything that is not in that order fails loudly in the loop
    below, line by line, which is the behaviour we want anyway.
    """
    name = (getattr(f, 'name', '') or '').lower()
    if name.endswith('.csv') or name.endswith('.txt'):
        raw = f.read()
        if isinstance(raw, bytes):
            raw = raw.decode('utf-8-sig', errors='replace')
        return list(csv.reader(io.StringIO(raw)))

    wb = openpyxl.load_workbook(f, data_only=True, read_only=True)
    out = []
    # The first sheet only. The second is the instructions, and reading it
    # would report every sentence on it as a line that could not be parsed.
    ws = wb.worksheets[0]
    for row in ws.iter_rows(values_only=True):
        cells = ['' if v is None else str(v) for v in row]
        if any(c.strip() for c in cells):
            out.append(cells)
    return out


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
                row = match_head(rec.head_key, rows)
                j['matched'] = bool(row)
                j['matched_to'] = f'{row.region} — {row.head_name}' if row else None
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
                    'head_code': r.head_code}
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
            lines = list(csv.reader(io.StringIO(text)))

        snap = _latest()
        rows = _rows(snap)
        known = list(dict.fromkeys(r.region for r in rows if r.region))

        added, updated, skipped = 0, 0, []
        for n, line in enumerate(lines, start=1):
            cells = [c.strip() for c in line]
            if not any(cells) or cells[0].startswith('#'):
                continue
            if cells[0].lower() in ('role', 'r'):
                continue                      # the header row
            if len(cells) < 4:
                skipped.append(f'line {n}: needs at least four columns')
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
            # checked FIRST. The workbook carries a title, a row of hints and
            # a section heading above each block; the hint row begins "head
            # or manager", so an email check that ran first reported its
            # "WHERE TO SEND IT" cell as a bad address.
            if role not in ('head', 'manager'):
                # Furniture is skipped in silence. A row carrying an address
                # is somebody's real line with the role mistyped, and that
                # has to be said.
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
                regions = [] if covered.upper() == 'ALL' else [
                    p.strip() for p in covered.split(';') if p.strip()]
                unknown = [r for r in regions if r not in known]
                if unknown:
                    skipped.append(f'line {n}: no region named '
                                   + ', '.join(unknown[:3]) + ' on the latest sheet')
                    continue
                _, made = ReportRecipient.objects.update_or_create(
                    email=email, role='manager', head_key='',
                    defaults={'name': name, 'regions': regions,
                              'covers_all': covered.upper() == 'ALL',
                              'is_active': True})
            else:
                if not match_head(key, rows):
                    skipped.append(f'line {n}: no head matching "{key}" '
                                   f'on the latest sheet')
                    continue
                _, made = ReportRecipient.objects.update_or_create(
                    email=email, role='head', head_key=key,
                    defaults={'name': name, 'is_active': True})
            added, updated = added + bool(made), updated + (not made)

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
