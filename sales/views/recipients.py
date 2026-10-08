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

from django.http import HttpResponse
from rest_framework.response import Response

from .auth import SalesIQView, SalesIQAdminView, SalesIQOwnerView
from ..models import ReportRecipient, UploaderGrant, ReviewSnapshot
from .. import report as REPORT
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
    """A CSV with every head on the latest sheet and an empty email column.

    Filled in and pasted back, this is the whole of setting the list up. It
    is generated from the sheet rather than typed out, so the APIS IDs in it
    are the ones the reports are actually keyed on and cannot be mistyped.
    """

    def get(self, request):
        """The list, with the table FIRST.

        An earlier version opened with fifteen lines of instructions, which in
        Excel is fifteen rows of text spilling across empty columns before the
        header -- it reads as a broken file rather than as a form. The table
        comes first now and the notes sit under it, where they can be read
        without being in the way.
        """
        snap = _latest()
        rows = _rows(snap)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(['role', 'key_or_regions', 'name', 'email'])

        for r in rows:
            rec = ReportRecipient.objects.filter(
                role=ReportRecipient.ROLE_HEAD,
                head_key__in=[k for k in (r.head_code, r.region) if k]).first()
            w.writerow(['head', r.head_code or r.region, r.head_name,
                        rec.email if rec else ''])

        if not rows:
            # Said in the file itself. An empty list downloaded with no
            # explanation reads as the feature being broken, when what has
            # actually happened is that the Region Summary tab has not been
            # loaded -- and that is fixable in one upload.
            w.writerow([])
            w.writerow(['# NO HEADS YET. The Region Summary tab has not been '
                        'uploaded, so there is nothing to list here.'])
            w.writerow(['# Upload the daily workbook with that tab in it and '
                        'download this again -- every head will be filled in.'])

        w.writerow([])
        existing = list(ReportRecipient.objects.filter(
            role=ReportRecipient.ROLE_MANAGER))
        for rec in existing:
            w.writerow(['manager',
                        'ALL' if rec.covers_all else ';'.join(rec.regions or []),
                        rec.name, rec.email])
        for _ in range(max(6 - len(existing), 2)):
            w.writerow(['manager', '', '', ''])

        for line in (
                '',
                '# ---- how to fill this in ----',
                '# head     gets their own territory only. Leave column 2 as '
                'it is: it is the key their report is built from.',
                '# manager  gets one report across the territories in column '
                '2, plus each of those heads own files.',
                '#          Put their regions in column 2 separated by '
                'SEMICOLONS (GTR01;GTR02), or the word ALL.',
                '#',
                '# Fill in the email column and paste the whole file back into '
                'SalesIQ. Nothing is guessed from it.',
                '# A row with no email is simply not set up yet, so this can '
                'be done a few at a time.'):
            w.writerow([line])

        out = HttpResponse(buf.getvalue(), content_type='text/csv; charset=utf-8')
        out['Content-Disposition'] = 'attachment; filename="report_recipients.csv"'
        return out


class SalesRecipientsImportView(SalesIQAdminView):
    """Paste the filled-in CSV back.

    Reported line by line rather than accepted or refused as a block: one bad
    address in sixteen should not throw away the other fifteen, and a silent
    partial import is worse than either.
    """

    def post(self, request):
        text = request.data.get('text') or ''
        if not text.strip():
            return Response({'error': 'Nothing was pasted.'}, status=400)

        snap = _latest()
        rows = _rows(snap)
        known = list(dict.fromkeys(r.region for r in rows if r.region))

        added, updated, skipped = 0, 0, []
        reader = csv.reader(io.StringIO(text))
        for n, line in enumerate(reader, start=1):
            cells = [c.strip() for c in line]
            if not any(cells) or cells[0].startswith('#'):
                continue
            if cells[0].lower() in ('role', 'r'):
                continue                      # the header row
            if len(cells) < 4:
                skipped.append(f'line {n}: needs four columns')
                continue

            role, key, name, email = cells[0].lower(), cells[1], cells[2], cells[3].lower()
            if not email:
                continue                      # not filled in yet, not an error
            if '@' not in email:
                skipped.append(f'line {n}: "{email}" is not an email address')
                continue
            if role not in ('head', 'manager'):
                skipped.append(f'line {n}: role must be head or manager')
                continue

            if role == 'manager':
                regions = [] if key.upper() == 'ALL' else [
                    p.strip() for p in key.split(';') if p.strip()]
                unknown = [r for r in regions if r not in known]
                if unknown:
                    skipped.append(f'line {n}: no region named '
                                   + ', '.join(unknown[:3]) + ' on the latest sheet')
                    continue
                _, made = ReportRecipient.objects.update_or_create(
                    email=email, role='manager', head_key='',
                    defaults={'name': name, 'regions': regions,
                              'covers_all': key.upper() == 'ALL',
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
