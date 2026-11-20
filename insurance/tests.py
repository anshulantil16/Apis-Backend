"""Tests for the insurance SSO token.

The point of most of these is that we cannot test against the insurer. There
is no sandbox in the integration guide and no secret yet, so the only thing
that can be checked here is that what we produce matches what section 4 and
section 5 describe, byte for byte -- and that a token can be decrypted again
by an independent implementation of the same spec.

The `_decrypt` helper below is deliberately written from the document rather
than by calling our own code backwards: a test that reuses the implementation
it is testing proves only that the code agrees with itself.
"""
import base64
import hashlib
import json

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import AppKey, PortalUser

from . import sso

SECRET = 'a-shared-secret-from-the-insurer'
BASE = 'https://portal.insurer.example'


def _decrypt(token, secret=SECRET):
    """Undo section 5, the way the insurer's side would."""
    iv_b64, ct_b64, tag_b64 = token.split('.')

    def un(part):
        return base64.urlsafe_b64decode(part + '=' * (-len(part) % 4))

    key = hashlib.sha256(secret.encode()).digest()
    plain = AESGCM(key).decrypt(un(iv_b64), un(ct_b64) + un(tag_b64), None)
    return json.loads(plain)


def _user(**kw):
    kw.setdefault('employee_code', 'HO0235')
    kw.setdefault('name', 'Test Person')
    kw.setdefault('email', 'test.person@apisindia.com')
    return PortalUser.objects.create(**kw)


@override_settings(INSURANCE_SSO_SECRET=SECRET, INSURANCE_SSO_BASE_URL=BASE)
class TokenShapeTests(TestCase):

    def test_token_round_trips(self):
        """The whole integration rests on this one assertion."""
        user = _user()
        payload = _decrypt(sso.make_token(sso.payload_for(user, 'claim-status')))
        self.assertEqual(payload['email'], 'test.person@apisindia.com')
        self.assertEqual(payload['destination'], 'claim-status')

    def test_three_parts_separated_by_dots(self):
        token = sso.make_token({'email': 'a@b.com'})
        self.assertEqual(len(token.split('.')), 3)

    def test_iv_is_twelve_bytes_and_tag_is_sixteen(self):
        iv, _, tag = sso.make_token({'email': 'a@b.com'}).split('.')

        def size(part):
            return len(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))

        self.assertEqual(size(iv), 12)
        self.assertEqual(size(tag), 16)

    def test_encoding_is_url_safe_and_unpadded(self):
        """Section 4: URL-safe alphabet, padding removed."""
        token = sso.make_token({'email': 'a@b.com', 'pad': 'x' * 40})
        self.assertNotIn('=', token)
        self.assertNotIn('+', token)
        self.assertNotIn('/', token)

    def test_every_token_uses_a_fresh_iv(self):
        """Section 10: never reuse an IV with the same key. Reusing one in
        GCM does not merely weaken the cipher, it leaks the plaintext."""
        ivs = {sso.make_token({'email': 'a@b.com'}).split('.')[0] for _ in range(50)}
        self.assertEqual(len(ivs), 50)

    def test_a_tampered_token_will_not_decrypt(self):
        """What the authentication tag is for.

        The flip is done on the decoded BYTES, not on a base64 character:
        the last character of a base64 string carries bits that decoding
        throws away, so changing it sometimes decodes to the very same
        ciphertext and the tamper never happens.
        """
        iv, ct, tag = sso.make_token({'email': 'a@b.com'}).split('.')
        raw = bytearray(base64.urlsafe_b64decode(ct + '=' * (-len(ct) % 4)))
        raw[0] ^= 0xFF
        flipped = base64.urlsafe_b64encode(bytes(raw)).decode().rstrip('=')
        with self.assertRaises(Exception):
            _decrypt(f'{iv}.{flipped}.{tag}')

    def test_a_different_secret_will_not_decrypt(self):
        with self.assertRaises(Exception):
            _decrypt(sso.make_token({'email': 'a@b.com'}), secret='the-wrong-secret')


@override_settings(INSURANCE_SSO_SECRET=SECRET, INSURANCE_SSO_BASE_URL=BASE)
class PayloadTests(TestCase):

    def test_timestamp_is_unix_seconds_and_is_now(self):
        """Section 7: not in the future, and recent enough to be inside the
        three-hour window. A server reading its clock in UTC while believing
        it is IST would be five and a half hours out, which is the failure
        mode most likely to go unnoticed until the insurer rejects it."""
        payload = sso.payload_for(_user())
        now = int(timezone.now().timestamp())
        self.assertIsInstance(payload['timestamp'], int)
        self.assertLessEqual(payload['timestamp'], now)
        self.assertGreater(payload['timestamp'], now - 5)

    def test_employee_code_is_sent_when_there_is_no_email(self):
        """Roughly fifty people in the directory have no address at all.
        Section 3 allows the code in exactly that case, so it has to go."""
        payload = sso.payload_for(_user(email=None, employee_code='NA0007'))
        self.assertNotIn('email', payload)
        self.assertEqual(payload['employee_id'], 'NA0007')

    def test_employee_code_field_name_is_settable(self):
        """The guide does not pin this field's spelling down -- see settings."""
        with override_settings(INSURANCE_SSO_EMPLOYEE_ID_FIELD='employeeId'):
            self.assertIn('employeeId', sso.payload_for(_user()))

    def test_no_destination_means_no_destination_key(self):
        """Section 3: omitted, the insurer uses their own default page.
        Sending an empty string is not the same as omitting the field."""
        self.assertNotIn('destination', sso.payload_for(_user()))

    def test_an_unapproved_destination_is_refused_before_minting(self):
        with self.assertRaises(ValueError):
            sso.launch_url(_user(), 'payroll')

    def test_url_is_the_documented_shape(self):
        url = sso.launch_url(_user(), 'my-coverage')
        self.assertTrue(url.startswith(f'{BASE}/sso/login/'))

    def test_nothing_is_minted_without_a_secret(self):
        with override_settings(INSURANCE_SSO_SECRET=''):
            with self.assertRaises(sso.NotConfigured):
                sso.launch_url(_user())


class EndpointTests(TestCase):
    """The endpoints answer the caller about the caller, and nobody else."""

    def setUp(self):
        self.user = _user()
        self.user.app_access = [AppKey.INSURANCE]
        self.user.save()

    def test_overview_refuses_an_anonymous_caller(self):
        self.assertEqual(self.client.get('/api/insurance/overview/').status_code, 401)

    def test_launch_refuses_an_anonymous_caller(self):
        r = self.client.post('/api/insurance/launch/',
                             {'destination': 'claim-status'}, content_type='application/json')
        self.assertEqual(r.status_code, 401)

    @override_settings(INSURANCE_SSO_SECRET='', INSURANCE_SSO_BASE_URL='')
    def test_unconfigured_is_reported_not_crashed(self):
        self.assertFalse(sso.configured())
