"""Goal Setting Hub: the workflow, and the history that makes it trustworthy.

The product's promise is that a manager and an HOD can rewrite someone's goals
AND that the employee can still see exactly what they originally proposed. Both
halves are tested here: who may edit at each stage, and that no version is ever
lost or altered.
"""
from django.test import TestCase

from .models import (EmployeeProfile, GoalCycle, GoalPlan, PlanVersion,
                     diff_snapshots)
from .services import WorkflowError, advance, get_or_create_plan, readiness, save_kras

BASE = '/api/goalsetting'


def kpi(metric, weight, **over):
    """A complete KPI - every field readiness() insists on."""
    return {'metric': metric, 'weightage': weight, 'frequency': 'Monthly',
            'unit_of_measurement': 'INR', 'parameter_type': 'Higher is better',
            'data_source': 'SAP', 'target_value': '100', **over}


def sheet(*pairs):
    """One KRA per category, carrying the weights given."""
    cats = ['Financial', 'Customer Enhancement', 'Internal Business Process',
            'People Development']
    return [{'category': cats[i], 'title': f'KRA {i + 1}',
             'kpis': [kpi(m, w)]}
            for i, (m, w) in enumerate(pairs)]


FULL = sheet(('Primary sales', 40), ('Retail coverage', 30),
             ('Process audit', 20), ('Team training', 10))


class Fixture(TestCase):
    def setUp(self):
        self.cycle = GoalCycle.objects.create(name='FY26 Goals', fiscal_year='2025-26',
                                              status='open')
        self.emp = EmployeeProfile.objects.create(employee_id='E1', name='Rahul',
                                                  email='rahul@apisindia.com',
                                                  reporting_manager_id='M1', hod_id='H1')
        self.mgr = EmployeeProfile.objects.create(employee_id='M1', name='Arun',
                                                  user_type='manager', hod_id='H1')
        self.hod = EmployeeProfile.objects.create(employee_id='H1', name='Narendra',
                                                  user_type='hod')
        self.plan = get_or_create_plan(self.emp, self.cycle)

    def fill(self, rows=None):
        save_kras(self.plan, rows if rows is not None else FULL)
        self.plan.refresh_from_db()
        return self.plan

    def post(self, path, body):
        return self.client.post(BASE + path, body, content_type='application/json')


class SheetRules(Fixture):
    def test_a_new_plan_starts_empty(self):
        """The form supplies the four category headings and their empty states;
        the plan itself holds nothing until the employee writes something."""
        self.assertEqual(self.plan.kras.count(), 0)

    def test_empty_rows_are_not_saved(self):
        save_kras(self.plan, [{'category': 'Financial', 'title': '',
                               'kpis': [kpi('', 0, frequency='', unit_of_measurement='',
                                            parameter_type='', data_source='',
                                            target_value='')]}])
        self.assertEqual(self.plan.kras.count(), 0)

    def test_weightage_must_total_one_hundred(self):
        self.fill(sheet(('A', 40), ('B', 30), ('C', 20), ('D', 5)))
        problems = readiness(self.plan)
        self.assertTrue(any('95' in p and '100%' in p for p in problems), problems)

    def test_a_complete_sheet_has_no_problems(self):
        self.fill()
        self.assertEqual(readiness(self.plan), [])

    def test_missing_fields_are_all_reported_at_once(self):
        """Four trips back for four blank fields is how a tool gets hated."""
        self.fill([{'category': 'Financial', 'title': 'KRA 1',
                    'kpis': [kpi('Sales', 100, frequency='', data_source='')]}])
        problems = readiness(self.plan)
        joined = ' '.join(problems)
        self.assertIn('Frequency', joined)
        self.assertIn('Data Source', joined)


class WhoMayEdit(Fixture):
    """The heart of it: a stage decides who holds the pen."""

    def test_employee_edits_only_while_it_is_theirs(self):
        self.assertTrue(self.plan.may_edit('employee'))
        self.plan.status = 'submitted'
        self.assertFalse(self.plan.may_edit('employee'))

    def test_manager_edits_only_once_it_is_submitted(self):
        self.assertFalse(self.plan.may_edit('manager'))
        self.plan.status = 'submitted'
        self.assertTrue(self.plan.may_edit('manager'))

    def test_manager_cannot_still_edit_after_sending_to_hod(self):
        self.plan.status = 'with_hod'
        self.assertFalse(self.plan.may_edit('manager'))
        self.assertTrue(self.plan.may_edit('hod'))

    def test_an_accepted_sheet_is_closed_to_the_people_who_agreed_it(self):
        """Nobody who took part may quietly reopen what they signed off.

        The admin is the deliberate exception - see AdminPowers, where the
        override is only allowed because every admin edit is versioned.
        """
        self.plan.status = 'accepted'
        for role in ('employee', 'manager', 'hod'):
            self.assertFalse(self.plan.may_edit(role), role)
        self.assertTrue(self.plan.may_edit('admin'))


class Workflow(Fixture):
    def test_the_whole_journey(self):
        self.fill()
        advance(self.plan, 'submit', role='employee', name='Rahul')
        self.assertEqual(self.plan.status, 'submitted')

        advance(self.plan, 'to_hod', role='manager', name='Arun')
        self.assertEqual(self.plan.status, 'with_hod')

        advance(self.plan, 'to_employee', role='hod', name='Narendra')
        self.assertEqual(self.plan.status, 'awaiting_employee')

        advance(self.plan, 'accept', role='employee', name='Rahul')
        self.assertEqual(self.plan.status, 'accepted')
        self.assertIsNotNone(self.plan.accepted_at)

    def test_a_role_cannot_take_another_role_s_step(self):
        self.fill()
        advance(self.plan, 'submit', role='employee')
        with self.assertRaises(WorkflowError) as e:
            advance(self.plan, 'to_hod', role='employee')
        self.assertEqual(e.exception.status, 403)

    def test_a_step_out_of_order_is_refused(self):
        self.fill()
        with self.assertRaises(WorkflowError) as e:
            advance(self.plan, 'to_hod', role='manager')
        self.assertEqual(e.exception.status, 409)

    def test_an_incomplete_sheet_cannot_be_submitted(self):
        self.fill(sheet(('A', 10), ('B', 10), ('C', 10), ('D', 10)))
        with self.assertRaises(WorkflowError) as e:
            advance(self.plan, 'submit', role='employee')
        self.assertTrue(e.exception.problems)

    def test_a_return_is_allowed_even_when_incomplete(self):
        """Incompleteness is usually the reason for sending it back."""
        self.fill()
        advance(self.plan, 'submit', role='employee')
        save_kras(self.plan, sheet(('A', 5), ('B', 5), ('C', 5), ('D', 5)))
        self.plan.refresh_from_db()
        advance(self.plan, 'manager_return', role='manager', note='Targets look low')
        self.assertEqual(self.plan.status, 'returned')

    def test_a_returned_sheet_can_be_resubmitted(self):
        self.fill()
        advance(self.plan, 'submit', role='employee')
        advance(self.plan, 'manager_return', role='manager')
        advance(self.plan, 'submit', role='employee')
        self.assertEqual(self.plan.status, 'submitted')

    def test_a_locked_cycle_stops_the_workflow(self):
        self.fill()
        self.cycle.status = 'locked'
        self.cycle.save()
        with self.assertRaises(WorkflowError) as e:
            advance(self.plan, 'submit', role='employee')
        self.assertEqual(e.exception.status, 403)


class History(Fixture):
    """What the employee can prove months later."""

    def test_every_handoff_is_a_version(self):
        self.fill()
        advance(self.plan, 'submit', role='employee', name='Rahul')
        advance(self.plan, 'to_hod', role='manager', name='Arun')
        advance(self.plan, 'to_employee', role='hod', name='Narendra')
        self.assertEqual([v.actor_role for v in self.plan.versions.all()],
                         ['employee', 'manager', 'hod'])
        self.assertEqual([v.version_no for v in self.plan.versions.all()], [1, 2, 3])

    def test_the_original_survives_a_manager_rewriting_it(self):
        self.fill()
        advance(self.plan, 'submit', role='employee', name='Rahul')

        save_kras(self.plan, sheet(('Primary sales', 60), ('Retail coverage', 20),
                                   ('Process audit', 10), ('Team training', 10)))
        self.plan.refresh_from_db()
        advance(self.plan, 'to_hod', role='manager', name='Arun')

        v1 = self.plan.versions.get(version_no=1)
        self.assertEqual(v1.kras[0]['kpis'][0]['weightage'], 40,
                         'the employee\'s original figure must not move')
        self.assertEqual(self.plan.versions.get(version_no=2).kras[0]['kpis'][0]['weightage'], 60)

    def test_a_deleted_kra_is_still_readable_in_its_old_version(self):
        self.fill()
        advance(self.plan, 'submit', role='employee')
        save_kras(self.plan, sheet(('Primary sales', 100))[:1])
        self.plan.refresh_from_db()
        advance(self.plan, 'manager_return', role='manager')

        self.assertEqual(len(self.plan.versions.get(version_no=1).kras), 4)
        self.assertEqual(self.plan.kras.count(), 1)

    def test_the_change_list_names_what_moved(self):
        self.fill()
        advance(self.plan, 'submit', role='employee')
        save_kras(self.plan, sheet(('Primary sales', 60), ('Retail coverage', 20),
                                   ('Process audit', 10), ('Team training', 10)))
        self.plan.refresh_from_db()
        advance(self.plan, 'to_hod', role='manager')

        changes = self.plan.versions.get(version_no=2).changes
        moved = {c['kpi']: (c['from'], c['to']) for c in changes
                 if c['type'] == 'kpi_changed' and c['field'] == 'weightage'}
        self.assertEqual(moved.get('Primary sales'), (40, 60))
        self.assertEqual(moved.get('Retail coverage'), (30, 20))
        self.assertNotIn('Team training', moved, 'an unchanged weight is not a change')


class Diff(TestCase):
    """diff_snapshots is what the employee reads, so it has to be honest."""

    def test_added_and_removed_kras(self):
        before = [{'category': 'Financial', 'title': 'A', 'kpis': []}]
        after = [{'category': 'Financial', 'title': 'B', 'kpis': []}]
        kinds = {c['type'] for c in diff_snapshots(before, after)}
        self.assertEqual(kinds, {'kra_added', 'kra_removed'})

    def test_added_and_removed_kpis(self):
        before = [{'category': 'F', 'title': 'A', 'kpis': [{'metric': 'x', 'weightage': 10}]}]
        after = [{'category': 'F', 'title': 'A', 'kpis': [{'metric': 'y', 'weightage': 10}]}]
        kinds = {c['type'] for c in diff_snapshots(before, after)}
        self.assertEqual(kinds, {'kpi_added', 'kpi_removed'})

    def test_no_change_reads_as_no_change(self):
        rows = [{'category': 'F', 'title': 'A', 'kpis': [{'metric': 'x', 'weightage': 10}]}]
        self.assertEqual(diff_snapshots(rows, rows), [])


class Api(Fixture):
    """The HTTP surface, since the permission checks live in the views too."""

    def test_save_and_submit_over_http(self):
        r = self.post(f'/plans/E1/{self.cycle.id}/', {'role': 'employee', 'kras': FULL})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['total_weightage'], 100)
        self.assertEqual(r.json()['problems'], [])

        plan_id = r.json()['id']
        r = self.post(f'/plans/{plan_id}/action/',
                      {'role': 'employee', 'action': 'submit', 'actor_name': 'Rahul'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['status'], 'submitted')

    def test_the_wrong_role_cannot_save(self):
        """A manager must not be able to edit a sheet still with the employee."""
        r = self.post(f'/plans/E1/{self.cycle.id}/', {'role': 'manager', 'kras': FULL})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(GoalPlan.objects.get(id=self.plan.id).kpi_count, 0)

    def test_submitting_an_incomplete_sheet_explains_why(self):
        self.post(f'/plans/E1/{self.cycle.id}/',
                  {'role': 'employee', 'kras': sheet(('A', 10), ('B', 10), ('C', 10), ('D', 10))})
        r = self.post(f'/plans/{self.plan.id}/action/', {'role': 'employee', 'action': 'submit'})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(r.json()['problems'])

    def test_an_edit_sent_with_the_action_is_not_lost(self):
        """Sending on must save what is on screen, or a manager's last change
        vanishes the moment they click the button."""
        self.fill()
        advance(self.plan, 'submit', role='employee')
        edited = sheet(('Primary sales', 55), ('Retail coverage', 25),
                       ('Process audit', 10), ('Team training', 10))
        r = self.post(f'/plans/{self.plan.id}/action/',
                      {'role': 'manager', 'action': 'to_hod', 'kras': edited,
                       'actor_name': 'Arun'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(PlanVersion.objects.get(plan=self.plan, version_no=2)
                         .kras[0]['kpis'][0]['weightage'], 55)

    def test_manager_team_lists_people_without_a_sheet(self):
        r = self.client.get(f'{BASE}/manager/M1/team/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual([row['employee_id'] for row in r.json()], ['E1'])

    def test_meta_gives_the_form_its_vocabulary(self):
        r = self.client.get(f'{BASE}/meta/')
        self.assertEqual(len(r.json()['categories']), 4)
        self.assertIn('Monthly', r.json()['frequencies'])


class Regressions(Fixture):
    """One test per bug found reviewing the finished product.

    Each of these passed review by eye and failed against a running server,
    which is the reason they are written down rather than just fixed.
    """

    def test_reading_someone_elses_sheet_does_not_create_one(self):
        """A manager clicking a name used to mark that person as started, so
        the admin's "not started" count fell every time anyone looked."""
        GoalPlan.objects.all().delete()
        before = GoalPlan.objects.count()
        r = self.client.get(f'{BASE}/plans/E1/{self.cycle.id}/?role=manager')
        self.assertEqual(r.status_code, 404)
        self.assertTrue(r.json().get('not_started'))
        self.assertEqual(GoalPlan.objects.count(), before)

    def test_the_employees_own_visit_still_creates_one(self):
        GoalPlan.objects.all().delete()
        r = self.client.get(f'{BASE}/plans/E1/{self.cycle.id}/?role=employee')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(GoalPlan.objects.count(), 1)

    def test_a_refused_action_writes_nothing(self):
        """The edit used to land even when the hand-off was refused: a manager
        could edit a locked cycle, be told it was locked, and have it saved."""
        self.fill()
        advance(self.plan, 'submit', role='employee')
        self.cycle.status = 'locked'
        self.cycle.save()

        r = self.post(f'/plans/{self.plan.id}/action/', {
            'role': 'manager', 'action': 'to_hod',
            'kras': sheet(('Rewritten', 100)),
        })
        self.assertEqual(r.status_code, 403)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.kras.count(), 4, 'the refused edit must be rolled back')
        self.assertEqual(self.plan.kras.first().kpis.first().metric, 'Primary sales')

    def test_a_new_sheet_starts_empty(self):
        """It used to be seeded with four blank KRAs, which the form marks
        invalid — so the sheet opened as four red error boxes."""
        GoalPlan.objects.all().delete()
        plan = get_or_create_plan(self.emp, self.cycle)
        self.assertEqual(plan.kras.count(), 0)

    def test_requesting_changes_sends_it_to_the_manager(self):
        """The button says "sends it back to your manager", so it must."""
        self.fill()
        advance(self.plan, 'submit', role='employee')
        advance(self.plan, 'to_hod', role='manager')
        advance(self.plan, 'to_employee', role='hod')

        advance(self.plan, 'employee_return', role='employee', note='The target is unrealistic')
        self.assertEqual(self.plan.status, 'submitted')
        self.assertTrue(self.plan.may_edit('manager'))
        self.assertFalse(self.plan.may_edit('employee'))


class AdminPowers(Fixture):
    """The admin seat overrides the workflow — and is recorded doing it.

    The override is only safe because it is visible. These tests exist to keep
    it that way: if an admin change could ever land without a version, the
    product's promise that history is complete would quietly become false.
    """

    def test_admin_edits_a_sheet_that_is_with_someone_else(self):
        self.fill()
        advance(self.plan, 'submit', role='employee')          # now with the manager
        r = self.post(f'/plans/E1/{self.cycle.id}/', {
            'role': 'admin', 'actor_name': 'Anshul',
            'kras': sheet(('Corrected metric', 100)),
        })
        self.assertEqual(r.status_code, 200, r.content)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.kras.first().kpis.first().metric, 'Corrected metric')

    def test_admin_edits_an_agreed_sheet(self):
        """The hardest case: changing something both sides already signed off."""
        self.fill()
        for action, role in [('submit', 'employee'), ('to_hod', 'manager'),
                             ('to_employee', 'hod'), ('accept', 'employee')]:
            advance(self.plan, action, role=role)
        self.assertEqual(self.plan.status, 'accepted')

        r = self.post(f'/plans/E1/{self.cycle.id}/', {
            'role': 'admin', 'actor_name': 'Anshul', 'kras': sheet(('Late correction', 100)),
        })
        self.assertEqual(r.status_code, 200, r.content)

    def test_an_admin_edit_is_always_recorded(self):
        """Without this, an admin is the one actor who can change goals invisibly."""
        self.fill()
        advance(self.plan, 'submit', role='employee')
        before = self.plan.versions.count()

        self.post(f'/plans/E1/{self.cycle.id}/', {
            'role': 'admin', 'actor_name': 'Anshul',
            'kras': sheet(('Corrected metric', 100)),
        })
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.versions.count(), before + 1)

        v = self.plan.versions.order_by('-version_no').first()
        self.assertEqual(v.actor_role, 'admin')
        self.assertEqual(v.actor_name, 'Anshul')
        self.assertTrue(v.changes, 'the version must say what the admin changed')

    def test_admin_edits_even_when_the_cycle_is_locked(self):
        self.fill()
        self.cycle.status = 'locked'
        self.cycle.save()
        r = self.post(f'/plans/E1/{self.cycle.id}/', {
            'role': 'admin', 'actor_name': 'Anshul', 'kras': sheet(('After lock', 100)),
        })
        self.assertEqual(r.status_code, 200, r.content)

    def test_a_normal_user_still_cannot(self):
        """The override belongs to the admin alone."""
        self.fill()
        advance(self.plan, 'submit', role='employee')
        for role in ('employee', 'hod'):
            r = self.post(f'/plans/E1/{self.cycle.id}/', {'role': role, 'kras': FULL})
            self.assertEqual(r.status_code, 403, role)

    def test_admin_moves_a_sheet_to_any_stage(self):
        self.fill()
        r = self.post(f'/plans/{self.plan.id}/status/',
                      {'status': 'with_hod', 'actor_name': 'Anshul', 'note': 'Manager is away.'})
        self.assertEqual(r.status_code, 200, r.content)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, 'with_hod')

    def test_an_override_says_where_it_came_from(self):
        self.fill()
        self.post(f'/plans/{self.plan.id}/status/',
                  {'status': 'accepted', 'actor_name': 'Anshul', 'note': 'Agreed offline.'})
        v = self.plan.versions.order_by('-version_no').first()
        self.assertEqual(v.action, 'admin_moved')
        self.assertIn('Draft with Employee', v.note)
        self.assertIn('Agreed offline', v.note)

    def test_a_nonsense_stage_is_refused(self):
        r = self.post(f'/plans/{self.plan.id}/status/', {'status': 'banana'})
        self.assertEqual(r.status_code, 400)

    def test_admin_adds_a_person_by_hand(self):
        r = self.post('/employees/create/', {
            'employee_id': 'E9', 'name': 'Late Joiner', 'email': 'late@apisindia.com',
            'reporting_manager_id': 'M1', 'user_type': 'employee',
        })
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(EmployeeProfile.objects.filter(employee_id='E9').exists())

    def test_a_duplicate_id_is_refused(self):
        r = self.post('/employees/create/', {'employee_id': 'E1', 'name': 'Clash'})
        self.assertEqual(r.status_code, 400)

    def test_admin_edits_employee_details(self):
        r = self.client.patch(f'{BASE}/employees/E1/',
                              {'designation': 'Senior Executive', 'hod_id': 'H1'},
                              content_type='application/json')
        self.assertEqual(r.status_code, 200, r.content)
        self.emp.refresh_from_db()
        self.assertEqual(self.emp.designation, 'Senior Executive')

    def test_the_activity_feed_shows_every_step(self):
        self.fill()
        advance(self.plan, 'submit', role='employee', name='Rahul')
        advance(self.plan, 'to_hod', role='manager', name='Arun')

        r = self.client.get(f'{BASE}/activity/')
        self.assertEqual(r.status_code, 200)
        feed = r.json()
        self.assertEqual([e['action'] for e in feed], ['to_hod', 'submit'],
                         'newest first')
        self.assertEqual(feed[0]['employee_name'], 'Rahul')
        self.assertEqual(feed[0]['actor_name'], 'Arun')


class Template(TestCase):
    """The blank sheet an admin downloads, fills in and uploads back.

    Generated from the importer's own column list, so the two cannot drift; the
    tests below are what keep that true.
    """

    def test_it_downloads_as_a_spreadsheet(self):
        r = self.client.get(f'{BASE}/employees/template/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('spreadsheetml', r['Content-Type'])
        self.assertIn('attachment', r['Content-Disposition'])

    def test_its_headings_are_what_the_importer_reads(self):
        """A template whose columns have drifted fails at upload with a
        complaint about a column the person is certain they included."""
        import io as _io
        import pandas as pd
        from .views import COLUMN_MAP

        r = self.client.get(f'{BASE}/employees/template/')
        df = pd.read_excel(_io.BytesIO(r.content))
        for heading in df.columns:
            self.assertIn(heading, COLUMN_MAP,
                          f'"{heading}" is in the template but the importer ignores it')
        for required in ('employee_id', 'name'):
            self.assertIn(required, df.columns)

    def test_it_contains_no_rows_to_delete(self):
        """It used to ship a guidance row and three example people with a note
        saying to delete them. Forget, and four imaginary employees import."""
        import io as _io
        import pandas as pd
        r = self.client.get(f'{BASE}/employees/template/')
        self.assertEqual(len(pd.read_excel(_io.BytesIO(r.content))), 0)

    def test_a_filled_in_copy_imports(self):
        """The round trip, end to end - which is the only thing that matters."""
        import io as _io
        import pandas as pd
        from django.core.files.uploadedfile import SimpleUploadedFile

        blank = pd.read_excel(_io.BytesIO(
            self.client.get(f'{BASE}/employees/template/').content))

        filled = pd.DataFrame([{
            'employee_id': 'T1', 'name': 'Filled In', 'email': 't1@apisindia.com',
            'reporting_manager_id': 'T2', 'user_type': 'employee',
        }, {
            'employee_id': 'T2', 'name': 'Their Manager', 'user_type': 'manager',
        }], columns=blank.columns)

        buf = _io.BytesIO()
        filled.to_excel(buf, index=False)

        r = self.client.post(f'{BASE}/employees/import/', {
            'file': SimpleUploadedFile('filled.xlsx', buf.getvalue()),
        })
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['created'], 2)
        self.assertEqual(r.json()['error_count'], 0)

        # and the reporting line actually linked, which is the usual failure
        team = self.client.get(f'{BASE}/manager/T2/team/').json()
        self.assertEqual([p['employee_id'] for p in team], ['T1'])
