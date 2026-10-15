import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook, load_workbook

from accounts.models import PortalSession, PortalUser

from .columns import COLUMNS
from .models import CtcCandidate, CtcUpload

BASE = '/api/ctc'
HEADINGS = [h for h, *_ in COLUMNS]


def _xlsx(rows, headings=HEADINGS, name='candidates.xlsx'):
    wb = Workbook()
    ws = wb.active
    ws.append(headings)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return SimpleUploadedFile(name, buf.getvalue())


ROW = ['Rahul Sharma', 'Sales', 'Area Sales Manager', 'M3', 'Management', 'Delhi HO', 1200000]


class CtcCalculator(TestCase):
    def setUp(self):
        self.admin = PortalUser.objects.create(email='a@apisindia.com', employee_code='A1',
                                               name='Admin', is_superadmin=True)
        self.alice = PortalUser.objects.create(email='alice@apisindia.com', employee_code='E1',
                                               name='Alice')
        self.bob = PortalUser.objects.create(email='bob@apisindia.com', employee_code='E2',
                                             name='Bob')

    def _auth(self, user):
        return {'HTTP_AUTHORIZATION': f'Bearer {PortalSession.start(user)}'}

    def _upload(self, user, f):
        return self.client.post(f'{BASE}/uploads/', {'file': f}, **self._auth(user))

    def test_template_has_exactly_the_columns_the_uploader_reads(self):
        r = self.client.get(f'{BASE}/template/', **self._auth(self.alice))
        self.assertEqual(r.status_code, 200)
        ws = load_workbook(io.BytesIO(r.content)).worksheets[0]
        self.assertEqual([c.value for c in ws[1]], HEADINGS)
        self.assertEqual(ws.max_row, 1, 'the upload sheet must hold nothing but headings')

    def test_everything_needs_sign_in(self):
        self.assertEqual(self.client.get(f'{BASE}/template/').status_code, 401)
        self.assertEqual(self.client.get(f'{BASE}/uploads/').status_code, 401)
        self.assertEqual(self.client.post(f'{BASE}/uploads/', {'file': _xlsx([ROW])}).status_code, 401)

    def test_upload_stores_every_row(self):
        second = ['Priya Verma', 'Marketing', 'Brand Manager', 'M2', 'Management', 'Roorkee', '12,50,000']
        r = self._upload(self.alice, _xlsx([ROW, [], second]))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['rowCount'], 2)
        names = list(CtcCandidate.objects.values_list('candidate_name', flat=True))
        self.assertEqual(names, ['Rahul Sharma', 'Priya Verma'])
        priya = CtcCandidate.objects.get(candidate_name='Priya Verma')
        self.assertEqual((priya.grade, priya.cadre, priya.work_location), ('M2', 'Management', 'Roorkee'))
        self.assertEqual(str(priya.ctc), '1250000.00')
        self.assertEqual(priya.row_number, 4)

    def test_headings_are_matched_loosely(self):
        loose = ['candidate name ', 'DEPARTMENT', 'designation', 'Grade', 'cadre', 'work_location', 'ctc']
        self.assertEqual(self._upload(self.alice, _xlsx([ROW], headings=loose)).status_code, 201)

    def test_a_missing_column_is_named(self):
        r = self._upload(self.alice, _xlsx([ROW[:5]], headings=HEADINGS[:5]))
        self.assertEqual(r.status_code, 400)
        self.assertIn('Work Location', r.json()['error'])
        self.assertIn('CTC', r.json()['error'])

    def test_any_bad_row_and_nothing_is_stored(self):
        no_name = ['', 'Sales', 'Exec', 'O4', 'Officer', 'Delhi', 500000]
        bad_ctc = ['Ravi', 'Sales', 'Exec', 'O4', 'Officer', 'Delhi', 'twelve lakh']
        no_ctc = ['Amit', 'Sales', 'Exec', 'O4', 'Officer', 'Delhi', None]
        r = self._upload(self.alice, _xlsx([ROW, no_name, bad_ctc, no_ctc]))
        self.assertEqual(r.status_code, 400)
        problems = r.json()['problems']
        self.assertTrue(any('Row 3' in p and 'Candidate Name' in p for p in problems), problems)
        self.assertTrue(any('Row 4' in p and 'not a number' in p for p in problems), problems)
        self.assertTrue(any('Row 5' in p and 'CTC is empty' in p for p in problems), problems)
        self.assertFalse(CtcUpload.objects.exists())

    def test_rejects_files_that_are_not_xlsx(self):
        csv = SimpleUploadedFile('c.csv', b'Candidate Name\nRahul\n')
        self.assertEqual(self._upload(self.alice, csv).status_code, 400)
        fake = SimpleUploadedFile('c.xlsx', b'not a zip')
        self.assertEqual(self._upload(self.alice, fake).status_code, 400)

    def test_people_see_their_own_uploads_and_admin_sees_all(self):
        self._upload(self.alice, _xlsx([ROW]))
        self._upload(self.bob, _xlsx([ROW]))
        mine = self.client.get(f'{BASE}/uploads/', **self._auth(self.alice)).json()
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]['candidates'][0]['candidateName'], 'Rahul Sharma')
        self.assertEqual(mine[0]['candidates'][0]['ctc'], '1200000.00')
        self.assertEqual(len(self.client.get(f'{BASE}/uploads/', **self._auth(self.admin)).json()), 2)

    def test_only_the_uploader_or_admin_removes_an_upload(self):
        self._upload(self.alice, _xlsx([ROW]))
        u = CtcUpload.objects.get()
        self.assertEqual(self.client.delete(f'{BASE}/uploads/{u.id}/', **self._auth(self.bob)).status_code, 403)
        self.assertEqual(self.client.delete(f'{BASE}/uploads/{u.id}/', **self._auth(self.alice)).status_code, 200)
        self.assertFalse(CtcCandidate.objects.exists())
