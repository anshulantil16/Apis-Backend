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
from unittest import mock
from datetime import date

import io

from accounts.services import hrms
from django.core.management import call_command
from django.core.management.base import CommandError

from .models import HrmsSyncLog

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


@DEV_OFF
class AdminConsolePowers(TestCase):
    """What an administrator can actually do to an account.

    The console could toggle switches but not edit an address, add a person, or
    remove one - so the endpoint that existed was unreachable and the ones that
    mattered were missing.
    """

    def setUp(self):
        self.admin = PortalUser.objects.create(
            email=SUPERADMIN_BOOTSTRAP_EMAIL, employee_code='APIS-ADMIN',
            name='Anshul Antil', is_superadmin=True)
        self.token = PortalSession.start(self.admin)
        self.other = PortalUser.objects.create(
            email='someone@apisindia.com', employee_code='E1', name='Some One',
            app_access=['home'])
        self.auth = {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def patch(self, user, body):
        return self.client.patch(f'/api/accounts/portal/admin/users/{user.id}/', body,
                                 content_type='application/json', **self.auth)

    def post(self, path, body):
        return self.client.post(f'/api/accounts/portal/admin/{path}', body,
                                content_type='application/json', **self.auth)

    def remove(self, user, name, auth=None):
        return self.client.delete(f'/api/accounts/portal/admin/users/{user.id}/',
                                  {'confirm_name': name}, content_type='application/json',
                                  **(auth or self.auth))

    # -- editing someone ----------------------------------------------------
    def test_an_address_can_be_corrected(self):
        """A mistyped address locks that person out with no clue why."""
        r = self.patch(self.other, {'email': 'Correct@apisindia.com'})
        self.assertEqual(r.status_code, 200, r.content)
        self.other.refresh_from_db()
        self.assertEqual(self.other.email, 'correct@apisindia.com')

    def test_two_people_cannot_share_an_address(self):
        r = self.patch(self.other, {'email': SUPERADMIN_BOOTSTRAP_EMAIL})
        self.assertEqual(r.status_code, 400)

    def test_a_malformed_address_is_refused(self):
        r = self.patch(self.other, {'email': 'not-an-address'})
        self.assertEqual(r.status_code, 400)

    def test_the_founding_address_is_fixed(self):
        """The bootstrap account is recognised by its address; change it and
        the way back into an empty portal is gone."""
        r = self.patch(self.admin, {'email': 'elsewhere@apisindia.com'})
        self.assertEqual(r.status_code, 400)

    def test_details_can_be_edited(self):
        r = self.patch(self.other, {'name': 'New Name', 'designation': 'Manager',
                                    'department': 'Sales', 'employee_code': 'E99'})
        self.assertEqual(r.status_code, 200, r.content)
        self.other.refresh_from_db()
        self.assertEqual((self.other.name, self.other.employee_code), ('New Name', 'E99'))

    # -- adding and removing ------------------------------------------------
    def test_a_person_can_be_added_by_hand(self):
        r = self.post('users/', {'email': 'new@apisindia.com', 'name': 'New Joiner',
                                 'employee_code': 'E2', 'app_access': ['home', 'tada']})
        self.assertEqual(r.status_code, 200, r.content)
        u = PortalUser.objects.get(email='new@apisindia.com')
        self.assertEqual(u.app_access, ['home', 'tada'])

    def test_adding_with_no_tools_grants_no_tools(self):
        """An empty list is a deliberate "grant nothing". It used to be read as
        "not specified" and silently handed over the three defaults - so an
        administrator who unticked everything got the opposite of what they
        asked for."""
        r = self.post('users/', {'email': 'none@apisindia.com', 'name': 'No Tools',
                                 'employee_code': 'E5', 'app_access': []})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(PortalUser.objects.get(email='none@apisindia.com').app_access, [])

    def test_adding_without_mentioning_tools_uses_the_defaults(self):
        """Not saying anything is different from saying none."""
        r = self.post('users/', {'email': 'def@apisindia.com', 'name': 'Defaults',
                                 'employee_code': 'E6'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(PortalUser.objects.get(email='def@apisindia.com').app_access)

    def test_removing_needs_the_name_typed(self):
        """A yes/no dialog is muscle memory; typing the name is not."""
        r = self.remove(self.other, 'wrong')
        self.assertEqual(r.status_code, 400)
        self.assertTrue(PortalUser.objects.filter(id=self.other.id).exists())

    def test_removing_works_with_the_right_name(self):
        r = self.remove(self.other, 'some one')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertFalse(PortalUser.objects.filter(id=self.other.id).exists())

    def test_the_founding_account_cannot_be_removed(self):
        r = self.remove(self.admin, self.admin.name)
        self.assertEqual(r.status_code, 400)

    def test_removing_an_hrms_person_warns_they_come_back(self):
        self.other.from_hrms = True
        self.other.save()
        r = self.remove(self.other, self.other.name)
        self.assertTrue(r.json()['warning'],
                        'silently reappearing on the next sync is worse than a warning')

    # -- many at once -------------------------------------------------------
    def test_a_tool_can_be_granted_to_several_people(self):
        third = PortalUser.objects.create(email='c@apisindia.com', employee_code='E3',
                                          name='Third', app_access=[])
        r = self.post('bulk-access/', {'user_ids': [self.other.id, third.id],
                                       'action': 'grant', 'app': 'tada'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['changed'], 2)
        for u in (self.other, third):
            u.refresh_from_db()
            self.assertIn('tada', u.app_access)

    def test_revoking_in_bulk(self):
        self.post('bulk-access/', {'user_ids': [self.other.id], 'action': 'grant', 'app': 'tada'})
        self.post('bulk-access/', {'user_ids': [self.other.id], 'action': 'revoke', 'app': 'tada'})
        self.other.refresh_from_db()
        self.assertNotIn('tada', self.other.app_access)

    def test_an_unknown_tool_is_refused(self):
        r = self.post('bulk-access/', {'user_ids': [self.other.id],
                                       'action': 'grant', 'app': 'not-a-tool'})
        self.assertEqual(r.status_code, 400)

    def test_bulk_disable_skips_the_founding_account(self):
        r = self.post('bulk-access/', {'user_ids': [self.admin.id, self.other.id],
                                       'action': 'disable'})
        self.assertEqual(r.status_code, 200, r.content)
        self.admin.refresh_from_db()
        self.other.refresh_from_db()
        self.assertTrue(self.admin.is_active, 'the way back in must survive a bulk action')
        self.assertFalse(self.other.is_active)
        self.assertTrue(r.json()['skipped'])

    def test_none_of_this_is_open_to_a_normal_user(self):
        plain = PortalUser.objects.create(email='p@apisindia.com', employee_code='E4',
                                          name='Plain')
        auth = {'HTTP_AUTHORIZATION': f'Bearer {PortalSession.start(plain)}'}
        self.assertEqual(self.client.patch(
            f'/api/accounts/portal/admin/users/{self.other.id}/', {'name': 'Hacked'},
            content_type='application/json', **auth).status_code, 403)
        self.assertEqual(self.remove(self.other, 'Some One', auth).status_code, 403)
        self.assertEqual(self.client.post(
            '/api/accounts/portal/admin/bulk-access/',
            {'user_ids': [self.other.id], 'action': 'grant', 'app': 'tada'},
            content_type='application/json', **auth).status_code, 403)


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)[:300]

    def json(self):
        return self._payload


@override_settings(POCKET_HRMS_TOKEN='test-token', POCKET_HRMS_BASE_URL='https://hrms.example')
class HrmsFieldDiscovery(TestCase):
    """The vendor's own recommended fix for the "wrong field name" problem:
    call the API with no EmployeeFields header, and it returns whatever
    columns are actually configured for this tenant. Guessing at EmailId vs
    Email is what caused the original sync failures.
    """

    def test_a_normal_call_asks_for_the_configured_fields(self):
        with mock.patch('accounts.services.hrms.requests.get') as get:
            get.return_value = FakeResponse([])
            hrms.fetch_page(take=5, fields=['Id', 'Code'])
        headers = get.call_args.kwargs['headers']
        self.assertEqual(headers['EmployeeFields'], 'Id,Code')

    def test_fields_none_omits_the_header_entirely(self):
        """Not an empty header - genuinely absent, which is what makes this
        the discovery call rather than a request for zero columns."""
        with mock.patch('accounts.services.hrms.requests.get') as get:
            get.return_value = FakeResponse([])
            hrms.fetch_page(take=5, fields=None)
        headers = get.call_args.kwargs['headers']
        self.assertNotIn('EmployeeFields', headers)

    def test_discover_fields_reads_back_whatever_the_tenant_actually_has(self):
        """The whole point: this tenant might call it OfficialEmail, not
        EmailId or Email - discovery is how that gets found out instead of
        guessed at."""
        sample = [{'Id': '1', 'Code': 'E1', 'OfficialEmail': 'a@apisindia.com'},
                  {'Id': '2', 'Code': 'E2', 'OfficialEmail': 'b@apisindia.com',
                   'MobileNo': '9999999999'}]
        with mock.patch('accounts.services.hrms.requests.get') as get:
            get.return_value = FakeResponse(sample)
            columns, rows = hrms.discover_fields(sample_size=2)
        self.assertEqual(columns, ['Code', 'Id', 'MobileNo', 'OfficialEmail'])
        self.assertEqual(rows, sample)
        # And the call that produced it used no EmployeeFields header.
        self.assertNotIn('EmployeeFields', get.call_args.kwargs['headers'])

    def test_a_configured_override_replaces_the_guessed_defaults(self):
        """Once discover_fields() has told someone the real names, they go in
        POCKET_HRMS_EMPLOYEE_FIELDS rather than a code change."""
        with override_settings(POCKET_HRMS_EMPLOYEE_FIELDS='Id,OfficialEmail,MobileNo'):
            fields = hrms._configured_fields()
        self.assertEqual(fields, ['Id', 'OfficialEmail', 'MobileNo'])

    def test_modified_date_is_sent_as_a_range_not_an_iso_date(self):
        """The vendor's own doc example (a bare ISO date) 400s. Confirmed
        correct by their support after we reported it - pinned here so a
        future "helpful" cleanup cannot quietly revert it."""
        with mock.patch('accounts.services.hrms.requests.get') as get:
            get.return_value = FakeResponse([])
            hrms.fetch_page(modified_since=date(2026, 1, 1), fields=['Id'])
        sent = get.call_args.kwargs['headers']['ModifiedDate']
        self.assertRegex(sent, r'^\d{2}/\d{2}/\d{4} - \d{2}/\d{2}/\d{4}$')
        self.assertTrue(sent.startswith('01/01/2026'))

    def test_a_500_is_reported_as_an_auth_problem_not_a_server_crash(self):
        """Their own quirk: an unauthenticated or bad-token call answers 500,
        not 401/403."""
        with mock.patch('accounts.services.hrms.requests.get') as get:
            get.return_value = FakeResponse({}, status=500)
            with self.assertRaises(hrms.HrmsError) as e:
                hrms.fetch_page(fields=['Id'])
        self.assertIn('token', str(e.exception).lower())


class SyncHrmsCommand(TestCase):
    """The management command a cron job runs. No token exists to test the
    real Pocket HRMS call, so sync_employees itself is mocked here - what
    matters for a scheduled job is that it never crashes with a traceback,
    that a real failure still exits non-zero for cron's own monitoring to see,
    and that success is logged the same way a manual click is.
    """

    @override_settings(POCKET_HRMS_TOKEN='')
    def test_an_unconfigured_token_exits_cleanly_not_as_an_error(self):
        """A cron job must not treat "nobody has set this up yet" as a
        failure worth paging anyone about."""
        out = io.StringIO()
        call_command('sync_hrms', stdout=out)
        self.assertIn('not configured', out.getvalue())

    @override_settings(POCKET_HRMS_TOKEN='test-token')
    def test_a_successful_sync_is_logged_same_as_a_manual_one(self):
        fake_log = HrmsSyncLog.objects.create(
            triggered_by='cron', ok=True, fetched=3, created=1, updated=2,
            message='1 created, 2 updated, 0 deactivated, 0 skipped (no email).')
        with mock.patch('accounts.services.hrms.sync_employees', return_value=fake_log) as m:
            out = io.StringIO()
            call_command('sync_hrms', stdout=out)
        self.assertEqual(m.call_args.kwargs['triggered_by'], 'cron')
        self.assertIn('1 created', out.getvalue())

    @override_settings(POCKET_HRMS_TOKEN='test-token')
    def test_a_real_failure_exits_non_zero_for_cron_to_notice(self):
        with mock.patch('accounts.services.hrms.sync_employees',
                        side_effect=hrms.HrmsError('token rejected')):
            with self.assertRaises(CommandError):
                call_command('sync_hrms')

    @override_settings(POCKET_HRMS_TOKEN='test-token')
    def test_since_days_is_passed_through_as_a_date(self):
        with mock.patch('accounts.services.hrms.sync_employees') as m:
            m.return_value = HrmsSyncLog.objects.create(triggered_by='cron', ok=True,
                                                         message='ok')
            call_command('sync_hrms', '--since-days', '2')
        self.assertIsNotNone(m.call_args.kwargs['modified_since'])

    @override_settings(POCKET_HRMS_TOKEN='test-token')
    def test_without_since_days_it_runs_the_full_unfiltered_sync(self):
        """The scheduled job's whole point includes catching leavers, and that
        only happens on a full sync - modified_since must default to None,
        not to "today" or some other accidental value."""
        with mock.patch('accounts.services.hrms.sync_employees') as m:
            m.return_value = HrmsSyncLog.objects.create(triggered_by='cron', ok=True,
                                                         message='ok')
            call_command('sync_hrms')
        self.assertIsNone(m.call_args.kwargs['modified_since'])
