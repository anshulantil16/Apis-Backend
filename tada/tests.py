"""The TA/DA directory comes from the HRMS master, not a spreadsheet.

Everybody who works here is in the portal directory already. These tests are
about the things the sync must never do, both of which would stay invisible
until somebody was paid the wrong allowance or a request reached nobody.
"""
from django.test import TestCase

from accounts.models import PortalSession, PortalUser
from .models import TadaUser
from .sync import sync_from_master


class TheDirectoryComesFromTheMaster(TestCase):
    def setUp(self):
        self.p = PortalUser.objects.create(
            employee_code='HO0101', email='asha@apisindia.com', name='Asha Rao',
            designation='Area Sales Manager', department='GENERAL TRADE',
            location='Delhi HO', grade='M4', reporting_manager_code='HO0235')

    def test_an_employee_arrives_with_no_spreadsheet(self):
        out = sync_from_master()
        self.assertEqual(out['created'], 1)
        u = TadaUser.objects.get(employee_id='HO0101')
        self.assertEqual((u.name, u.email), ('Asha Rao', 'asha@apisindia.com'))
        self.assertEqual(u.department, 'GENERAL TRADE')
        self.assertEqual(u.grade, 'M4')
        self.assertEqual(u.role, 'employee', 'a role is decided here, not in HRMS')

    def test_hrms_details_are_refreshed_every_run(self):
        sync_from_master()
        self.p.designation = 'Senior Area Sales Manager'
        self.p.save()
        sync_from_master()
        self.assertEqual(TadaUser.objects.get(employee_id='HO0101').designation,
                         'Senior Area Sales Manager')

    def test_a_role_set_here_survives_the_next_sync(self):
        """HRMS has no notion of who signs off Finance. If a sync could reset
        a role, every approver would silently become an employee overnight."""
        sync_from_master()
        u = TadaUser.objects.get(employee_id='HO0101')
        u.role = 'finance'
        u.save()
        sync_from_master()
        self.assertEqual(TadaUser.objects.get(employee_id='HO0101').role, 'finance')

    def test_a_level_set_by_hand_is_never_replaced(self):
        """The level decides the allowance. Somebody set it deliberately, and
        a sync overwriting it would quietly change what people are paid."""
        sync_from_master()
        u = TadaUser.objects.get(employee_id='HO0101')
        u.level = 'M6'
        u.save()
        self.p.grade = 'M4'
        self.p.save()
        sync_from_master()
        self.assertEqual(TadaUser.objects.get(employee_id='HO0101').level, 'M6')

    def test_a_grade_that_is_not_a_tada_level_does_not_become_one(self):
        """HRMS grades are a different alphabet: O2, W1, C1, 05, D1. Only the
        ones that already are a TA/DA level are copied, because guessing the
        rest pays people at the wrong rate."""
        for grade in ('O2', 'W1', 'C1', '05', 'D1'):
            TadaUser.objects.all().delete()
            self.p.grade = grade
            self.p.save()
            sync_from_master()
            self.assertEqual(TadaUser.objects.get(employee_id='HO0101').level, '',
                             grade + ' is not a TA/DA level')

    def test_a_grade_that_is_a_level_is_used(self):
        for grade in ('M1', 'M7', 'E1', 'E4'):
            TadaUser.objects.all().delete()
            self.p.grade = grade
            self.p.save()
            sync_from_master()
            self.assertEqual(TadaUser.objects.get(employee_id='HO0101').level, grade)

    def test_an_approver_is_never_blanked_by_an_empty_feed(self):
        """The one that would have done the most damage. reporting_manager_id
        is what routes a request to whoever approves it, and HRMS holds a
        manager for 3 of 717 people. Copying that column over wholesale would
        leave everybody else's travel with nobody to approve it."""
        sync_from_master()
        u = TadaUser.objects.get(employee_id='HO0101')
        u.reporting_manager_id = 'HO0999'
        u.save()
        self.p.reporting_manager_code = ''          # HRMS has nobody for them
        self.p.save()
        sync_from_master()
        self.assertEqual(TadaUser.objects.get(employee_id='HO0101').reporting_manager_id,
                         'HO0999')

    def test_somebody_who_has_left_cannot_claim(self):
        sync_from_master()
        self.p.is_active = False
        self.p.save()
        out = sync_from_master(only_active=False)
        self.assertEqual(out['deactivated'], 1)
        self.assertFalse(TadaUser.objects.get(employee_id='HO0101').is_active)

    def test_people_without_a_level_are_counted_so_they_can_be_fixed(self):
        self.p.grade = 'O2'
        self.p.save()
        out = sync_from_master()
        self.assertEqual(len(out['needs_level']), 1)
        self.assertIn('HO0101', out['needs_level'][0])


class OnlyASuperAdminChangesRolesAndLevels(TestCase):
    def setUp(self):
        PortalUser.objects.create(employee_code='HO0101', email='a@apisindia.com',
                                  name='Asha', grade='M4')
        PortalUser.objects.create(employee_code='HO0235', email='m@apisindia.com',
                                  name='Manager', grade='M6')
        sync_from_master()
        self.u = TadaUser.objects.get(employee_id='HO0101')
        self.plain = PortalUser.objects.create(email='plain@apisindia.com',
                                               employee_code='X1', name='Plain')
        self.admin = PortalUser.objects.create(email='boss@apisindia.com', employee_code='X2',
                                               name='Boss', is_superadmin=True)

    def _patch(self, body, user=None):
        kw = {'HTTP_AUTHORIZATION': 'Bearer ' + PortalSession.start(user)} if user else {}
        return self.client.patch('/api/tada/users/%d/' % self.u.id, body,
                                 content_type='application/json', **kw)

    def test_an_ordinary_employee_cannot_make_themselves_finance(self):
        self.assertEqual(self._patch({'role': 'finance'}, self.plain).status_code, 403)
        self.assertEqual(self._patch({'role': 'finance'}).status_code, 401)
        self.u.refresh_from_db()
        self.assertEqual(self.u.role, 'employee')

    def test_a_super_admin_sets_the_role_here_rather_than_in_a_spreadsheet(self):
        r = self._patch({'role': 'finance'}, self.admin)
        self.assertEqual(r.status_code, 200)
        self.u.refresh_from_db()
        self.assertEqual(self.u.role, 'finance')

    def test_a_level_that_is_not_on_the_matrix_is_refused(self):
        # M8 and M9 are NOT invalid: the matrix treats M7 and above as one
        # band, so band_for_level answers M7+ for them. The ones with no
        # band are what must be refused -- stored, they would pay nothing.
        for bad in ('X3', 'M0', 'E0', 'manager'):
            r = self._patch({'level': bad}, self.admin)
            self.assertEqual(r.status_code, 400, bad)
            self.assertIn('not a TA/DA level', r.json()['error'])
        self.u.refresh_from_db()
        self.assertEqual(self.u.level, 'M4', 'unchanged, not stored as nonsense')

    def test_a_level_above_m7_is_accepted_because_the_matrix_has_a_band(self):
        r = self._patch({'level': 'M8'}, self.admin)
        self.assertEqual(r.status_code, 200)
        self.u.refresh_from_db()
        self.assertEqual(self.u.level, 'M8')

    def test_nobody_approves_their_own_travel(self):
        r = self._patch({'reporting_manager_id': self.u.employee_id}, self.admin)
        self.assertEqual(r.status_code, 400)

    def test_an_approver_who_is_not_in_the_directory_is_refused(self):
        r = self._patch({'reporting_manager_id': 'NOBODY1'}, self.admin)
        self.assertEqual(r.status_code, 400)
        self.assertIn('nothing would reach them', r.json()['error'])

    def test_a_real_approver_is_accepted(self):
        r = self._patch({'reporting_manager_id': 'HO0235'}, self.admin)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['user']['reporting_manager_name'], 'Manager')

    def test_hrms_owned_details_cannot_be_edited_here(self):
        self._patch({'name': 'Someone Else', 'department': 'Nope'}, self.admin)
        self.u.refresh_from_db()
        self.assertEqual(self.u.name, 'Asha', 'the master owns this')


class TheDirectoryListIsReachableAndGated(TestCase):
    """The list is what the admin table reads, and it went out as a 401.

    It was a plain APIView while the screen called it with a portal Bearer
    token, so SimpleJWT tried to parse that token as a JWT, failed, and
    answered 401 before any of the view ran. Nothing tested it with a real
    token, so nothing caught it -- the browser did, as a blank page.
    """

    def setUp(self):
        PortalUser.objects.create(employee_code='HO0101', email='a@apisindia.com',
                                  name='Asha', grade='M4')
        sync_from_master()
        self.plain = PortalUser.objects.create(email='p@apisindia.com', employee_code='P1',
                                               name='Plain')
        self.admin = PortalUser.objects.create(email='b@apisindia.com', employee_code='P2',
                                               name='Boss', is_superadmin=True)

    def _get(self, user=None):
        kw = {'HTTP_AUTHORIZATION': 'Bearer ' + PortalSession.start(user)} if user else {}
        return self.client.get('/api/tada/users/', **kw)

    def test_a_super_admin_token_is_accepted_not_mangled_into_a_401(self):
        r = self._get(self.admin)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body['total'], 1)
        self.assertIn('users', body, 'the screen reads body.users and crashes without it')
        self.assertEqual(body['users'][0]['employee_id'], 'HO0101')

    def test_the_employee_directory_is_not_handed_to_anyone_who_asks(self):
        self.assertEqual(self._get().status_code, 401)
        self.assertEqual(self._get(self.plain).status_code, 403)


class TheImportIsGone(TestCase):
    def test_the_template_and_import_endpoints_no_longer_exist(self):
        self.assertEqual(self.client.get('/api/tada/users/template/').status_code, 404)
        self.assertEqual(self.client.post('/api/tada/users/import/').status_code, 404)


class TheApproverComesFromWhateverTheMasterHas(TestCase):
    """HRMS sends a reporting manager for almost nobody.

    The screen showed 716 people and three approvers, which is not a sync
    that went wrong -- it is the master having a reporting manager for three
    people. The master does hold a HOD and a leave chain as well, so the
    approver is taken from the first of those that is actually filled in,
    and which one it was is recorded rather than left to look like magic.
    """

    def setUp(self):
        self.boss = PortalUser.objects.create(employee_code='HO0235', name='Boss',
                                              email='boss@apisindia.com')
        self.p = PortalUser.objects.create(employee_code='HO0101', name='Asha',
                                           email='asha@apisindia.com', grade='M4')

    def _sync_and_get(self):
        sync_from_master()
        return TadaUser.objects.get(employee_id='HO0101')

    def test_the_reporting_manager_is_used_first(self):
        self.p.reporting_manager_code = 'HO0235'
        self.p.hod_code = 'HO0235'
        self.p.save()
        u = self._sync_and_get()
        self.assertEqual((u.reporting_manager_id, u.approver_source), ('HO0235', 'manager'))

    def test_the_hod_routes_the_request_when_there_is_no_manager(self):
        self.p.hod_code, self.p.hod_name = 'HO0235', 'Boss'
        self.p.save()
        u = self._sync_and_get()
        self.assertEqual((u.reporting_manager_id, u.approver_source), ('HO0235', 'hod'))
        self.assertEqual(u.hod_name, 'Boss', 'shown in its own column either way')

    def test_the_leave_chain_is_the_last_resort(self):
        self.p.leave_approver_code = 'HO0235'
        self.p.save()
        self.assertEqual(self._sync_and_get().approver_source, 'leave approver')

    def test_a_code_for_somebody_who_is_not_here_is_not_stored(self):
        """Worse than no approver: an unrouted request is visible on the
        admin screen, one routed at nobody waits in silence."""
        self.p.hod_code = 'GONE99'
        self.p.save()
        u = self._sync_and_get()
        self.assertEqual(u.reporting_manager_id, '')
        self.assertEqual(u.hod_id, 'GONE99', 'still recorded, just not routed through')

    def test_nobody_is_routed_to_themselves(self):
        self.p.hod_code = 'HO0101'
        self.p.save()
        self.assertEqual(self._sync_and_get().reporting_manager_id, '')

    def test_an_approver_set_by_hand_survives_a_master_that_now_has_one(self):
        self.p.hod_code = 'HO0235'
        self.p.save()
        u = self._sync_and_get()
        u.reporting_manager_id, u.approver_source = 'HO0999', 'set here'
        u.save()
        self.p.reporting_manager_code = 'HO0235'
        self.p.save()
        u = self._sync_and_get()
        self.assertEqual((u.reporting_manager_id, u.approver_source), ('HO0999', 'set here'))

    def test_a_hod_already_filled_in_is_not_wiped_by_a_thin_feed(self):
        self.p.hod_code, self.p.hod_name = 'HO0235', 'Boss'
        self.p.save()
        self._sync_and_get()
        self.p.hod_code, self.p.hod_name = '', ''
        self.p.save()
        self.assertEqual(self._sync_and_get().hod_name, 'Boss')

    def test_the_summary_says_how_many_are_still_unrouted(self):
        out = sync_from_master()
        self.assertEqual(out['no_approver'], 2, 'neither of them has anybody')
        self.p.hod_code = 'HO0235'
        self.p.save()
        out = sync_from_master()
        self.assertEqual(out['routed'], {'hod': 1})
        self.assertEqual(out['no_approver'], 1)


class ClearingEverythingIsNotOpenToTheWorld(TestCase):
    """POST /api/tada/admin/reset/ deleted every request, and with
    what='all' the entire directory, for any caller who knew the URL."""

    def test_an_anonymous_caller_cannot_delete_the_directory(self):
        PortalUser.objects.create(employee_code='HO0101', name='Asha',
                                  email='asha@apisindia.com')
        sync_from_master()
        r = self.client.post('/api/tada/admin/reset/', {'what': 'all'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(TadaUser.objects.count(), 1)

    def test_an_ordinary_employee_cannot_either(self):
        plain = PortalUser.objects.create(employee_code='P1', name='Plain',
                                          email='p@apisindia.com')
        r = self.client.post('/api/tada/admin/reset/', {'what': 'all'},
                             content_type='application/json',
                             HTTP_AUTHORIZATION='Bearer ' + PortalSession.start(plain))
        self.assertEqual(r.status_code, 403)
