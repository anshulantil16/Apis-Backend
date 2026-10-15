"""Appraisal and EOM: the sign-in that was missing.

Both tools kept the OTP login they had before the portal. It proved who you
were once, at the door, and handed back a profile and nothing else -- so
every request afterwards named an employee id in the URL and was believed.
A sweep of the live site on 14-10-2026, carrying no credential at all, got
/api/appraisal/employees/ and /api/eom/employees/ in full: names, employee
ids, email addresses, phone numbers.

These tests cover the three things that have to be true: the login issues a
session, the endpoints refuse a caller without one, and -- until the flag is
turned on -- nothing changes for anybody.
"""
from django.test import Client, TestCase, override_settings

from accounts.models import AppSession, PortalSession, PortalUser


class TheSessionItself(TestCase):
    def test_a_token_is_stored_as_a_hash_and_never_in_the_clear(self):
        token = AppSession.start('appraisal', 'HO0222')
        row = AppSession.objects.get()
        self.assertNotEqual(row.token_hash, token)
        self.assertEqual(row.token_hash, AppSession.hash_token(token))
        self.assertNotIn(token, str(row.__dict__))

    def test_a_revoked_or_expired_session_is_not_live(self):
        from datetime import timedelta
        from django.utils import timezone
        AppSession.start('eom', 'E1')
        row = AppSession.objects.get()
        self.assertTrue(row.is_live)
        row.expires_at = timezone.now() - timedelta(seconds=1)
        self.assertFalse(row.is_live)
        row.expires_at = timezone.now() + timedelta(hours=1)
        row.revoked_at = timezone.now()
        self.assertFalse(row.is_live)

    def test_one_tool_s_session_is_not_the_other_s(self):
        """A token good for EOM must not open Appraisal. They are different
        employee tables and different people."""
        from accounts.auth import current_app_session
        token = AppSession.start('eom', 'E1')
        request = Client().get('/').wsgi_request
        request.META['HTTP_AUTHORIZATION'] = f'Bearer {token}'
        self.assertIsNotNone(current_app_session(request, 'eom'))
        self.assertIsNone(current_app_session(request, 'appraisal'))


class TheGate(TestCase):
    """Behaviour with ENFORCE_APP_SESSIONS on, which is how it will run once
    browsers are carrying tokens."""

    OPEN = '/api/appraisal/employees/'

    def setUp(self):
        self.anon = Client()

    @override_settings(ENFORCE_APP_SESSIONS=True)
    def test_an_anonymous_caller_is_refused(self):
        r = self.anon.get(self.OPEN)
        self.assertEqual(r.status_code, 401)
        self.assertNotIn(b'@', r.content)      # no addresses handed out

    @override_settings(ENFORCE_APP_SESSIONS=True)
    def test_the_tool_s_own_session_opens_it(self):
        token = AppSession.start('appraisal', 'HO0222')
        c = Client()
        c.defaults['HTTP_AUTHORIZATION'] = f'Bearer {token}'
        self.assertEqual(c.get(self.OPEN).status_code, 200)

    @override_settings(ENFORCE_APP_SESSIONS=True)
    def test_a_portal_session_opens_it_too(self):
        """Inside the intranet these tools are opened by somebody already
        signed in to the portal, whose browser sends that token instead."""
        user = PortalUser.objects.create(employee_code='P1', email='p1@apisindia.com',
                                         name='Portal Person')
        c = Client()
        c.defaults['HTTP_AUTHORIZATION'] = f'Bearer {PortalSession.start(user)}'
        self.assertEqual(c.get(self.OPEN).status_code, 200)

    @override_settings(ENFORCE_APP_SESSIONS=True)
    def test_the_login_stays_open(self):
        """Gating the way in locks everybody out of everything."""
        r = self.anon.post('/api/appraisal/auth/send-otp/', {})
        self.assertNotEqual(r.status_code, 401)

    @override_settings(ENFORCE_APP_SESSIONS=True)
    def test_the_same_holds_for_eom(self):
        self.assertEqual(self.anon.get('/api/eom/employees/').status_code, 401)
        token = AppSession.start('eom', 'E1')
        c = Client()
        c.defaults['HTTP_AUTHORIZATION'] = f'Bearer {token}'
        self.assertEqual(c.get('/api/eom/employees/').status_code, 200)


class UntilTheFlagGoesOn(TestCase):
    """The reason the flag exists.

    Issuing tokens and demanding them in one release would sign out everyone
    who was already working -- mid goal sheet, mid nomination -- because
    their browser has no token yet. So this ships inert.
    """

    @override_settings(ENFORCE_APP_SESSIONS=False)
    def test_nothing_changes_for_a_caller_with_no_token(self):
        self.assertEqual(Client().get('/api/appraisal/employees/').status_code, 200)
        self.assertEqual(Client().get('/api/eom/employees/').status_code, 200)

    def test_off_is_the_default(self):
        from django.conf import settings
        self.assertFalse(getattr(settings, 'ENFORCE_APP_SESSIONS', False),
                         'the flag must ship off; turn it on from the environment')
