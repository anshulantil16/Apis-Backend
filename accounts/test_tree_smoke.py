"""Who may change APIS Tree, and what "change" means.

The endpoints arrived with no tests of their own, so the first class is the
minimum: the routes resolve, an unsigned caller is refused, any signed-in
employee may read.

The second is the thing that matters. One grant used to carry three powers --
correct a card, add a person, take one off the chart -- and they are not the
same size. Correcting a misspelt designation is routine and worth delegating
widely; removing somebody is structural and visible to the whole company.
Held together, whoever could fix a typo could also delete the managing
director's card.
"""
from django.test import TestCase

from accounts.models import PortalSession, PortalUser, TreeProfile

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
                             {'name': 'Staff Member', 'role': 'Analyst'},
                             content_type='application/json', **auth(self.boss))
        self.assertIn(r.status_code, (200, 201), r.content[:300])
        back = self.client.get(f'{API}/tree/profiles/', **auth(self.boss)).json()
        self.assertTrue(back['profiles'], back)


class EditingACardIsNotTheSameAsRemovingSomebody(TestCase):
    """Two grants, because the two jobs are not the same size."""

    def setUp(self):
        self.boss = PortalUser.objects.create(
            email='boss@apisindia.com', name='Boss', employee_code='E1',
            is_active=True, is_superadmin=True)
        # May correct cards, and nothing more.
        self.editor = PortalUser.objects.create(
            email='editor@apisindia.com', name='Editor', employee_code='E2',
            is_active=True, can_edit_tree=True)
        # May change who is on the chart.
        self.manager = PortalUser.objects.create(
            email='manager@apisindia.com', name='Manager', employee_code='E3',
            is_active=True, can_manage_tree=True)

    def patch(self, pid, body, who):
        return self.client.patch(f'{API}/tree/profiles/{pid}/', body,
                                 content_type='application/json', **auth(who))

    # -- what an editor may do ---------------------------------------------

    def test_an_editor_can_still_correct_a_card(self):
        r = self.patch('e9', {'role': 'Senior Analyst'}, self.editor)
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(TreeProfile.objects.get(person_id='e9').role,
                         'Senior Analyst')

    def test_an_editor_cannot_add_a_person(self):
        r = self.client.post(f'{API}/tree/profiles/', {'name': 'New Joiner'},
                             content_type='application/json', **auth(self.editor))
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.assertIn('not add or remove', r.json()['error'])

    def test_an_editor_cannot_take_somebody_off_the_chart(self):
        r = self.patch('e9', {'hidden': True}, self.editor)
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.assertFalse(
            TreeProfile.objects.filter(person_id='e9', is_hidden=True).exists())

    def test_an_editor_cannot_delete_an_override(self):
        TreeProfile.objects.create(person_id='e9', name='Someone')
        r = self.client.delete(f'{API}/tree/profiles/e9/', **auth(self.editor))
        self.assertEqual(r.status_code, 403, r.content[:300])
        self.assertTrue(TreeProfile.objects.filter(person_id='e9').exists())

    def test_an_ordinary_edit_mentioning_hidden_is_not_refused(self):
        """The page sends the whole card back on a save, `hidden` included.
        Refusing that would break every edit an editor makes."""
        TreeProfile.objects.create(person_id='e9', name='Someone', is_hidden=False)
        r = self.patch('e9', {'name': 'Someone Else', 'hidden': False}, self.editor)
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(TreeProfile.objects.get(person_id='e9').name,
                         'Someone Else')

    # -- what a manager may do ---------------------------------------------

    def test_a_manager_can_add_and_remove(self):
        r = self.client.post(f'{API}/tree/profiles/', {'name': 'New Joiner'},
                             content_type='application/json', **auth(self.manager))
        self.assertEqual(r.status_code, 201, r.content[:300])
        pid = r.json()['profile']['person_id']
        r = self.client.delete(f'{API}/tree/profiles/{pid}/', **auth(self.manager))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertFalse(TreeProfile.objects.filter(person_id=pid).exists())

    def test_a_manager_can_hide_one_of_the_charts_own_people(self):
        r = self.patch('e9', {'hidden': True}, self.manager)
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertTrue(TreeProfile.objects.get(person_id='e9').is_hidden)

    def test_managing_carries_editing_with_it(self):
        """Somebody trusted to add a person cannot sensibly be barred from
        correcting their title afterwards."""
        self.assertFalse(self.manager.can_edit_tree)
        r = self.patch('e9', {'role': 'Analyst'}, self.manager)
        self.assertEqual(r.status_code, 200, r.content[:300])

    def test_a_superadmin_needs_neither_grant(self):
        self.assertFalse(self.boss.can_edit_tree)
        self.assertFalse(self.boss.can_manage_tree)
        r = self.client.post(f'{API}/tree/profiles/', {'name': 'New Joiner'},
                             content_type='application/json', **auth(self.boss))
        self.assertEqual(r.status_code, 201, r.content[:300])

    # -- somebody with neither ---------------------------------------------

    def test_an_ordinary_employee_can_read_but_not_write(self):
        plain = PortalUser.objects.create(
            email='plain@apisindia.com', name='Plain', employee_code='E4',
            is_active=True)
        self.assertEqual(
            self.client.get(f'{API}/tree/profiles/', **auth(plain)).status_code,
            200)
        self.assertEqual(self.patch('e9', {'role': 'x'}, plain).status_code, 403)

    # -- only a super admin hands either of them out -----------------------

    def test_an_editor_cannot_grant_themselves_the_bigger_one(self):
        r = self.client.patch(f'{API}/portal/admin/users/{self.editor.id}/',
                              {'can_manage_tree': True},
                              content_type='application/json', **auth(self.editor))
        self.assertIn(r.status_code, (401, 403), r.status_code)
        self.editor.refresh_from_db()
        self.assertFalse(self.editor.can_manage_tree)

    def test_a_superadmin_grants_and_revokes_it_from_the_console(self):
        url = f'{API}/portal/admin/users/{self.editor.id}/'
        r = self.client.patch(url, {'can_manage_tree': True},
                              content_type='application/json', **auth(self.boss))
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.editor.refresh_from_db()
        self.assertTrue(self.editor.can_manage_tree)

        self.client.patch(url, {'can_manage_tree': False},
                          content_type='application/json', **auth(self.boss))
        self.editor.refresh_from_db()
        self.assertFalse(self.editor.can_manage_tree)
        # Revoking the bigger grant leaves the smaller one alone: they can
        # still correct a card, which is what they had before.
        self.assertTrue(self.editor.can_edit_tree)


class WhatANewAccountStartsWith(TestCase):
    """DEFAULT_APPS is read from the environment, because the answer differs
    between a server the team is testing on and one the whole company is
    about to sign in to for the first time.

    On the day the portal opens, every grant should be a decision somebody
    made. A default that quietly hands four tools to 500 people is not that.
    """

    def _reload(self, value):
        """Re-evaluate the module-level list under a given env var."""
        import os
        from accounts import models as m
        old = os.environ.get('PORTAL_DEFAULT_APPS')
        if value is None:
            os.environ.pop('PORTAL_DEFAULT_APPS', None)
        else:
            os.environ['PORTAL_DEFAULT_APPS'] = value
        try:
            return m._default_apps()
        finally:
            if old is None:
                os.environ.pop('PORTAL_DEFAULT_APPS', None)
            else:
                os.environ['PORTAL_DEFAULT_APPS'] = old

    def test_unset_keeps_the_reference_pages(self):
        from accounts.models import AppKey
        apps = self._reload(None)
        self.assertIn(AppKey.HOME, apps)
        self.assertIn(AppKey.POLICIES, apps)

    def test_empty_means_nobody_starts_with_anything(self):
        self.assertEqual(self._reload(''), [])

    def test_a_named_list_is_honoured(self):
        self.assertEqual(self._reload('home,policies'), ['home', 'policies'])

    def test_a_typo_is_dropped_not_stored(self):
        """A misspelt key in a server's .env must not put a grant in the
        database for a tool that does not exist."""
        self.assertEqual(self._reload('home,salesiq-typo,policies'),
                         ['home', 'policies'])

    def test_whitespace_is_forgiven(self):
        self.assertEqual(self._reload(' home , policies '), ['home', 'policies'])
