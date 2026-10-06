"""Appraisal and EOM records survive being dumped and loaded back.

Going live keeps the existing production database and migrates it forward:
Appraisal and EOM are byte-identical between what Live runs (ccda73b) and
what is being deployed -- same models, same five and fourteen migrations --
so their tables are already where they need to be and their data never
moves. Every new project creates new tables beside them.

This still matters, for the two moments when those records do have to move:
the backup taken before the migration is run, and the restore if it has to
be undone. A backup nobody has ever restored is a hope, not a backup.

So it rehearses the round trip on every commit: foreign keys land in a
loadable order, the JSON columns keep their contents, and an empty database
-- which is what a restore lands on -- gets everything back.
"""
import io
from datetime import date

from django.core.management import call_command
from django.test import TestCase

from appraisal.models import (CompetencyRating, EmployeeProfile, Goal, GoalCard,
                              KPI, PerformanceCycle)
from eom.models import EOMCycle, EOMEmployee, EOMNomination

CARRIED = ['appraisal', 'eom']


def a_cycle():
    return PerformanceCycle.objects.create(
        name='Q2 FY26-27', quarter=2, fiscal_year='2026-27',
        goal_setting_deadline=date(2026, 7, 15),
        review_start_date=date(2026, 10, 1),
        review_deadline=date(2026, 10, 31), status='active')


class CarryingTheLiveRecordsOver(TestCase):

    def setUp(self):
        emp = EmployeeProfile.objects.create(
            employee_id='E1001', name='Priya Sharma', email='priya@apisindia.com',
            designation='Area Sales Manager', department='Sales', zone='North')
        card = GoalCard.objects.create(
            employee=emp, cycle=a_cycle(), status='hr_approved',
            manager_remarks='Strong quarter.',
            # The JSON columns are where a careless dump loses things.
            self_review_answers=[{'q': 'What went well?', 'a': 'Coverage'}],
            key_skills=['Negotiation', 'Route planning'],
            manager_uplift_ratings={'communication': 4},
            hod_competency_ratings={'ownership': 5})
        goal = Goal.objects.create(goal_card=card, title='Grow North zone',
                                   category='Sales', order=1)
        KPI.objects.create(kra=goal, metric='Primary sales', target_value='12 Cr',
                           weightage=40.0, frequency='Quarterly')
        CompetencyRating.objects.create(goal_card=card, competency='communication',
                                        marks=4, manager_remarks='Clear.')
        self.card_id = card.id

        # EOM is the other half of what moves, and it is a separate app with
        # its own employee table -- the same person exists twice, once here
        # and once in Appraisal, joined by nothing but the employee code.
        nominee = EOMEmployee.objects.create(
            employee_id='E1001', name='Priya Sharma', email='priya@apisindia.com',
            department='Sales', zone='North')
        cycle = EOMCycle.objects.create(name='September 2026', month=9, year=2026,
                                        status='closed')
        nom = EOMNomination.objects.create(
            employee=nominee, cycle=cycle, status='panel_reviewed',
            part_a_achievement='Opened 14 new outlets.',
            smart_specific='North zone, general trade.',
            declaration_agreed=True, signature_name='Priya Sharma',
            hod_recommendation='recommended', panel_sustainability_bonus=5)
        self.nom_id = nom.id

    def _wipe(self):
        """Children first: an empty production database is what the load has
        to land on, and deleting a parent first would cascade the lot."""
        for m in (KPI, Goal, CompetencyRating, GoalCard,
                  PerformanceCycle, EmployeeProfile,
                  EOMNomination, EOMCycle, EOMEmployee):
            m.objects.all().delete()

    def _dump(self):
        buf = io.StringIO()
        call_command('dumpdata', *CARRIED, indent=1, stdout=buf)
        return buf.getvalue()

    def test_the_dump_carries_every_record(self):
        blob = self._dump()
        for model in ('appraisal.employeeprofile', 'appraisal.performancecycle',
                      'appraisal.goalcard', 'appraisal.goal', 'appraisal.kpi',
                      'appraisal.competencyrating',
                      'eom.eomemployee', 'eom.eomcycle', 'eom.eomnomination'):
            self.assertIn(model, blob, f'{model} was not in the dump')

    def test_it_loads_back_onto_an_empty_database(self):
        """The real test: wipe the tables, load the file, get it all back.

        An empty database is what production will be on the day of the
        cutover, and a dump that only loads onto the database it came from
        is no use at all.
        """
        blob = self._dump()
        path = 'carry_test.json'
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(blob)
        try:
            self._wipe()
            self.assertEqual(GoalCard.objects.count(), 0)
            self.assertEqual(EOMNomination.objects.count(), 0)

            call_command('loaddata', path, verbosity=0)

            card = GoalCard.objects.get(id=self.card_id)
            self.assertEqual(card.employee.name, 'Priya Sharma')
            self.assertEqual(card.status, 'hr_approved')
            self.assertEqual(card.goals.first().title, 'Grow North zone')
            self.assertEqual(card.goals.first().kpis.first().weightage, 40.0)
            self.assertEqual(card.competency_ratings.first().marks, 4)

            nom = EOMNomination.objects.get(id=self.nom_id)
            self.assertEqual(nom.employee.name, 'Priya Sharma')
            self.assertEqual(nom.cycle.name, 'September 2026')
            self.assertEqual(nom.status, 'panel_reviewed')
            self.assertTrue(nom.declaration_agreed)
            self.assertEqual(nom.panel_sustainability_bonus, 5)
        finally:
            import os
            os.path.exists(path) and os.remove(path)

    def test_the_json_columns_survive_the_trip(self):
        """self_review_answers, key_skills and the uplift maps are where a
        dump that goes through the wrong serializer loses its contents."""
        blob = self._dump()
        path = 'carry_json_test.json'
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(blob)
        try:
            self._wipe()
            call_command('loaddata', path, verbosity=0)
            card = GoalCard.objects.get(id=self.card_id)
            self.assertEqual(card.key_skills, ['Negotiation', 'Route planning'])
            self.assertEqual(card.self_review_answers[0]['a'], 'Coverage')
            self.assertEqual(card.manager_uplift_ratings, {'communication': 4})
            self.assertEqual(card.hod_competency_ratings, {'ownership': 5})
        finally:
            import os
            os.path.exists(path) and os.remove(path)
