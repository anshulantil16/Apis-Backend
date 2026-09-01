"""Portal sign-in, with the development bypass under a microscope.

The bypass registers a caller and hands back the sign-in code, so if it were
ever live on a server it would be a complete authentication bypass: anyone
could name any @apisindia.com address and be let in as that person. It is
gated on PORTAL_DEV_LOGIN, which is deliberately NOT tied to DEBUG — DEBUG
defaults to True when unset in config/settings.py, so a server whose .env
forgot the line would silently enable it.

Most of what follows is therefore about proving the gate is shut by default.
"""
from django.test import TestCase, override_settings

from .models import PortalOTP, PortalSession, PortalUser, SUPERADMIN_BOOTSTRAP_EMAIL

REQUEST = '/api/accounts/portal/request-otp/'
VERIFY = '/api/accounts/portal/verify-otp/'
ME = '/api/accounts/portal/me/'

DEV_ON = override_settings(PORTAL_DEV_LOGIN=True)
DEV_OFF = override_settings(PORTAL_DEV_LOGIN=False)


def ask(client, email):
    return client.post(REQUEST, {'email': email}, content_type='application/json')


@DEV_OFF
class DevLoginDisabled(TestCase):
    """The deployed configuration. Nothing here may leak a code or a user."""

    def test_unknown_address_is_not_registered(self):
        ask(self.client, 'rainy@apisindia.com')
        self.assertFalse(PortalUser.objects.filter(email='rainy@apisindia.com').exists())

    def test_no_code_is_ever_returned(self):
        r = ask(self.client, 'rainy@apisindia.com')
        self.assertNotIn('dev_otp', r.json())

    def test_unknown_address_is_indistinguishable_from_a_known_one(self):
        """Otherwise the endpoint is a directory of who works here."""
        PortalUser.objects.create(email='real@apisindia.com', employee_code='E1', name='Real')
        with override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'):
            known = ask(self.client, 'real@apisindia.com')
        unknown = ask(self.client, 'ghost@apisindia.com')
        self.assertEqual(known.status_code, unknown.status_code)
        self.assertEqual(set(known.json()), set(unknown.json()))

    def test_no_session_without_a_valid_code(self):
        r = self.client.post(VERIFY, {'email': 'rainy@apisindia.com', 'otp': '123456'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PortalSession.objects.count(), 0)


@DEV_ON
class DevLoginEnabled(TestCase):
    """A developer's machine: no HRMS directory, no SMTP credentials."""

    def test_company_address_is_registered_on_first_use(self):
        ask(self.client, 'rainy@apisindia.com')
        u = PortalUser.objects.get(email='rainy@apisindia.com')
        self.assertTrue(u.is_active)
        self.assertFalse(u.is_superadmin, 'a dev account must not be an administrator')

    def test_code_comes_back_in_the_response(self):
        r = ask(self.client, 'rainy@apisindia.com')
        self.assertRegex(r.json().get('dev_otp', ''), r'^\d{6}$')

    def test_the_returned_code_actually_signs_you_in(self):
        code = ask(self.client, 'rainy@apisindia.com').json()['dev_otp']
        r = self.client.post(VERIFY, {'email': 'rainy@apisindia.com', 'otp': code},
                             content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        token = r.json()['token']

        me = self.client.get(ME, HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()['user']['email'], 'rainy@apisindia.com')

    def test_a_wrong_code_is_still_refused(self):
        """The bypass must shorten the setup, not weaken the check."""
        ask(self.client, 'rainy@apisindia.com')
        r = self.client.post(VERIFY, {'email': 'rainy@apisindia.com', 'otp': '000000'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PortalSession.objects.count(), 0)

    def test_outside_addresses_are_still_refused(self):
        ask(self.client, 'attacker@gmail.com')
        self.assertFalse(PortalUser.objects.filter(email='attacker@gmail.com').exists())

    def test_an_existing_person_is_not_overwritten(self):
        """Signing in must never reset someone's real access."""
        PortalUser.objects.create(email='hod@apisindia.com', employee_code='E9',
                                  name='Head of Dept', app_access=['tada'])
        ask(self.client, 'hod@apisindia.com')
        u = PortalUser.objects.get(email='hod@apisindia.com')
        self.assertEqual(u.employee_code, 'E9')
        self.assertEqual(u.app_access, ['tada'])

    def test_a_disabled_person_stays_disabled(self):
        """Otherwise the bypass silently re-admits someone who was switched off."""
        PortalUser.objects.create(email='gone@apisindia.com', employee_code='E8',
                                  name='Left', is_active=False)
        ask(self.client, 'gone@apisindia.com')
        self.assertFalse(PortalUser.objects.get(email='gone@apisindia.com').is_active)


class BootstrapSuperadmin(TestCase):
    """The founding account has to work on a completely empty database."""

    @DEV_OFF
    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_created_on_demand_without_the_dev_flag(self):
        ask(self.client, SUPERADMIN_BOOTSTRAP_EMAIL)
        u = PortalUser.objects.get(email=SUPERADMIN_BOOTSTRAP_EMAIL)
        self.assertTrue(u.is_superadmin)

    @DEV_ON
    def test_signs_in_end_to_end(self):
        code = ask(self.client, SUPERADMIN_BOOTSTRAP_EMAIL).json()['dev_otp']
        r = self.client.post(VERIFY, {'email': SUPERADMIN_BOOTSTRAP_EMAIL, 'otp': code},
                             content_type='application/json')
        self.assertTrue(r.json()['user']['is_superadmin'])


@DEV_ON
class CodeHandling(TestCase):
    """Properties that must hold however the code was delivered."""

    def setUp(self):
        self.email = 'dev@apisindia.com'

    def test_a_code_cannot_be_used_twice(self):
        code = ask(self.client, self.email).json()['dev_otp']
        body = {'email': self.email, 'otp': code}
        self.assertEqual(self.client.post(VERIFY, body, content_type='application/json')
                         .status_code, 200)
        self.assertEqual(self.client.post(VERIFY, body, content_type='application/json')
                         .status_code, 400)

    def test_codes_are_not_stored_in_the_clear(self):
        code = ask(self.client, self.email).json()['dev_otp']
        self.assertNotIn(code, [o.code_hash for o in PortalOTP.objects.all()])

    def test_attempts_are_capped(self):
        """The last allowed guess answers 429 and burns the code; the request
        after that finds nothing outstanding, which is a 400, not another 429."""
        code = ask(self.client, self.email).json()['dev_otp']
        for _ in range(PortalOTP.MAX_ATTEMPTS):
            r = self.client.post(VERIFY, {'email': self.email, 'otp': '000000'},
                                 content_type='application/json')
        self.assertEqual(r.status_code, 429)

        # And the real code is dead too — exhausting the guesses burns it.
        r = self.client.post(VERIFY, {'email': self.email, 'otp': code},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(PortalSession.objects.count(), 0)


@DEV_OFF
class SessionTokens(TestCase):
    def setUp(self):
        self.user = PortalUser.objects.create(email='s@apisindia.com', employee_code='E2',
                                              name='Session Tester')

    def test_me_rejects_a_missing_or_bogus_token(self):
        self.assertEqual(self.client.get(ME).status_code, 401)
        self.assertEqual(self.client.get(ME, HTTP_AUTHORIZATION='Bearer nonsense').status_code, 401)

    def test_tokens_are_not_stored_in_the_clear(self):
        raw = PortalSession.start(self.user)
        self.assertTrue(PortalSession.objects.exclude(token_hash=raw).exists())
        self.assertFalse(PortalSession.objects.filter(token_hash=raw).exists())

    def test_a_revoked_session_stops_working(self):
        raw = PortalSession.start(self.user)
        self.assertEqual(self.client.get(ME, HTTP_AUTHORIZATION=f'Bearer {raw}').status_code, 200)
        PortalSession.objects.update(revoked_at=__import__('django.utils.timezone',
                                                           fromlist=['timezone']).now())
        self.assertEqual(self.client.get(ME, HTTP_AUTHORIZATION=f'Bearer {raw}').status_code, 401)

    def test_deactivating_someone_kills_their_live_session(self):
        raw = PortalSession.start(self.user)
        PortalUser.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.client.get(ME, HTTP_AUTHORIZATION=f'Bearer {raw}').status_code, 401)
