"""Reading the daily review sheet back out.

Two endpoints: the list of snapshots that have been uploaded, and one head's
report. Everything the report shows is computed here rather than in the
browser, so the screen, the PDF and the email are all reading one answer
instead of three implementations of it.
"""
import io
import zipfile
from datetime import date

from django.http import HttpResponse
from rest_framework.response import Response

from .auth import SalesIQView, SalesIQAdminView
from ..models import ReviewSnapshot, ReviewRow
from .. import report as REPORT


# The financial year opens in April, so April is month 1 and March is month 12.
FY_START_MONTH = 4


def fy_index(d):
    """-> 1..12 for a month within the April-March year, or None."""
    if not d:
        return None
    return (d.month - FY_START_MONTH) % 12 + 1


def _money(v):
    return round(float(v or 0), 2)


def row_json(r):
    return {
        'id': r.id,
        'channel': r.channel, 'region': r.region,
        'head_name': r.head_name, 'head_code': r.head_code,
        'sfo_count': r.sfo_count,
        'lmtd': _money(r.lmtd),
        'month_target': _money(r.month_target),
        'yesterday_billing': _money(r.yesterday_billing),
        'mtd_primary': _money(r.mtd_primary),
        'mtd_secondary': _money(r.mtd_secondary),
        'ytd_target': _money(r.ytd_target), 'ytd_actual': _money(r.ytd_actual),
        'fy_target': _money(r.fy_target), 'fy_actual': _money(r.fy_actual),
        # Recomputed, never read off the sheet -- see the note on ReviewRow.
        'month_pct': r.month_pct, 'ytd_pct': r.ytd_pct, 'fy_pct': r.fy_pct,
        'growth_pct': r.growth_pct,
        'month_backlog': _money(r.month_backlog),
        'ytd_backlog': _money(r.ytd_backlog),
        'fy_backlog': _money(r.fy_backlog),
        'is_total': r.is_total,
    }


def snapshot_json(s):
    return {
        'id': s.id, 'filename': s.filename, 'sheet_name': s.sheet_name,
        'uploaded_by': s.uploaded_by,
        'as_of_month': s.as_of_month.strftime('%Y-%m') if s.as_of_month else None,
        'as_of_month_label': s.as_of_month.strftime('%b %Y') if s.as_of_month else None,
        'as_of_date': s.as_of_date.isoformat() if s.as_of_date else None,
        'source_unit': s.source_unit,
        'row_count': s.row_count,
        'warnings': s.warnings, 'notes': s.notes,
        'created_at': s.created_at.isoformat(),
    }


def _pick(request):
    """The snapshot asked for, or the most recent one."""
    sid = request.query_params.get('snapshot')
    qs = ReviewSnapshot.objects.all()
    if sid:
        return qs.filter(id=sid).first()
    return qs.first()          # Meta.ordering puts the newest first


class SalesReviewView(SalesIQView):
    """The snapshots on file, and one snapshot's rows."""

    def get(self, request):
        snaps = list(ReviewSnapshot.objects.all()[:60])
        out = {'snapshots': [snapshot_json(s) for s in snaps]}

        snap = _pick(request)
        if snap:
            rows = list(snap.rows.all())
            out['snapshot'] = snapshot_json(snap)
            out['rows'] = [row_json(r) for r in rows if not r.is_total]
            out['totals'] = [row_json(r) for r in rows if r.is_total]
        return Response(out)


class SalesReviewDeleteView(SalesIQAdminView):
    def delete(self, request, pk):
        snap = ReviewSnapshot.objects.filter(id=pk).first()
        if not snap:
            return Response({'error': 'That snapshot is not on file.'}, status=404)
        snap.delete()
        return Response({'deleted': pk})


class SalesReviewReportView(SalesIQView):
    """Everything one head's report needs, in one call.

    Ranks and averages are taken within the head's own channel. A GT head
    measured against the whole sheet would be compared with E-COM, whose plan
    is phased differently and whose field force is zero -- a comparison that
    reads as a judgement and means nothing.
    """

    def get(self, request):
        snap = _pick(request)
        if not snap:
            return Response({'error': 'No review sheet has been uploaded yet.'},
                            status=404)

        rows = [r for r in snap.rows.all() if not r.is_total]
        key = (request.query_params.get('head') or '').strip()
        if not key:
            return Response({'error': 'Which head? Pass ?head= an APIS ID, a '
                                      'region code, or the name on the sheet.'},
                            status=400)

        low = key.lower()
        me = next((r for r in rows
                   if r.head_code.lower() == low
                   or r.region.lower() == low
                   or r.head_name.lower() == low), None)
        if me is None:
            return Response({'error': f'No row for "{key}" on this sheet.'}, status=404)

        peers = [r for r in rows if r.channel == me.channel]
        totals = {r.channel: r for r in snap.rows.filter(is_total=True)}

        def rank(attr):
            """Where this head sits, best first. Heads with no figure at all
            are left out rather than ranked last -- a territory with no plan
            has not come bottom, it has not been measured."""
            vals = [(r, getattr(r, attr)) for r in peers]
            vals = [(r, v) for r, v in vals if v is not None]
            vals.sort(key=lambda t: t[1], reverse=True)
            for i, (r, _) in enumerate(vals, start=1):
                if r.id == me.id:
                    return {'position': i, 'of': len(vals)}
            return None

        # Where the year stands. The sheet carries only the month, so the
        # count of months behind and ahead comes from the fiscal calendar.
        idx = fy_index(snap.as_of_month)
        elapsed = idx or 0
        remaining = (12 - elapsed) if idx else 0
        prior_months = max(elapsed - 1, 0)
        prior_actual = float(me.ytd_actual) - float(me.mtd_primary)
        prior_target = float(me.ytd_target) - float(me.month_target)

        def per(v, n):
            return round(v / n, 2) if n else None

        channel_total = totals.get(me.channel)
        peer_sfo = sum(r.sfo_count for r in peers)
        peer_mtd = sum(float(r.mtd_primary) for r in peers)
        peer_ytd = sum(float(r.ytd_actual) for r in peers)

        return Response({
            'snapshot': snapshot_json(snap),
            'head': row_json(me),
            'channel_total': row_json(channel_total) if channel_total else None,
            'peers': [row_json(r) for r in peers],
            'rank': {
                'month_pct': rank('month_pct'),
                'ytd_pct': rank('ytd_pct'),
                'growth_pct': rank('growth_pct'),
            },
            'year': {
                'months_elapsed': elapsed,
                'months_remaining': remaining,
                'prior_months': prior_months,
                # April to the month before this one: the base the current
                # month's growth is actually a step up from.
                'prior_actual': round(prior_actual, 2),
                'prior_target': round(prior_target, 2),
                'prior_monthly_avg': per(prior_actual, prior_months),
                'prior_pct': (round(prior_actual / prior_target * 100, 1)
                              if prior_target else None),
                # What the rest of the year has to run at to close the annual
                # plan. The single most useful number on the sheet, and the
                # one it does not print.
                'required_monthly': per(me.fy_backlog, remaining),
                # The annual backlog splits into two very different things,
                # and the sheet prints only their sum. One is the plan the
                # months ahead were always going to carry; the other is
                # catching up on months already closed. A head who is told
                # only the total cannot tell which of the two is the problem.
                'plan_ahead': round(float(me.fy_target) - float(me.ytd_target), 2),
                'catch_up': round(me.ytd_backlog, 2),
                'required_base': per(float(me.fy_target) - float(me.ytd_target),
                                     remaining),
                'required_catch_up': per(me.ytd_backlog, remaining),
                'required_vs_month_target': (
                    round((me.fy_backlog / remaining) / float(me.month_target), 2)
                    if remaining and float(me.month_target) else None),
                'required_vs_current': (
                    round((me.fy_backlog / remaining) / float(me.mtd_primary), 2)
                    if remaining and float(me.mtd_primary) else None),
                'plan_due_by_now_pct': (
                    round(float(me.ytd_target) / float(me.fy_target) * 100, 1)
                    if float(me.fy_target) else None),
            },
            'productivity': {
                'per_sfo_mtd': per(float(me.mtd_primary), me.sfo_count),
                'per_sfo_ytd': per(float(me.ytd_actual), me.sfo_count),
                'channel_per_sfo_mtd': per(peer_mtd, peer_sfo),
                'channel_per_sfo_ytd': per(peer_ytd, peer_sfo),
                'sfo_count': me.sfo_count,
            },
            'flow': {
                # Secondary below primary means stock is building at the
                # distributor; above means the trade is pulling down stock
                # loaded earlier. Neither is good or bad on its own, which is
                # why it is reported as a ratio and not scored.
                'secondary_to_primary': (
                    round(float(me.mtd_secondary) / float(me.mtd_primary) * 100, 1)
                    if float(me.mtd_primary) else None),
                'channel_secondary_to_primary': (
                    round(sum(float(r.mtd_secondary) for r in peers) / peer_mtd * 100, 1)
                    if peer_mtd else None),
                # At yesterday's billing, how many more days clear the month's
                # backlog. Derived only from figures the sheet prints.
                'days_to_clear': (
                    round(me.month_backlog / float(me.yesterday_billing), 1)
                    if float(me.yesterday_billing) > 0 and me.month_backlog > 0 else None),
                'yesterday_billing': _money(me.yesterday_billing),
            },
            'share': {
                'of_channel_mtd': (round(float(me.mtd_primary) / peer_mtd * 100, 1)
                                   if peer_mtd else None),
                'of_channel_ytd': (round(float(me.ytd_actual) / peer_ytd * 100, 1)
                                   if peer_ytd else None),
            },
        })


class SalesReviewReportHtmlView(SalesIQView):
    """One head's report as a standalone HTML file.

    Self-contained: no external stylesheet, no script, no image. It has to
    survive being emailed, opened offline and printed, and a report that
    needs the network to render is one that arrives blank.
    """

    def get(self, request):
        snap = _pick(request)
        if not snap:
            return HttpResponse('No review sheet has been uploaded yet.',
                                status=404, content_type='text/plain')
        resp = SalesReviewReportView.as_view()(request._request)
        data = resp.data
        if resp.status_code != 200:
            return HttpResponse(data.get('error', 'Not found'),
                                status=resp.status_code, content_type='text/plain')

        html = REPORT.render(data)
        out = HttpResponse(html, content_type='text/html; charset=utf-8')
        if request.query_params.get('download'):
            stamp = snap.as_of_date.isoformat() if snap.as_of_date else 'latest'
            fn = f"{REPORT._safe(data['head']['region'])}_{REPORT._safe(data['head']['head_name'])}_{stamp}.html"
            out['Content-Disposition'] = f'attachment; filename="{fn}"'
        return out


class SalesReviewReportBundleView(SalesIQAdminView):
    """Every head's report, zipped.

    One file per head rather than one document with everybody in it: these
    are sent to individuals, and a head who can read the whole sheet is being
    shown their peers' numbers whether or not that was intended.
    """

    def get(self, request):
        snap = _pick(request)
        if not snap:
            return HttpResponse('No review sheet has been uploaded yet.',
                                status=404, content_type='text/plain')

        rows = [r for r in snap.rows.all() if not r.is_total]
        if not rows:
            return HttpResponse('That sheet has no head rows on it.',
                                status=400, content_type='text/plain')

        buf = io.BytesIO()
        made, failed = 0, []
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            for row in rows:
                req = request._request
                # Each head answered through the same view the browser uses.
                req.GET = req.GET.copy()
                req.GET['head'] = row.head_code or row.region or row.head_name
                req.GET['snapshot'] = str(snap.id)
                resp = SalesReviewReportView.as_view()(req)
                if resp.status_code != 200:
                    failed.append(row.head_name or row.region)
                    continue
                stamp = snap.as_of_date.isoformat() if snap.as_of_date else 'latest'
                z.writestr(f'{REPORT._safe(row.region)}_{REPORT._safe(row.head_name)}_{stamp}.html',
                           REPORT.render(resp.data))
                made += 1
            if failed:
                # Said in the zip rather than only in a log, because a bundle
                # quietly one report short is one that gets sent that way.
                z.writestr('NOT-INCLUDED.txt',
                           'No report could be built for:\n  '
                           + '\n  '.join(failed))

        buf.seek(0)
        stamp = snap.as_of_date.isoformat() if snap.as_of_date else 'latest'
        out = HttpResponse(buf.read(), content_type='application/zip')
        out['Content-Disposition'] = f'attachment; filename="reports_{stamp}.zip"'
        out['X-Reports-Built'] = str(made)
        return out
