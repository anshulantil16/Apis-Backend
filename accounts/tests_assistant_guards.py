"""Every Ask APIS endpoint refuses a caller with no token.

These live together rather than in each app because the thing being
checked is one rule applied in four places, and the risk is that a fifth
is added later without it.

Each of these endpoints is declared `authentication_classes = ()`, which
is correct -- the project's JWT authenticator would reject the chat's own
token before the view ran -- but it means nothing stands between the
internet and the view except the check inside it. A regression here is
not a broken feature, it is a stranger raising a ticket, filing a travel
claim or editing a goal sheet as a named employee.

No fixtures on purpose: a test that needs a database set up is a test
somebody skips when it goes red.
"""
from django.test import TestCase

# (what it is, url, a body that would otherwise be a valid request)
GUARDED = [
    ('TA/DA submit',        '/api/tada/assistant/submit/',
     {'kind': 'tour_sanction', 'data': {'purpose': 'client visit'}}),
    ('Goal Setting save',   '/api/goalsetting/assistant/save/',
     {'cycle_id': 1, 'kras': []}),
    ('EOM save',            '/api/eom/assistant/save/',
     {'cycle_id': 1}),
    ('Help Desk ticket',    '/api/roompulse/assistant/ticket/',
     {'subject': 'laptop will not start', 'description': 'since this morning',
      'category': 'hardware', 'priority': 'high'}),
    ('Help Desk request',   '/api/roompulse/assistant/request/',
     {'item_name': 'office chair', 'quantity': '1', 'urgency': 'normal'}),
]


class NoTokenNoAction(TestCase):
    def test_every_endpoint_refuses_a_caller_with_no_token(self):
        for what, url, body in GUARDED:
            with self.subTest(what):
                r = self.client.post(url, body, content_type='application/json')
                self.assertEqual(r.status_code, 401, f'{what} acted without a token')

    def test_every_endpoint_refuses_a_made_up_token(self):
        for what, url, body in GUARDED:
            with self.subTest(what):
                r = self.client.post(url, {**body, 'token': 'pasted-from-somewhere'},
                                     content_type='application/json')
                self.assertEqual(r.status_code, 401, f'{what} accepted an invented token')

    def test_an_empty_token_is_not_treated_as_a_token(self):
        """The one that slips through: '' is falsy, but a cache lookup on a
        bare prefix can answer with something."""
        for what, url, body in GUARDED:
            with self.subTest(what):
                r = self.client.post(url, {**body, 'token': ''}, content_type='application/json')
                self.assertEqual(r.status_code, 401, f'{what} accepted an empty token')

    def test_whitespace_is_not_a_token(self):
        for what, url, body in GUARDED:
            with self.subTest(what):
                r = self.client.post(url, {**body, 'token': '   '},
                                     content_type='application/json')
                self.assertEqual(r.status_code, 401, f'{what} accepted whitespace as a token')


class LogoutIsSafeToCall(TestCase):
    """Signing out must never be the thing that errors -- somebody doing it
    twice, or after their session has already gone, is the normal case."""

    LOGOUTS = [
        '/api/goalsetting/assistant/logout/',
        '/api/eom/assistant/logout/',
        '/api/roompulse/assistant/logout/',
    ]

    def test_logging_out_without_a_token_succeeds_quietly(self):
        for url in self.LOGOUTS:
            with self.subTest(url):
                r = self.client.post(url, {}, content_type='application/json')
                self.assertEqual(r.status_code, 200)

    def test_logging_out_twice_is_fine(self):
        for url in self.LOGOUTS:
            with self.subTest(url):
                self.client.post(url, {'token': 'whatever'}, content_type='application/json')
                r = self.client.post(url, {'token': 'whatever'}, content_type='application/json')
                self.assertEqual(r.status_code, 200)
