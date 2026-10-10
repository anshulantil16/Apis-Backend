"""Sending the morning mail.

Three things this deliberately does not do.

It does not decide who is on the list. That is the recipient list, filled in
by hand, and nothing here infers an address from a name.

It does not run itself. There is no scheduler behind this; somebody presses a
button having looked at what is about to go. A daily mail to the sales
leadership that fires on its own the morning after a bad upload is a worse
failure than one that did not go at all.

And it does not reach the list before it has reached you. The first action on
the screen sends the whole run to one address, every message composed exactly
as it will be -- same subject, same table cut to that reader, same
attachments -- so what is approved is the thing itself rather than a
description of it.
"""
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.http import HttpRequest, QueryDict
from django.utils import timezone
from rest_framework.response import Response

from .auth import SalesIQView, SalesIQOwnerView
from ..models import ReportRecipient, ReviewSnapshot
from .. import report as REPORT
from .. import mail as MAIL
from .. import pdf as PDF


def _rows(snap):
    return [r for r in snap.rows.all() if not r.is_total] if snap else []


def _stamp(snap):
    return snap.as_of_date.isoformat() if snap.as_of_date else 'latest'


def _attach(name, html):
    """-> (filename, body, mimetype).

    A PDF where one can be rendered, because Gmail previews a PDF and shows
    an .html file as its own source -- so the first thing a reader would see
    each morning is a wall of CSS, and only a download gets them the report.
    """
    body = PDF.render(html)
    if body is not None:
        return (name + '.pdf', body, 'application/pdf')
    return (name + '.html', html, 'text/html')


def _file_name(row, snap):
    return '{}_{}_{}'.format(REPORT._safe(row.region),
                             REPORT._safe(row.head_name), _stamp(snap))


def _sub(req, **params):
    """A GET request to one of our own views, carrying this caller's session.

    Built fresh rather than by mutating the caller's request. The send is a
    POST, and a POST handed to a view that only answers GET comes back 405 --
    which read as "report could not be built" for every single recipient.
    """
    sub = HttpRequest()
    sub.method = 'GET'
    sub.META = dict(req.META)
    sub.META['REQUEST_METHOD'] = 'GET'
    sub.path = sub.path_info = req.path
    q = QueryDict(mutable=True)
    for k, v in params.items():
        q[k] = str(v)
    sub.GET = q
    if hasattr(req, 'user'):
        sub.user = req.user
    return sub


def _one_head(req, snap, row):
    """One head's report data, asked for by row id.

    By id and not by region: a region is not unique on this sheet, and the
    handover line shares GTR04 A with the row above it.
    """
    from .review import SalesReviewReportView
    return SalesReviewReportView.as_view()(
        _sub(req, snapshot=snap.id, row=row.id))


def compose(rec, req, snap, rows):
    """-> {'subject', 'text', 'html', 'files': [(name, body)]} or {'error'}.

    Built through the same views the screen reads, so the mail, the preview
    and the report on screen are one answer rather than three implementations
    that agree until they do not.
    """
    from .recipients import for_recipient, SalesTeamReportView

    if rec.role == ReportRecipient.ROLE_HEAD:
        hits = for_recipient(rec, rows)
        if len(hits) > 1:
            return {'error': '"{}" is on {} rows of this sheet ({}), so it '
                             'does not say whose report to send. Download the '
                             'list again.'.format(
                                 rec.head_key, len(hits),
                                 ', '.join(h.head_name for h in hits))}
        if not hits:
            return {'error': 'No row matching "{}"{} on this sheet.'.format(
                rec.head_key,
                ' or "{}"'.format(rec.name) if (rec.name or '').strip() else '')}
        resp = _one_head(req, snap, hits[0])
        if resp.status_code != 200:
            return {'error': resp.data.get('error', 'Report could not be built.')}
        subject, html = MAIL.for_head(resp.data)
        _, text = REPORT.email_for(resp.data)
        return {'subject': subject, 'html': html, 'text': text,
                'files': [_attach(_file_name(hits[0], snap),
                                  REPORT.render(resp.data))]}

    covers = rec.covered_regions(
        list(dict.fromkeys(r.region for r in rows if r.region)))
    if not covers:
        return {'error': 'No territories are set against this manager, so '
                         'there is nothing to send. An empty coverage list '
                         'means no territories, never all of them.'}

    resp = SalesTeamReportView.as_view()(_sub(
        req, snapshot=snap.id, regions=','.join(covers),
        name=rec.name or 'Group'))
    if resp.status_code != 200:
        return {'error': resp.data.get('error', 'Report could not be built.')}
    subject, html = MAIL.for_manager(resp.data)
    _, text = REPORT.team_email_for(resp.data)

    # One file, covering every territory they hold. It used to be the
    # roll-up plus a separate report per head, which for a manager covering
    # the whole channel arrived as fourteen attachments -- a strip of
    # thumbnails to scroll through rather than a report to read. The roll-up
    # already carries every territory by name, with the group line under
    # them, so the other thirteen were the same figures a second time.
    files = [_attach('{}_{}'.format(REPORT._safe(rec.name or 'group'),
                                    _stamp(snap)),
                     REPORT.render_team(resp.data))]
    return {'subject': subject, 'html': html, 'text': text, 'files': files}


def _people(ids):
    qs = ReportRecipient.objects.filter(is_active=True)
    if ids:
        qs = qs.filter(id__in=ids)
    return list(qs)


def _configured():
    return bool(settings.DEFAULT_FROM_EMAIL and settings.EMAIL_HOST_PASSWORD)


class SalesMailOutboxView(SalesIQView):
    """What a send would do, without doing any of it.

    Composed for real rather than counted, because the failures worth knowing
    about -- a key that names two people, a manager covering nothing -- only
    appear when the report is actually built.
    """

    def get(self, request):
        snap = ReviewSnapshot.objects.first()
        rows = _rows(snap)
        out = []
        for rec in _people(None):
            d = {'id': rec.id, 'name': rec.name, 'email': rec.email,
                 'role': rec.role,
                 'last_sent_at': (rec.last_sent_at.isoformat()
                                  if rec.last_sent_at else None)}
            if snap:
                made = compose(rec, request._request, snap, rows)
                d['error'] = made.get('error')
                d['subject'] = made.get('subject')
                d['attachments'] = [n for n, _, _ in made.get('files', [])]
            out.append(d)
        return Response({
            'from': settings.DEFAULT_FROM_EMAIL or '',
            'configured': _configured(),
            # Said out loud. An .html attachment still works, but it shows in
            # Gmail as its own source rather than as the report.
            'attachment_format': 'pdf' if PDF.available() else 'html',
            'snapshot': ({'as_of_date': snap.as_of_date.isoformat()
                          if snap.as_of_date else None,
                          'filename': snap.filename,
                          'row_count': snap.row_count} if snap else None),
            'recipients': out,
            'ready': sum(1 for r in out if not r.get('error')),
        })


class SalesMailSendView(SalesIQOwnerView):
    """Send it. The owner's call, and nobody else's.

    `test_to` redirects every message to one address and says so in the
    subject, so a run can be read end to end before a single real inbox is
    touched. Only a real send stamps last_sent_at: a test must not be able to
    make the screen say the morning's mail has gone out.
    """

    def post(self, request):
        d = request.data or {}
        test_to = (d.get('test_to') or '').strip().lower()
        from .recipients import looks_like_an_address
        if test_to and not looks_like_an_address(test_to):
            return Response({'error': f'"{test_to}" is not an email address.'},
                            status=400)

        if not _configured():
            return Response({'error': 'No sending account is set up on this '
                                      'server, so nothing was sent. '
                                      'EMAIL_HOST_USER and EMAIL_HOST_PASSWORD '
                                      'have to be in the .env first.'},
                            status=400)
        sender = settings.DEFAULT_FROM_EMAIL

        snap = ReviewSnapshot.objects.first()
        if not snap:
            return Response({'error': 'No review sheet has been uploaded, so '
                                      'there is nothing to send.'}, status=400)
        rows = _rows(snap)

        people = _people(d.get('recipients') or None)
        if not people:
            return Response({'error': 'Nobody on the list is set up to be '
                                      'sent to.'}, status=400)

        # Composed for everybody BEFORE anything is sent. Half a run going out
        # and the rest failing on a bad key is the worst outcome available:
        # nobody can say afterwards who holds the morning's numbers.
        made, bad = [], []
        for rec in people:
            m = compose(rec, request._request, snap, rows)
            if m.get('error'):
                bad.append({'id': rec.id, 'email': rec.email,
                            'name': rec.name, 'error': m['error']})
            else:
                made.append((rec, m))
        if bad and not d.get('skip_broken'):
            return Response(
                {'error': '{} of {} could not be built, so nothing was sent. '
                          'Fix these, or send again with the rest.'.format(
                              len(bad), len(people)),
                 'sent': 0, 'failed': bad}, status=400)

        # One connection for the run. Thirty SMTP handshakes in a row is how a
        # send gets throttled half way through.
        sent, results = 0, []
        conn = get_connection(fail_silently=False)
        try:
            conn.open()
            for rec, m in made:
                subject = m['subject']
                if test_to:
                    subject = '[TEST - for {}] {}'.format(rec.email, subject)
                msg = EmailMultiAlternatives(
                    subject=subject, body=m['text'], from_email=sender,
                    to=[test_to or rec.email], connection=conn)
                msg.attach_alternative(m['html'], 'text/html')
                for name, body, mime in m['files']:
                    msg.attach(name, body, mime)
                try:
                    msg.send()
                except Exception as e:
                    results.append({'id': rec.id, 'email': rec.email,
                                    'name': rec.name, 'ok': False,
                                    'error': str(e)})
                    continue
                if not test_to:
                    rec.last_sent_at = timezone.now()
                    rec.save(update_fields=['last_sent_at'])
                sent += 1
                results.append({'id': rec.id, 'email': rec.email,
                                'name': rec.name, 'ok': True,
                                'attachments': len(m['files'])})
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return Response({'sent': sent, 'test': bool(test_to),
                         'to': test_to or None,
                         'results': results + [dict(b, ok=False) for b in bad]})
