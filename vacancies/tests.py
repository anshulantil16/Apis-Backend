from django.test import TestCase

from accounts.models import PortalSession, PortalUser
from accounts.moderation import ModerationStatus

from .models import Vacancy

URL = '/api/vacancies/'
CONFIDENTIAL = URL + '?scope=confidential'

ROLE = {'title': 'Regional Head', 'function': 'Sales', 'department': 'SALES',
        'location': 'Mumbai', 'state': 'Maharashtra'}


class ConfidentialVacancies(TestCase):
    def setUp(self):
        self.admin = PortalUser.objects.create(email='a@apisindia.com', employee_code='A1',
                                               name='Admin', is_superadmin=True)
        self.hr = PortalUser.objects.create(email='hr@apisindia.com', employee_code='H1',
                                            name='HR', can_view_confidential_vacancies=True)
        self.staff = PortalUser.objects.create(email='s@apisindia.com', employee_code='S1',
                                               name='Staff')
        self.secret = Vacancy.objects.create(**ROLE, confidential=True,
                                             moderation_status=ModerationStatus.APPROVED)
        self.open = Vacancy.objects.create(**{**ROLE, 'title': 'Sales Executive'},
                                           moderation_status=ModerationStatus.APPROVED)

    def _auth(self, user):
        return {'HTTP_AUTHORIZATION': f'Bearer {PortalSession.start(user)}'}

    def titles(self, url, user=None):
        r = self.client.get(url, **(self._auth(user) if user else {}))
        return r.status_code, [v['title'] for v in r.json()] if r.status_code == 200 else None

    def test_the_ordinary_list_never_contains_them_for_anyone(self):
        # This list feeds the dashboard card and the referral form's dropdown.
        # (The seeded hiring plan is in the list too, hence membership checks.)
        for user in (None, self.staff, self.hr, self.admin):
            status, titles = self.titles(URL, user)
            self.assertEqual(status, 200)
            self.assertIn('Sales Executive', titles)
            self.assertNotIn('Regional Head', titles, user)
        self.assertFalse(Vacancy.published().filter(confidential=True).exists())

    def test_only_superadmins_and_grant_holders_can_read_them(self):
        self.assertEqual(self.titles(CONFIDENTIAL)[0], 401)
        self.assertEqual(self.titles(CONFIDENTIAL, self.staff)[0], 403)
        self.assertEqual(self.titles(CONFIDENTIAL, self.hr), (200, ['Regional Head']))
        self.assertEqual(self.titles(CONFIDENTIAL, self.admin), (200, ['Regional Head']))

    def test_grant_holder_can_add_one_and_it_is_live_but_still_hidden(self):
        r = self.client.post(URL, {**ROLE, 'title': 'CFO', 'confidential': True},
                             content_type='application/json', **self._auth(self.hr))
        self.assertEqual(r.status_code, 201, r.content)
        v = Vacancy.objects.get(title='CFO')
        self.assertTrue(v.confidential)
        self.assertEqual(v.moderation_status, ModerationStatus.APPROVED)
        self.assertNotIn('CFO', self.titles(URL, self.staff)[1])
        self.assertIn('CFO', self.titles(CONFIDENTIAL, self.hr)[1])

    def test_someone_without_access_cannot_add_one(self):
        r = self.client.post(URL, {**ROLE, 'title': 'CFO', 'confidential': True},
                             content_type='application/json', **self._auth(self.staff))
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Vacancy.objects.filter(title='CFO').exists())

    def test_grant_holder_can_close_a_confidential_one_but_not_an_ordinary_one(self):
        url = f'{URL}{self.secret.id}/status/'
        r = self.client.patch(url, {'status': 'Closed'}, content_type='application/json',
                              **self._auth(self.hr))
        self.assertEqual(r.status_code, 200)
        r = self.client.patch(f'{URL}{self.open.id}/status/', {'status': 'Closed'},
                              content_type='application/json', **self._auth(self.hr))
        self.assertEqual(r.status_code, 403)
        r = self.client.patch(url, {'status': 'Active'}, content_type='application/json',
                              **self._auth(self.staff))
        self.assertEqual(r.status_code, 403)

    def test_superadmin_grants_and_revokes_access(self):
        url = f'/api/accounts/portal/admin/users/{self.staff.id}/'
        r = self.client.patch(url, {'can_view_confidential_vacancies': True},
                              content_type='application/json', **self._auth(self.admin))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json()['user']['can_view_confidential_vacancies'])
        self.assertEqual(self.titles(CONFIDENTIAL, self.staff)[0], 200)

        self.client.patch(url, {'can_view_confidential_vacancies': False},
                          content_type='application/json', **self._auth(self.admin))
        self.assertEqual(self.titles(CONFIDENTIAL, self.staff)[0], 403)

        # Not something a grant holder can hand on.
        r = self.client.patch(f'/api/accounts/portal/admin/users/{self.staff.id}/',
                              {'can_view_confidential_vacancies': True},
                              content_type='application/json', **self._auth(self.hr))
        self.assertIn(r.status_code, (401, 403))
