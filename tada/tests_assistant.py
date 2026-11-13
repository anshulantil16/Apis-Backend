"""Ask APIS cannot be used to act as somebody else.

The chat endpoints are deliberately declared with `authentication_classes
= ()`, which looks alarming and is correct: the project-wide JWT
authenticator would try to parse these tokens and reject them before the
view ran. The actual check is the token issued after an OTP, and these
tests exist so that the real guard cannot be quietly removed while the
alarming-looking line stays.

Four properties matter, and every one of them is a way somebody could file
a travel claim in a colleague's name:

  * no token is refused
  * a forged or expired token is refused
  * the employee_id in the body is ignored in favour of the token's
  * guessing the six-digit code gets you locked out
"""
from datetime import timedelta

from django.core import signing
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from tada.assistant import SALT, TOKEN_AGE
from tada.models import TadaOTP, TadaUser


class AskApisIdentity(TestCase):
    VERIFY = '/api/tada/assistant/verify/'
    SUBMIT = '/api/tada/assistant/submit/'

    def setUp(self):
        cache.clear()      # the lockout counter is cached and leaks between tests
        self.asha = TadaUser.objects.create(
            employee_id='HO0501', name='Asha Mehta', email='ho0501@apisindia.com',
            is_active=True, level='L3', hq_city='Delhi')
        self.bhavesh = TadaUser.objects.create(
            employee_id='HO0502', name='Bhavesh Shah', email='ho0502@apisindia.com',
            is_active=True, level='L3', hq_city='Mumbai')

    def _token_for(self, user):
        return signing.dumps({'uid': user.id, 'eid': user.employee_id, 'n': 'x'}, salt=SALT)

    def _otp(self, user, code='123456', minutes=5):
        return TadaOTP.objects.create(user=user, code=code, is_used=False,
                                      expires_at=timezone.now() + timedelta(minutes=minutes))

    # --- the token is the only thing that says who you are ----------------

    def test_submitting_without_a_token_is_refused(self):
        r = self.client.post(self.SUBMIT, {'kind': 'tour_sanction', 'data': {}},
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_a_made_up_token_is_refused(self):
        r = self.client.post(self.SUBMIT, {'token': 'not-a-real-token',
                                           'kind': 'tour_sanction', 'data': {}},
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_a_token_signed_with_another_salt_is_refused(self):
        """Someone who knows the SECRET_KEY shape but not this salt."""
        forged = signing.dumps({'uid': self.asha.id, 'eid': self.asha.employee_id},
                               salt='some.other.salt')
        r = self.client.post(self.SUBMIT, {'token': forged, 'kind': 'tour_sanction', 'data': {}},
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_an_expired_token_is_refused(self):
        """Signed tokens carry their own age; a stale one must not work."""
        old = signing.dumps({'uid': self.asha.id, 'eid': self.asha.employee_id, 'n': 'x'},
                            salt=SALT)
        with self.settings(USE_TZ=True):
            # Signed well beyond the window the view will accept.
            import django.core.signing as s
            real = s.time.time
            try:
                s.time.time = lambda: real() + TOKEN_AGE + 60
                r = self.client.post(self.SUBMIT,
                                     {'token': old, 'kind': 'tour_sanction', 'data': {}},
                                     content_type='application/json')
            finally:
                s.time.time = real
        self.assertEqual(r.status_code, 401)

    def test_a_token_for_a_deactivated_person_is_refused(self):
        """Somebody who has left keeps whatever token was in their browser."""
        token = self._token_for(self.asha)
        TadaUser.objects.filter(id=self.asha.id).update(is_active=False)
        r = self.client.post(self.SUBMIT, {'token': token, 'kind': 'tour_sanction', 'data': {}},
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_the_body_cannot_claim_to_be_somebody_else(self):
        """The whole point. Asha's token, Bhavesh's id in the body -- the
        request must be filed as Asha or not at all."""
        token = self._token_for(self.asha)
        r = self.client.post(self.SUBMIT, {
            'token': token, 'kind': 'tour_sanction',
            'data': {'employee_id': self.bhavesh.employee_id, 'purpose': 'client visit'},
        }, content_type='application/json')
        # Whatever the portal view makes of the rest, it must never have been
        # handed Bhavesh's id.
        self.assertNotEqual(r.status_code, 401)
        body = r.content.decode().lower()
        self.assertNotIn(self.bhavesh.employee_id.lower(), body)

    def test_an_unknown_kind_is_refused(self):
        r = self.client.post(self.SUBMIT, {'token': self._token_for(self.asha),
                                           'kind': 'travelling_expenses', 'data': {}},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    # --- the code cannot be guessed ---------------------------------------

    def test_a_wrong_code_is_refused(self):
        self._otp(self.asha, '123456')
        r = self.client.post(self.VERIFY, {'employee_id': 'HO0501', 'otp': '999999'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertNotIn('token', r.json())

    def test_guessing_locks_the_employee_out(self):
        """Six digits fall to a few hundred thousand tries; five is the limit."""
        self._otp(self.asha, '123456')
        for _ in range(5):
            self.client.post(self.VERIFY, {'employee_id': 'HO0501', 'otp': '000000'},
                             content_type='application/json')
        r = self.client.post(self.VERIFY, {'employee_id': 'HO0501', 'otp': '123456'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 429, 'the right code still works after five wrong ones')

    def test_a_correct_code_returns_a_working_token(self):
        self._otp(self.asha, '123456')
        r = self.client.post(self.VERIFY, {'employee_id': 'HO0501', 'otp': '123456'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json().get('token'))
        self.assertEqual(r.json()['user']['employee_id'], 'HO0501')

    def test_a_code_cannot_be_used_twice(self):
        self._otp(self.asha, '123456')
        self.client.post(self.VERIFY, {'employee_id': 'HO0501', 'otp': '123456'},
                         content_type='application/json')
        r = self.client.post(self.VERIFY, {'employee_id': 'HO0501', 'otp': '123456'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_an_expired_code_is_refused(self):
        self._otp(self.asha, '123456', minutes=-1)
        r = self.client.post(self.VERIFY, {'employee_id': 'HO0501', 'otp': '123456'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_somebody_elses_code_does_not_work_for_you(self):
        """Bhavesh's code, Asha's employee id."""
        self._otp(self.bhavesh, '654321')
        r = self.client.post(self.VERIFY, {'employee_id': 'HO0501', 'otp': '654321'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
