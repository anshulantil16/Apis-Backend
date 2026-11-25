"""The directory sync: what it fills in, and what it refuses to guess.

The facts these tests pin down were measured against the live HRMS tenant on
25-11-2026 and are spelled out in goalsetting/directory.py. The ones worth
protecting are the refusals -- an HOD name that matches nobody, or two
people, must leave the field EMPTY rather than pick one, because a wrong HOD
silently routes somebody's goals to a person who then approves them.
"""
from django.test import TestCase

from accounts.models import PortalUser

from .directory import sync_from_directory
from .models import EmployeeProfile
from .session import issue_session


def person(code, name, **kw):
    return PortalUser.objects.create(
        employee_code=code, name=name,
        email=kw.pop('email', f'{code.lower()}@apisindia.com'), **kw)


class Sync(TestCase):
    def setUp(self):
        # A small company: one HOD, one manager under them, two staff.
        person('HO0001', 'Arun Mishra', department='SALES', designation='HOD')
        person('HO0002', 'Shakil Ahmed', department='SALES',
               designation='REGIONAL SALES MANAGER',
               reporting_manager_code='HO0001', hod_name='Arun Mishra')
        person('HO0003', 'Ajay Mishra', department='SALES',
               designation='SALES OFFICER',
               reporting_manager_code='HO0002', hod_name='Arun Mishra')
        person('HO0004', 'Uma Pattanaik', department='SALES',
               designation='AREA SALES MANAGER',
               reporting_manager_code='HO0002', hod_name='Arun Mishra')

    def test_creates_everybody_once(self):
        r = sync_from_directory()
        self.assertEqual(r['created'], 4)
        self.assertEqual(r['updated'], 0)
        self.assertEqual(EmployeeProfile.objects.count(), 4)

    def test_running_it_twice_updates_rather_than_duplicates(self):
        sync_from_directory()
        r = sync_from_directory()
        self.assertEqual(r['created'], 0)
        self.assertEqual(r['updated'], 4)
        self.assertEqual(EmployeeProfile.objects.count(), 4)

    def test_manager_comes_through_as_a_code(self):
        sync_from_directory()
        row = EmployeeProfile.objects.get(employee_id='HO0003')
        self.assertEqual(row.reporting_manager_id, 'HO0002')

    def test_hod_name_is_resolved_to_a_code(self):
        sync_from_directory()
        row = EmployeeProfile.objects.get(employee_id='HO0003')
        self.assertEqual(row.hod_id, 'HO0001')

    def test_hod_matching_ignores_spacing_and_case(self):
        PortalUser.objects.filter(employee_code='HO0003').update(
            hod_name='arun   MISHRA')
        sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0003').hod_id, 'HO0001')

    def test_an_hod_name_matching_nobody_is_reported_not_guessed(self):
        PortalUser.objects.filter(employee_code='HO0003').update(
            hod_name='PANKAJ TRIPATHII')
        r = sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0003').hod_id, '')
        self.assertIn('PANKAJ TRIPATHII', [u['name'] for u in r['unresolved_hods']])

    def test_an_hod_name_two_people_share_is_left_empty(self):
        person('HO0009', 'Arun Mishra', department='OPS')   # a second one
        sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0003').hod_id, '')

    def test_na_in_the_hod_field_is_not_an_unresolved_name(self):
        PortalUser.objects.filter(employee_code='HO0003').update(hod_name='Na')
        r = sync_from_directory()
        self.assertEqual([u['name'] for u in r['unresolved_hods']], [])

    def test_somebody_listed_as_their_own_manager_gets_none(self):
        PortalUser.objects.filter(employee_code='HO0001').update(
            reporting_manager_code='HO0001')
        sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0001').reporting_manager_id, '')

    def test_a_manager_code_naming_nobody_is_dropped(self):
        PortalUser.objects.filter(employee_code='HO0003').update(
            reporting_manager_code='GONE999')
        sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0003').reporting_manager_id, '')


class Roles(TestCase):
    """A role is what somebody is to OTHER people, not a column."""

    def setUp(self):
        person('HO0001', 'Arun Mishra')
        person('HO0002', 'Shakil Ahmed',
               reporting_manager_code='HO0001', hod_name='Arun Mishra')
        person('HO0003', 'Ajay Mishra',
               reporting_manager_code='HO0002', hod_name='Arun Mishra')

    def test_somebody_named_as_an_hod_is_an_hod(self):
        sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0001').user_type, 'hod')

    def test_somebody_with_reports_is_a_manager(self):
        sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0002').user_type, 'manager')

    def test_everybody_else_is_an_employee(self):
        sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0003').user_type, 'employee')

    def test_an_admin_stays_an_admin(self):
        EmployeeProfile.objects.create(
            employee_id='HO0003', name='Ajay Mishra', user_type='admin')
        sync_from_directory()
        self.assertEqual(
            EmployeeProfile.objects.get(employee_id='HO0003').user_type, 'admin')


class HandEnteredRows(TestCase):
    """Somebody added here by hand is not in HRMS. A sync must leave them be."""

    def setUp(self):
        person('HO0001', 'Arun Mishra')

    def test_a_manual_row_is_not_touched(self):
        EmployeeProfile.objects.create(
            employee_id='CONTRACT-1', name='A Contractor',
            department='FACILITIES', source='manual')
        r = sync_from_directory()
        row = EmployeeProfile.objects.get(employee_id='CONTRACT-1')
        self.assertEqual(row.department, 'FACILITIES')
        self.assertTrue(row.is_active)
        self.assertEqual(row.source, 'manual')
        self.assertEqual(r['manual_untouched'], 1)

    def test_a_manual_row_is_not_deactivated_when_hrms_has_never_heard_of_them(self):
        EmployeeProfile.objects.create(
            employee_id='CONTRACT-1', name='A Contractor', source='manual')
        sync_from_directory()
        self.assertTrue(
            EmployeeProfile.objects.get(employee_id='CONTRACT-1').is_active)

    def test_a_leaver_is_deactivated_and_never_deleted(self):
        sync_from_directory()
        PortalUser.objects.filter(employee_code='HO0001').delete()
        r = sync_from_directory()
        self.assertEqual(r['deactivated'], 1)
        row = EmployeeProfile.objects.get(employee_id='HO0001')
        self.assertFalse(row.is_active)

    def test_an_existing_uploaded_row_is_adopted_rather_than_duplicated(self):
        # The spreadsheet got here first, with the same person under the same
        # code. The sync must update that row, not create a second one that
        # every manager/HOD lookup would then fail to find.
        EmployeeProfile.objects.create(
            employee_id='ho0001', name='A Mishra', source='manual')
        sync_from_directory()
        self.assertEqual(EmployeeProfile.objects.count(), 1)
        row = EmployeeProfile.objects.get()
        self.assertEqual(row.name, 'Arun Mishra')
        self.assertEqual(row.source, 'directory')


class OnlyPeopleWhoWorkHere(TestCase):
    """Ex-employees are in the directory and must not reach the goal master.

    They are kept in accounts.PortalUser, marked inactive, because their name
    is on things they did. Copying them in gave the People tab a tail of
    people who can never fill in a sheet. TADA has always filtered them out;
    this does now too.
    """

    def setUp(self):
        person('HO0001', 'Arun Mishra')
        person('HO0002', 'Shakil Ahmed', reporting_manager_code='HO0001',
               hod_name='Arun Mishra')
        self.gone = person('HO0003', 'Someone Who Left',
                           reporting_manager_code='HO0001',
                           hod_name='Arun Mishra', is_active=False)

    def test_somebody_who_has_left_is_not_imported(self):
        r = sync_from_directory()
        self.assertEqual(r['total'], 2)
        self.assertFalse(
            EmployeeProfile.objects.filter(employee_id='HO0003').exists())

    def test_somebody_who_leaves_later_is_deactivated_not_deleted(self):
        PortalUser.objects.filter(employee_code='HO0003').update(is_active=True)
        sync_from_directory()
        self.assertTrue(EmployeeProfile.objects.get(employee_id='HO0003').is_active)

        PortalUser.objects.filter(employee_code='HO0003').update(is_active=False)
        r = sync_from_directory()
        self.assertEqual(r['deactivated'], 1)
        row = EmployeeProfile.objects.get(employee_id='HO0003')
        self.assertFalse(row.is_active)

    def test_a_manager_who_has_left_does_not_stay_on_their_reports(self):
        # HO0001 leaves. HO0002 reported to them, and a sheet routed to
        # somebody who does not work here waits forever.
        PortalUser.objects.filter(employee_code='HO0001').update(is_active=False)
        sync_from_directory()
        row = EmployeeProfile.objects.get(employee_id='HO0002')
        self.assertEqual(row.reporting_manager_id, '')
        self.assertEqual(row.hod_id, '')

    def test_it_can_still_be_asked_for_everybody(self):
        r = sync_from_directory(only_active=False)
        self.assertEqual(r['total'], 3)


class Endpoint(TestCase):
    def setUp(self):
        person('HO0001', 'Arun Mishra')
        admin = EmployeeProfile.objects.create(
            employee_id='GS-ADMIN', name='Goal Setting Admin', user_type='admin')
        self.token = issue_session(admin)

    def test_an_admin_can_sync(self):
        r = self.client.post('/api/goalsetting/employees/sync/',
                             HTTP_X_GOALSETTING_SESSION=self.token)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['created'], 1)

    def test_a_stranger_cannot(self):
        r = self.client.post('/api/goalsetting/employees/sync/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(EmployeeProfile.objects.filter(source='directory').count(), 0)

    def test_an_ordinary_employee_cannot(self):
        emp = EmployeeProfile.objects.create(
            employee_id='E1', name='Someone', user_type='employee')
        r = self.client.post('/api/goalsetting/employees/sync/',
                             HTTP_X_GOALSETTING_SESSION=issue_session(emp))
        self.assertEqual(r.status_code, 403)
