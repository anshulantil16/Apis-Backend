"""Goal Setting Hub: the workflow, and the history that makes it trustworthy.

The product's promise is that a manager and an HOD can rewrite someone's goals
AND that the employee can still see exactly what they originally proposed. Both
halves are tested here: who may edit at each stage, and that no version is ever
lost or altered.
"""
from django.test import TestCase

from .models import (EmployeeProfile, GoalCycle, GoalPlan, PlanEvent,
                     PlanVersion, diff_snapshots)
from .services import WorkflowError, advance, get_or_create_plan, readiness, save_kras
from .session import issue_session

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


class AsAdmin:
    """Signs the test client in as an administrator.

    These screens belong to the admin, and until recently they did not have to
    prove it: every admin endpoint was open to anyone who knew the URL,
    `/reset/` among them. Needing this mixin is the guard doing its job.
    """

    def setUp(self):
        super().setUp()
        admin, _ = EmployeeProfile.objects.get_or_create(
            employee_id='GS-ADMIN',
            defaults={'name': 'Goal Setting Admin', 'user_type': 'admin'})
        self.client.defaults['HTTP_X_GOALSETTING_SESSION'] = issue_session(admin)


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


class AdminPowers(AsAdmin, Fixture):
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
        self.client.defaults.pop('HTTP_X_GOALSETTING_SESSION', None)   # not an admin now
        for who in ('E1', 'H1'):
            r = self.post(f'/plans/E1/{self.cycle.id}/',
                          {'actor_employee_id': who, 'role': 'admin', 'kras': FULL})
            self.assertEqual(r.status_code, 403, who)

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


class Template(AsAdmin, TestCase):
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

    def test_it_contains_nothing_but_sample_rows(self):
        """Filled-in examples are the point of a template - but every row in it
        must be one the importer will skip, or a forgotten example becomes a
        real employee. See SampleRows for the skipping itself."""
        import io as _io
        import pandas as pd
        r = self.client.get(f'{BASE}/employees/template/')
        df = pd.read_excel(_io.BytesIO(r.content))
        self.assertTrue(len(df) > 0)
        for value in df['employee_id']:
            self.assertTrue(str(value).startswith('SAMPLE-'),
                            f'"{value}" would be imported as a real person')

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


class SampleRows(AsAdmin, TestCase):
    """The template ships filled-in rows, and they must be harmless.

    An example you have to remember to delete is a trap: forget once and three
    imaginary employees are on the list. These are skipped on import instead.
    """

    def _upload(self, frame):
        import io as _io
        from django.core.files.uploadedfile import SimpleUploadedFile
        buf = _io.BytesIO()
        frame.to_excel(buf, index=False)
        return self.client.post(f'{BASE}/employees/import/',
                                {'file': SimpleUploadedFile('x.xlsx', buf.getvalue())})

    def test_the_template_now_shows_filled_in_rows(self):
        import io as _io
        import pandas as pd
        df = pd.read_excel(_io.BytesIO(
            self.client.get(f'{BASE}/employees/template/').content))
        self.assertEqual(len(df), 3, 'the examples are the point of a template')
        self.assertTrue(all(str(v).startswith('SAMPLE-') for v in df['employee_id']))

    def test_uploading_the_untouched_template_imports_nobody(self):
        import io as _io
        import pandas as pd
        df = pd.read_excel(_io.BytesIO(
            self.client.get(f'{BASE}/employees/template/').content))
        r = self._upload(df)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['created'], 0)
        self.assertEqual(r.json()['skipped_samples'], 3)
        self.assertEqual(EmployeeProfile.objects.exclude(employee_id='GS-ADMIN').count(), 0)

    def test_real_rows_alongside_samples_still_import(self):
        """Someone typing under the examples rather than over them."""
        import io as _io
        import pandas as pd
        df = pd.read_excel(_io.BytesIO(
            self.client.get(f'{BASE}/employees/template/').content))
        mine = pd.DataFrame([{'employee_id': 'R1', 'name': 'Real Person'}],
                            columns=df.columns)
        r = self._upload(pd.concat([df, mine], ignore_index=True))
        self.assertEqual(r.json()['created'], 1)
        self.assertEqual(r.json()['skipped_samples'], 3)
        self.assertEqual([e.employee_id for e in
                          EmployeeProfile.objects.exclude(employee_id='GS-ADMIN')], ['R1'])


class Reset(AsAdmin, Fixture):
    """Clearing the data. Guarded, because it deletes the version history —
    the one thing this product promises is permanent."""

    def setUp(self):
        super().setUp()
        self.fill()
        advance(self.plan, 'submit', role='employee')

    def test_it_refuses_without_the_confirmation(self):
        r = self.post('/reset/', {'scope': 'all'})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(GoalPlan.objects.exists())

    def test_it_refuses_an_unknown_scope(self):
        r = self.post('/reset/', {'scope': 'everything', 'confirm': 'RESET_CONFIRMED'})
        self.assertEqual(r.status_code, 400)
        self.assertTrue(GoalPlan.objects.exists())

    def test_clearing_sheets_keeps_the_people(self):
        """The common case after a trial run — an all-or-nothing wipe would
        make them re-upload the master just to carry on."""
        r = self.post('/reset/', {'scope': 'plans', 'confirm': 'RESET_CONFIRMED'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(GoalPlan.objects.count(), 0)
        self.assertEqual(PlanVersion.objects.count(), 0)
        self.assertEqual(EmployeeProfile.objects.exclude(employee_id='GS-ADMIN').count(), 3)
        self.assertEqual(GoalCycle.objects.count(), 1)

    def test_clearing_people_keeps_the_cycle(self):
        self.post('/reset/', {'scope': 'people', 'confirm': 'RESET_CONFIRMED'})
        self.assertEqual(EmployeeProfile.objects.exclude(employee_id='GS-ADMIN').count(), 0)
        self.assertEqual(GoalCycle.objects.count(), 1)

    def test_clearing_everything(self):
        r = self.post('/reset/', {'scope': 'all', 'confirm': 'RESET_CONFIRMED'})
        self.assertEqual(r.status_code, 200, r.content)
        for model in (GoalPlan, PlanVersion, PlanEvent, GoalCycle):
            self.assertEqual(model.objects.count(), 0, model.__name__)
        # Everyone except the bootstrap admin, who is kept on purpose.
        self.assertEqual(EmployeeProfile.objects.exclude(employee_id='GS-ADMIN').count(), 0)

    def test_the_bootstrap_admin_survives(self):
        """Otherwise a reset locks the administrator out of undoing it."""
        EmployeeProfile.objects.get_or_create(employee_id='GS-ADMIN',
                                              defaults={'name': 'Admin', 'user_type': 'admin'})
        self.post('/reset/', {'scope': 'all', 'confirm': 'RESET_CONFIRMED'})
        self.assertTrue(EmployeeProfile.objects.filter(employee_id='GS-ADMIN').exists())

    def test_it_says_what_it_would_remove_before_you_click(self):
        r = self.client.get(f'{BASE}/reset/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['counts']['plans'], 1)
        self.assertEqual(r.json()['counts']['people'], 3)


class Export(AsAdmin, Fixture):
    """The workbook of agreed goals.

    Three sheets because an admin asks three questions of the same data: what
    was agreed, how each sheet totals up, and who still owes goals.
    """

    def setUp(self):
        super().setUp()
        # E1 agrees; E2 gets stuck with the HOD; a third person never starts.
        self.fill()
        for action, role in [('submit', 'employee'), ('to_hod', 'manager'),
                             ('to_employee', 'hod'), ('accept', 'employee')]:
            advance(self.plan, action, role=role, name='Somebody')

        self.other = EmployeeProfile.objects.create(
            employee_id='E2', name='Priya Nair', reporting_manager_id='M1', hod_id='H1')
        stuck = get_or_create_plan(self.other, self.cycle)
        save_kras(stuck, FULL)
        stuck.refresh_from_db()
        advance(stuck, 'submit', role='employee')
        advance(stuck, 'to_hod', role='manager')

        EmployeeProfile.objects.create(employee_id='E3', name='Never Started',
                                       reporting_manager_id='M1', hod_id='H1')

    def _book(self, query=''):
        import io as _io
        import pandas as pd
        r = self.client.get(f'{BASE}/export/{query}')
        self.assertEqual(r.status_code, 200)
        return pd.ExcelFile(_io.BytesIO(r.content))

    def test_it_downloads_as_a_spreadsheet(self):
        r = self.client.get(f'{BASE}/export/')
        self.assertIn('spreadsheetml', r['Content-Type'])
        self.assertIn('attachment', r['Content-Disposition'])

    def test_three_sheets(self):
        self.assertEqual(self._book().sheet_names,
                         ['Goals', 'Summary', 'Not agreed yet'])

    def test_it_exports_only_the_agreed_goals_by_default(self):
        """"The final one" is the whole point - a mid-review sheet is not final."""
        import pandas as pd
        book = self._book()
        goals = pd.read_excel(book, 'Goals')
        self.assertEqual(set(goals['Employee ID']), {'E1'})
        self.assertEqual(len(goals), 4, 'one row per KPI')

    def test_every_sheet_can_be_asked_for(self):
        import pandas as pd
        goals = pd.read_excel(self._book('?status=all'), 'Goals')
        self.assertEqual(set(goals['Employee ID']), {'E1', 'E2'})

    def test_a_goal_row_carries_the_person_it_belongs_to(self):
        """Flat, so the file can be filtered and pivoted."""
        import pandas as pd
        row = pd.read_excel(self._book(), 'Goals').iloc[0]
        self.assertEqual(row['Name'], 'Rahul')
        self.assertEqual(row['Weight %'], 40)
        self.assertTrue(row['KRA'])
        self.assertTrue(row['Plan / Target'])

    def test_the_summary_is_one_line_per_person(self):
        import pandas as pd
        s = pd.read_excel(self._book(), 'Summary')
        self.assertEqual(len(s), 1)
        self.assertEqual(s.iloc[0]['Total weight %'], 100)
        self.assertEqual(s.iloc[0]['KPIs'], 4)

    def test_pending_distinguishes_stuck_from_never_started(self):
        """This was wrong: the sheet was built from the filtered list, so
        anyone mid-review read as "Not started" - the opposite of the truth,
        in the one column an admin uses to chase people."""
        import pandas as pd
        p = pd.read_excel(self._book(), 'Not agreed yet').set_index('Employee ID')
        self.assertEqual(p.loc['E2', 'Where it is stuck'], 'With HOD')
        self.assertEqual(p.loc['E2', 'Waiting on'], 'The HOD, to review')
        self.assertEqual(p.loc['E3', 'Where it is stuck'], 'Not started')
        self.assertNotIn('E1', p.index, 'someone who has agreed is not pending')


class EveryoneSetsTheirOwnGoals(Fixture):
    """A manager and an HOD have goals of their own.

    They could not fill them in. The role came from EmployeeProfile.user_type,
    which says what you are in the company, not what you are to the sheet in
    front of you -- so a manager's own draft was "with the employee" while they
    were "a manager", and their own sheet was read-only to them. Opening it
    returned "has not started a goal sheet yet" about themselves.
    """

    def own(self, who):
        return (f'{BASE}/plans/{who.employee_id}/{self.cycle.id}/'
                f'?actor_employee_id={who.employee_id}')

    def test_a_manager_opens_their_own_sheet(self):
        r = self.client.get(self.own(self.mgr))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['your_role'], 'employee')

    def test_an_hod_opens_their_own_sheet(self):
        r = self.client.get(self.own(self.hod))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['your_role'], 'employee')

    def test_a_manager_fills_and_submits_their_own(self):
        r = self.post(f'/plans/M1/{self.cycle.id}/',
                      {'actor_employee_id': 'M1', 'kras': FULL})
        self.assertEqual(r.status_code, 200, r.content)

        plan = GoalPlan.objects.get(employee=self.mgr, cycle=self.cycle)
        r = self.post(f'/plans/{plan.id}/action/',
                      {'actor_employee_id': 'M1', 'actor_name': 'Arun', 'action': 'submit'})
        self.assertEqual(r.status_code, 200, r.content)
        plan.refresh_from_db()
        # M1 has an HOD but no manager of their own, so it goes straight there.
        self.assertEqual(plan.status, 'with_hod')

    def test_a_managers_own_sheet_completes_its_round_trip(self):
        self.post(f'/plans/M1/{self.cycle.id}/', {'actor_employee_id': 'M1', 'kras': FULL})
        plan = GoalPlan.objects.get(employee=self.mgr, cycle=self.cycle)
        self.post(f'/plans/{plan.id}/action/', {'actor_employee_id': 'M1', 'action': 'submit'})

        r = self.post(f'/plans/{plan.id}/action/',
                      {'actor_employee_id': 'H1', 'actor_name': 'Narendra',
                       'action': 'to_employee'})
        self.assertEqual(r.status_code, 200, r.content)

        r = self.post(f'/plans/{plan.id}/action/',
                      {'actor_employee_id': 'M1', 'action': 'accept'})
        self.assertEqual(r.status_code, 200, r.content)
        plan.refresh_from_db()
        self.assertEqual(plan.status, 'accepted')

    def test_being_a_manager_elsewhere_does_not_follow_you_to_your_own_sheet(self):
        """M1 reviews E1, but on M1's own sheet M1 is simply the employee."""
        self.fill()
        advance(self.plan, 'submit', role='employee')
        r = self.client.get(f'{BASE}/plans/E1/{self.cycle.id}/?actor_employee_id=M1')
        self.assertEqual(r.json()['your_role'], 'manager')
        r = self.client.get(self.own(self.mgr))
        self.assertEqual(r.json()['your_role'], 'employee')


class RoleComesFromTheOrgChart(Fixture):
    """What you are is decided by the sheet, not by what the caller claims."""

    def test_a_stranger_cannot_open_someone_elses_sheet(self):
        EmployeeProfile.objects.create(employee_id='X9', name='Nosy')
        r = self.client.get(f'{BASE}/plans/E1/{self.cycle.id}/?actor_employee_id=X9')
        self.assertEqual(r.status_code, 403)

    def test_a_stranger_cannot_edit_someone_elses_sheet(self):
        EmployeeProfile.objects.create(employee_id='X9', name='Nosy')
        r = self.post(f'/plans/E1/{self.cycle.id}/',
                      {'actor_employee_id': 'X9', 'role': 'employee', 'kras': FULL})
        self.assertEqual(r.status_code, 403)

    def test_claiming_to_be_the_employee_does_not_make_you_one(self):
        """The old API believed `role` in the body, so a manager could post
        role=employee on a subordinate's draft and edit it as them."""
        r = self.post(f'/plans/E1/{self.cycle.id}/',
                      {'actor_employee_id': 'M1', 'role': 'employee', 'kras': FULL})
        self.assertEqual(r.status_code, 403)

    def test_a_reviewer_looking_does_not_start_a_sheet_for_someone(self):
        self.assertFalse(GoalPlan.objects.filter(employee=self.mgr).exists())
        r = self.client.get(f'{BASE}/plans/M1/{self.cycle.id}/?actor_employee_id=H1')
        self.assertEqual(r.status_code, 404)
        self.assertTrue(r.json()['not_started'])
        self.assertFalse(GoalPlan.objects.filter(employee=self.mgr).exists())

    def test_the_actor_recorded_is_the_reviewer_not_the_employee(self):
        """The screen used to send the sheet owner's id as the actor, so every
        review a manager made was filed under the employee's name."""
        self.fill()
        advance(self.plan, 'submit', role='employee')
        self.post(f'/plans/{self.plan.id}/action/',
                  {'actor_employee_id': 'M1', 'actor_name': 'Arun', 'action': 'to_hod'})
        v = PlanVersion.objects.filter(plan=self.plan).order_by('-version_no').first()
        self.assertEqual(v.actor_role, 'manager')
        self.assertEqual(v.actor_employee_id, 'M1')

    def test_one_person_who_is_both_manager_and_hod_can_act_at_both_stages(self):
        """Small departments do this. Holding two roles must not lock you out
        of one of them."""
        solo = EmployeeProfile.objects.create(employee_id='S1', name='Solo',
                                              reporting_manager_id='H1', hod_id='H1')
        plan = get_or_create_plan(solo, self.cycle)
        save_kras(plan, FULL)
        plan.refresh_from_db()
        advance(plan, 'submit', role='employee')

        r = self.post(f'/plans/{plan.id}/action/',
                      {'actor_employee_id': 'H1', 'action': 'to_hod'})
        self.assertEqual(r.status_code, 200, r.content)
        r = self.post(f'/plans/{plan.id}/action/',
                      {'actor_employee_id': 'H1', 'action': 'to_employee'})
        self.assertEqual(r.status_code, 200, r.content)
        plan.refresh_from_db()
        self.assertEqual(plan.status, 'awaiting_employee')


class NobodyGetsStuck(Fixture):
    """Rolling this out to the whole company means people whose reporting line
    is incomplete. A sheet must never reach a stage nobody can act at."""

    def submit_for(self, emp):
        plan = get_or_create_plan(emp, self.cycle)
        save_kras(plan, FULL)
        plan.refresh_from_db()
        advance(plan, 'submit', role='employee')
        plan.refresh_from_db()
        return plan

    def test_no_manager_on_file_goes_straight_to_the_hod(self):
        orphan = EmployeeProfile.objects.create(employee_id='O1', name='Orphan', hod_id='H1')
        self.assertEqual(self.submit_for(orphan).status, 'with_hod')

    def test_a_manager_id_naming_nobody_is_not_a_manager(self):
        ghost = EmployeeProfile.objects.create(employee_id='G1', name='Ghost',
                                               reporting_manager_id='WHO', hod_id='H1')
        self.assertEqual(self.submit_for(ghost).status, 'with_hod')

    def test_an_inactive_manager_is_not_a_manager(self):
        EmployeeProfile.objects.create(employee_id='L1', name='Left', user_type='manager',
                                       is_active=False)
        leftover = EmployeeProfile.objects.create(employee_id='R1', name='Reports to leaver',
                                                  reporting_manager_id='L1', hod_id='H1')
        self.assertEqual(self.submit_for(leftover).status, 'with_hod')

    def test_nobody_above_you_at_all_comes_back_for_your_acceptance(self):
        top = EmployeeProfile.objects.create(employee_id='T1', name='Top', user_type='hod')
        self.assertEqual(self.submit_for(top).status, 'awaiting_employee')

    def test_and_the_record_says_why_it_skipped_a_stage(self):
        top = EmployeeProfile.objects.create(employee_id='T1', name='Top', user_type='hod')
        plan = self.submit_for(top)
        note = PlanVersion.objects.filter(plan=plan).order_by('-version_no').first().note
        self.assertIn('no ', note.lower())

    def test_pointing_at_yourself_is_not_a_review(self):
        selfy = EmployeeProfile.objects.create(employee_id='SF', name='Self',
                                               reporting_manager_id='SF', hod_id='SF')
        self.assertEqual(self.submit_for(selfy).status, 'awaiting_employee')


class AdminDoorsAreShut(Fixture):
    """Everything that can destroy or rewrite other people's data.

    All of it was open to anyone who could reach the URL. `/reset/` deletes
    every goal sheet and every version of it -- the one thing this product
    promises is permanent -- and asked only for a confirmation phrase that is
    written in the frontend source. Going live for the whole company means
    every employee has that URL.
    """

    def setUp(self):
        super().setUp()
        self.fill()
        # Signed in, and an ordinary employee: being a real user is not the
        # same as being an administrator.
        self.client.defaults['HTTP_X_GOALSETTING_SESSION'] = issue_session(self.emp)

    def test_an_employee_cannot_wipe_the_goal_sheets(self):
        r = self.post('/reset/', {'scope': 'all', 'confirm': 'RESET_CONFIRMED'})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(GoalPlan.objects.exists())

    def test_a_stranger_with_no_session_cannot_either(self):
        self.client.defaults.pop('HTTP_X_GOALSETTING_SESSION', None)
        r = self.post('/reset/', {'scope': 'all', 'confirm': 'RESET_CONFIRMED'})
        self.assertEqual(r.status_code, 403)
        self.assertTrue(GoalPlan.objects.exists())

    def test_an_employee_cannot_force_their_own_goals_to_accepted(self):
        r = self.post(f'/plans/{self.plan.id}/status/', {'status': 'accepted'})
        self.assertEqual(r.status_code, 403)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.status, 'draft')

    def test_an_employee_cannot_reopen_an_agreed_sheet(self):
        r = self.post(f'/plans/{self.plan.id}/reopen/', {'note': 'let me back in'})
        self.assertEqual(r.status_code, 403)

    def test_an_employee_cannot_read_the_whole_company(self):
        for path in ('/all-plans/', '/overview/', '/activity/', '/employees/', '/export/'):
            r = self.client.get(BASE + path)
            self.assertEqual(r.status_code, 403, path)

    def test_an_employee_cannot_add_or_retire_people(self):
        r = self.post('/employees/create/', {'employee_id': 'NEW', 'name': 'Invented'})
        self.assertEqual(r.status_code, 403)
        r = self.client.delete(f'{BASE}/employees/E1/')
        self.assertEqual(r.status_code, 403)

    def test_an_employee_cannot_open_or_close_a_cycle(self):
        r = self.post('/cycles/', {'name': 'Mine', 'fiscal_year': '2026-27'})
        self.assertEqual(r.status_code, 403)
        r = self.client.patch(f'{BASE}/cycles/{self.cycle.id}/', {'status': 'closed'},
                              content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.status, 'open')

    def test_an_admin_can_do_all_of_it(self):
        admin, _ = EmployeeProfile.objects.get_or_create(
            employee_id='GS-ADMIN', defaults={'name': 'Admin', 'user_type': 'admin'})
        self.client.defaults['HTTP_X_GOALSETTING_SESSION'] = issue_session(admin)
        self.assertEqual(self.client.get(f'{BASE}/overview/').status_code, 200)
        r = self.post(f'/plans/{self.plan.id}/status/',
                      {'status': 'with_hod', 'actor_name': 'Admin'})
        self.assertEqual(r.status_code, 200, r.content)


class SigningInProvesIt(Fixture):
    """The OTP has to be worth something."""

    def test_verifying_an_otp_returns_a_session(self):
        from .models import OTPToken
        from django.utils import timezone
        from datetime import timedelta
        self.emp.email = 'rahul@apisindia.com'
        self.emp.save()
        OTPToken.objects.create(employee=self.emp, otp_code='123456',
                                expires_at=timezone.now() + timedelta(minutes=5))
        r = self.post('/auth/verify-otp/', {'employee_id': 'E1', 'otp': '123456'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.json().get('session'))

    def test_the_session_says_who_you_are_whatever_the_body_claims(self):
        """A signed-in employee naming someone else is still themselves."""
        self.client.defaults['HTTP_X_GOALSETTING_SESSION'] = issue_session(self.emp)
        r = self.client.get(f'{BASE}/plans/E1/{self.cycle.id}/?actor_employee_id=GS-ADMIN')
        self.assertEqual(r.json()['your_role'], 'employee')

    def test_a_manager_cannot_pass_themselves_off_as_the_employee(self):
        self.client.defaults['HTTP_X_GOALSETTING_SESSION'] = issue_session(self.mgr)
        r = self.post(f'/plans/E1/{self.cycle.id}/',
                      {'actor_employee_id': 'E1', 'role': 'employee', 'kras': FULL})
        self.assertEqual(r.status_code, 403)
