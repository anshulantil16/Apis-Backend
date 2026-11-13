"""The list of people who cannot sign in.

This feeds a CSV that P&C act on, so the two ways it could mislead both
matter: naming somebody who can sign in perfectly well wastes their time,
and leaving somebody out means that person stays locked out silently.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from accounts.models import PortalUser


def person(code, name, email=None, **extra):
    return PortalUser.objects.create(employee_code=code, name=name, email=email, **extra)


class WhoIsListed(TestCase):
    def setUp(self):
        person('HO0601', 'Asha Mehta', department='Sales')
        person('HO0602', 'Bhavesh Shah', email='bhavesh@apisindia.com', department='Sales')
        person('HO0603', 'Chandra Iyer', department='Plant')

    def _run(self, *args):
        out = StringIO()
        call_command('people_without_email', *args, stdout=out)
        return out.getvalue()

    def test_lists_only_the_people_with_no_address(self):
        text = self._run()
        self.assertIn('Asha Mehta', text)
        self.assertIn('Chandra Iyer', text)
        self.assertNotIn('Bhavesh Shah', text)

    def test_leavers_are_left_out_by_default(self):
        """Somebody who has left needs no sign-in, and putting them on the
        list sends P&C chasing an address for a person who is gone."""
        person('HO0604', 'Deepa Nair', is_active=False)
        self.assertNotIn('Deepa Nair', self._run())
        self.assertIn('Deepa Nair', self._run('--all'))

    def test_the_header_names_every_column(self):
        first = self._run().splitlines()[0]
        self.assertIn('Employee Code', first)
        self.assertIn('Reporting Manager', first)
        self.assertIn('HOD', first)

    def test_the_manager_is_carried_so_somebody_can_be_asked(self):
        """An employee code on its own tells an HR executive nothing about
        who to chase for the address."""
        person('HO0605', 'Esha Rao', department='Plant',
               reporting_manager_name='Pankaj Tripathi', reporting_manager_code='HO0235')
        text = self._run()
        self.assertIn('Pankaj Tripathi', text)
        self.assertIn('HO0235', text)

    def test_missing_fields_come_out_blank_not_as_the_word_none(self):
        """A CSV with 'None' in it is one somebody has to clean by hand."""
        self.assertNotIn('None', self._run())

    def test_grouped_by_department_so_it_can_be_split_up(self):
        names = [line.split(',')[1] for line in self._run().splitlines()[1:]]
        self.assertEqual(names, ['Chandra Iyer', 'Asha Mehta'],
                         'Plant before Sales, and sorted by name inside each')

    def test_the_count_view_breaks_it_down_by_department(self):
        text = self._run('--count')
        self.assertIn('2 people have no email', text)
        self.assertIn('Sales', text)
        self.assertIn('Plant', text)

    def test_says_so_plainly_when_there_is_nothing_to_report(self):
        PortalUser.objects.filter(email__isnull=True).update(email=None, is_active=False)
        self.assertIn('Everybody in the directory has an email', self._run())
