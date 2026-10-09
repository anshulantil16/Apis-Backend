"""Who can reach what, checked against every endpoint the app exposes.

Written as a sweep over the URL conf rather than a list typed out here, so an
endpoint added later is covered the day it is added rather than the day
somebody remembers to add a test for it. A route that is deliberately open
has to be named below; anything else must refuse an anonymous caller.

The failure this guards against is not subtle. SalesIQ holds the company's
revenue by territory and by head; an endpoint that answers without a session
publishes it to anyone who can reach the server, and the server has a public
IP.
"""
from django.test import Client as RawClient, TestCase as _TestCase
from django.urls import get_resolver

from .views.auth import SALESIQ_SUPER_ADMIN, issue_session


# The one route that must answer without a session, because it is how a
# session is obtained.
OPEN = {'login/'}

# Routes that need a concrete object to address. Given an id that does not
# exist, so the gate is what answers, never the lookup.
PARAMS = {'<int:pk>': '1'}


def routes():
    """-> [(path, url), ...] for every endpoint under /api/sales/."""
    out = []
    for p in get_resolver().url_patterns:
        if not str(getattr(p, 'pattern', '')).startswith('api/sales'):
            continue
        for sub in p.url_patterns:
            raw = str(sub.pattern)
            url = '/api/sales/' + raw
            for k, v in PARAMS.items():
                url = url.replace(k, v)
            out.append((raw, url))
    return out


class EveryEndpointIsBehindTheGate(_TestCase):

    def setUp(self):
        self.all = routes()
        self.assertGreater(len(self.all), 30, 'the sweep found almost nothing')

    def test_an_anonymous_caller_is_refused_everywhere(self):
        """Every verb, not only GET: a POST that is gated and a DELETE that
        is not is still an open door."""
        c = RawClient()
        open_doors = []
        for raw, url in self.all:
            if raw in OPEN:
                continue
            for verb in ('get', 'post', 'patch', 'delete'):
                r = getattr(c, verb)(url)
                # 405 is fine -- the verb is not offered at all. Anything in
                # the 2xx range means it answered without a session.
                if 200 <= r.status_code < 300:
                    open_doors.append(f'{verb.upper()} {url} -> {r.status_code}')
        self.assertEqual(open_doors, [])

    def test_the_login_route_is_the_only_open_one(self):
        """Stated so that opening a second one is a deliberate edit to this
        list rather than something that happens quietly."""
        self.assertEqual(OPEN, {'login/'})


class WhatAReaderMayNotDo(_TestCase):
    """A reader is granted the dashboard. That is not the same as being
    granted the ability to mail the sales leadership, to replace the
    morning's figures, or to decide who else may."""

    def setUp(self):
        # A real reader: granted SalesIQ in the Admin Console, which is the
        # only way anybody becomes one.
        from accounts.models import AppKey, PortalUser
        PortalUser.objects.create(
            employee_code='R1', email='reader@apisindia.com', name='Reader',
            is_active=True, app_access=[AppKey.SALESIQ])
        self.reader = RawClient()
        self.reader.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session(
            'reader@apisindia.com')
        self.owner = RawClient()
        self.owner.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session(
            SALESIQ_SUPER_ADMIN)

    def refused(self, verb, url, **kw):
        r = getattr(self.reader, verb)(url, **kw)
        self.assertIn(r.status_code, (401, 403),
                      f'{verb.upper()} {url} answered {r.status_code} to a reader')

    def test_a_reader_cannot_send_the_morning_mail(self):
        self.refused('post', '/api/sales/mail/send/',
                     data='{}', content_type='application/json')

    def test_a_reader_cannot_upload_the_workbook(self):
        self.refused('post', '/api/sales/upload/')

    def test_a_reader_cannot_delete_an_upload(self):
        self.refused('delete', '/api/sales/uploads/')

    def test_a_reader_cannot_change_the_recipient_list(self):
        self.refused('post', '/api/sales/recipients/edit/',
                     data='{}', content_type='application/json')
        self.refused('delete', '/api/sales/recipients/edit/')
        self.refused('post', '/api/sales/recipients/import/')

    def test_a_reader_cannot_delete_a_review_snapshot(self):
        self.refused('delete', '/api/sales/review/1/')

    def test_a_reader_cannot_grant_upload_access(self):
        self.refused('post', '/api/sales/uploaders/edit/',
                     data='{}', content_type='application/json')
        self.refused('delete', '/api/sales/uploaders/edit/1/')

    def test_a_reader_may_still_read_the_dashboard(self):
        """The gate has to let the reader in, or it is not a gate, it is a
        wall -- and the numbers are the point of the tool."""
        for url in ('/api/sales/overview/', '/api/sales/review/',
                    '/api/sales/recipients/', '/api/sales/uploads/'):
            r = self.reader.get(url)
            self.assertEqual(r.status_code, 200, url)


class WhatAnUploaderMayNotDo(_TestCase):
    """An uploader who could grant upload access could grant it to anybody,
    which makes the distinction between the two roles decorative."""

    def setUp(self):
        from .models import UploaderGrant
        UploaderGrant.objects.create(email='up@apisindia.com', is_active=True)
        self.up = RawClient()
        self.up.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session(
            'up@apisindia.com')

    def test_an_uploader_cannot_grant_upload_access(self):
        r = self.up.post('/api/sales/uploaders/edit/', data='{}',
                         content_type='application/json')
        self.assertIn(r.status_code, (401, 403))

    def test_an_uploader_cannot_clear_the_whole_recipient_list(self):
        """Clearing the list is how everybody stops being sent anything."""
        r = self.up.delete('/api/sales/recipients/edit/')
        self.assertIn(r.status_code, (401, 403))

    def test_an_uploader_cannot_fire_the_send(self):
        r = self.up.post('/api/sales/mail/send/', data='{}',
                         content_type='application/json')
        self.assertIn(r.status_code, (401, 403))

    def test_an_uploader_can_upload(self):
        """Which is the whole of what the grant is for."""
        r = self.up.post('/api/sales/upload/')
        self.assertNotIn(r.status_code, (401, 403))


class AccessIsResolvedOnEveryRequest(_TestCase):
    """Not stamped into the session when it is issued.

    So revoking somebody in the Admin Console takes effect at once, even
    against a token they are already holding. A session that carried its own
    role would keep working until it expired, which is twelve hours of
    somebody reading revenue after they were told they could not.
    """

    def setUp(self):
        from accounts.models import AppKey, PortalUser
        self.user = PortalUser.objects.create(
            employee_code='R2', email='leaver@apisindia.com', name='Leaver',
            is_active=True, app_access=[AppKey.SALESIQ])
        self.c = RawClient()
        self.c.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session(
            'leaver@apisindia.com')

    def test_the_token_works_while_the_grant_stands(self):
        self.assertEqual(self.c.get('/api/sales/overview/').status_code, 200)

    def test_and_stops_the_moment_the_grant_is_taken_away(self):
        self.user.app_access = []
        self.user.save()
        self.assertEqual(self.c.get('/api/sales/overview/').status_code, 401)

    def test_and_stops_when_the_account_is_disabled(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.c.get('/api/sales/overview/').status_code, 401)

    def test_a_session_for_an_address_nobody_granted_is_refused(self):
        """Holding a validly signed token is not the same as being allowed
        in -- the address has to be one somebody chose."""
        c = RawClient()
        c.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session('nobody@apisindia.com')
        self.assertEqual(c.get('/api/sales/overview/').status_code, 401)
