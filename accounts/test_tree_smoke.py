"""Smoke test for the APIS Tree override endpoints.

They arrived with no tests of their own and are about to go to QA, so this
is the minimum: the routes resolve, they refuse an unsigned caller, and an
editor can write an override and read it back.
"""
from django.test import TestCase
from accounts.models import PortalUser, PortalSession

API = '/api/accounts'


def auth(user):
    return {'HTTP_AUTHORIZATION': f'Bearer {PortalSession.start(user)}'}


class TheTreeOverrideEndpoints(TestCase):
    def setUp(self):
        self.boss = PortalUser.objects.create(
            email='boss@apisindia.com', name='Boss', employee_code='E1',
            is_active=True, is_superadmin=True)
        self.staff = PortalUser.objects.create(
            email='staff@apisindia.com', name='Staff', employee_code='E2',
            is_active=True)

    def test_an_unsigned_caller_is_refused(self):
        r = self.client.get(f'{API}/tree/profiles/')
        self.assertIn(r.status_code, (401, 403), r.status_code)

    def test_a_signed_in_person_can_read_the_overrides(self):
        r = self.client.get(f'{API}/tree/profiles/', **auth(self.staff))
        self.assertEqual(r.status_code, 200, r.content[:300])

    def test_somebody_without_the_grant_cannot_edit(self):
        r = self.client.patch(f'{API}/tree/profiles/e2/',
                              {'name': 'Hacked'},
                              content_type='application/json', **auth(self.staff))
        self.assertIn(r.status_code, (401, 403), r.status_code)

    def test_a_superadmin_can_write_one_and_read_it_back(self):
        r = self.client.post(f'{API}/tree/profiles/',
                             {'person_id': 'e2', 'name': 'Staff Member', 'role': 'Analyst'},
                             content_type='application/json', **auth(self.boss))
        self.assertIn(r.status_code, (200, 201), r.content[:300])
        back = self.client.get(f'{API}/tree/profiles/', **auth(self.boss)).json()
        self.assertTrue(back, back)

    def test_the_grant_is_enough_without_being_superadmin(self):
        self.staff.can_edit_tree = True
        self.staff.save(update_fields=['can_edit_tree'])
        r = self.client.post(f'{API}/tree/profiles/',
                             {'person_id': 'e2', 'name': 'Staff Member'},
                             content_type='application/json', **auth(self.staff))
        self.assertIn(r.status_code, (200, 201), r.content[:300])
