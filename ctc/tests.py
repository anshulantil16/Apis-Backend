import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from openpyxl import Workbook, load_workbook

from accounts.models import PortalSession, PortalUser

from .calculator import calculate, state_for, vpp_percent
from .columns import COLUMNS
from .models import CtcCandidate, CtcSettings, CtcUpload

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


# Candidate Name, Category, Department, Designation, Grade, Cadre, Work Location, CTC
ROW = ['Rahul Sharma', 'Sales', 'General Trade', 'Area Sales Manager', 'M1', 'M', 'Delhi HO', 1200000]


class CtcUploads(TestCase):
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
        lists = [dv.formula1 for dv in ws.data_validations.dataValidation]
        self.assertIn('"Sales,HO,Plant"', lists)
        self.assertIn('"D,C,M,O,W"', lists)

    def test_everything_needs_sign_in(self):
        for url in ('template/', 'uploads/', 'settings/', 'candidates/1/breakdown/', 'candidates/1/pdf/'):
            self.assertEqual(self.client.get(f'{BASE}/{url}').status_code, 401, url)
        self.assertEqual(self.client.post(f'{BASE}/uploads/', {'file': _xlsx([ROW])}).status_code, 401)

    def test_upload_stores_every_row(self):
        second = ['Priya Verma', 'ho', 'Finance', 'Manager', 'm3', 'm', 'Roorkee', '12,50,000']
        r = self._upload(self.alice, _xlsx([ROW, [], second]))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['rowCount'], 2)
        priya = CtcCandidate.objects.get(candidate_name='Priya Verma')
        self.assertEqual((priya.category, priya.grade, priya.cadre), ('HO', 'M3', 'M'))
        self.assertEqual(str(priya.ctc), '1250000.00')
        self.assertEqual(priya.row_number, 4)

    def test_headings_are_matched_loosely(self):
        loose = ['candidate name ', 'CATEGORY', 'department', 'designation', 'Grade', 'cadre',
                 'work_location', 'ctc']
        self.assertEqual(self._upload(self.alice, _xlsx([ROW], headings=loose)).status_code, 201)

    def test_a_missing_column_is_named(self):
        r = self._upload(self.alice, _xlsx([ROW[:5]], headings=HEADINGS[:5]))
        self.assertEqual(r.status_code, 400)
        for heading in ('Cadre', 'Work Location', 'CTC'):
            self.assertIn(heading, r.json()['error'])

    def test_any_bad_row_and_nothing_is_stored(self):
        bad = [
            ['', 'Sales', 'GT', 'ASM', 'M1', 'M', 'Delhi', 500000],                # row 3: no name
            ['Ravi', 'Sales', 'GT', 'ASM', 'M1', 'M', 'Delhi', 'twelve lakh'],     # row 4: CTC text
            ['Amit', 'Sales', 'GT', 'ASM', 'M1', 'M', 'Delhi', None],              # row 5: no CTC
            ['Neha', 'Marketing', 'GT', 'ASM', 'M1', 'M', 'Delhi', 500000],        # row 6: category
            ['Om', 'HO', 'Fin', 'Exec', 'O4', 'Officer', 'Delhi', 500000],         # row 7: ok-ish cadre "O"
            ['Raj', 'HO', 'Fin', 'Exec', 'O4', 'M', 'Delhi', 500000],              # row 8: grade/cadre clash
            ['Sia', 'HO', 'Fin', 'Exec', '', '', 'Delhi', 500000],                 # row 9: no grade/cadre
        ]
        r = self._upload(self.alice, _xlsx([ROW, *bad]))
        self.assertEqual(r.status_code, 400)
        p = ' | '.join(r.json()['problems'])
        for expected in ('Row 3: Candidate Name is empty', 'Row 4: CTC "twelve lakh" is not a number',
                         'Row 5: CTC is empty', 'Row 6: Category "Marketing"',
                         'Row 8: Grade O4 does not belong to cadre M',
                         'Row 9: Grade is empty', 'Row 9: Cadre is empty'):
            self.assertIn(expected, p)
        self.assertNotIn('Row 7', p, '"Officer" should read as cadre O')
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
        self.assertEqual(mine[0]['candidates'][0]['category'], 'Sales')
        self.assertEqual(mine[0]['candidates'][0]['ctc'], '1200000.00')
        self.assertEqual(len(self.client.get(f'{BASE}/uploads/', **self._auth(self.admin)).json()), 2)

    def test_reset_needs_the_word_and_erases_only_what_the_caller_sees(self):
        self._upload(self.alice, _xlsx([ROW, ROW]))
        self._upload(self.bob, _xlsx([ROW]))
        url = f'{BASE}/reset/'
        self.assertEqual(self.client.post(url, {}).status_code, 401)
        self.assertEqual(self.client.post(url, {'confirm': 'yes'}, **self._auth(self.alice)).status_code, 400)
        self.assertEqual(CtcUpload.objects.count(), 2)

        r = self.client.post(url, {'confirm': 'RESET'}, **self._auth(self.alice))
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['deleted'], {'uploads': 1, 'candidates': 2})
        self.assertEqual(list(CtcUpload.objects.values_list('uploaded_by', flat=True)), [self.bob.id],
                         "Alice's reset touched Bob's upload")

        self.client.post(url, {'confirm': 'reset'}, **self._auth(self.admin))
        self.assertFalse(CtcCandidate.objects.exists())

    def test_only_the_uploader_or_admin_removes_an_upload(self):
        self._upload(self.alice, _xlsx([ROW]))
        u = CtcUpload.objects.get()
        self.assertEqual(self.client.delete(f'{BASE}/uploads/{u.id}/', **self._auth(self.bob)).status_code, 403)
        self.assertEqual(self.client.delete(f'{BASE}/uploads/{u.id}/', **self._auth(self.alice)).status_code, 200)
        self.assertFalse(CtcCandidate.objects.exists())

    # -- breakdown, PDF, settings ---------------------------------------------
    def test_breakdown_and_pdf_for_the_uploader_only(self):
        self._upload(self.alice, _xlsx([ROW]))
        c = CtcCandidate.objects.get()
        r = self.client.get(f'{BASE}/candidates/{c.id}/breakdown/', **self._auth(self.alice))
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()['breakdown']
        self.assertEqual(b['vppPercent'], 10)
        self.assertEqual(b['totalCtcAnnual'], 1200000)

        pdf = self.client.get(f'{BASE}/candidates/{c.id}/pdf/', **self._auth(self.alice))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf['Content-Type'], 'application/pdf')
        self.assertTrue(pdf.content.startswith(b'%PDF-'))
        self.assertIn('CTC_Breakup_Rahul_Sharma.pdf', pdf['Content-Disposition'])

        for url in ('breakdown/', 'pdf/'):
            self.assertEqual(self.client.get(f'{BASE}/candidates/{c.id}/{url}', **self._auth(self.bob)).status_code,
                             404, f"Bob could open Alice's candidate via {url}")
        self.assertEqual(self.client.get(f'{BASE}/candidates/{c.id}/pdf/', **self._auth(self.admin)).status_code, 200)

    def test_only_a_superadmin_edits_settings_and_they_feed_the_breakdown(self):
        url = f'{BASE}/settings/'
        self.assertFalse(self.client.get(url, **self._auth(self.alice)).json()['canEdit'])
        r = self.client.put(url, {'ltaM': 2000}, content_type='application/json', **self._auth(self.alice))
        self.assertEqual(r.status_code, 403)
        r = self.client.put(url, {'ltaM': '2000', 'gratuityPercent': '4.81'},
                            content_type='application/json', **self._auth(self.admin))
        self.assertEqual(r.status_code, 200, r.content)
        s = CtcSettings.current()
        self.assertEqual((s.lta_m, str(s.gratuity_percent)), (2000, '4.81'))
        bad = self.client.put(url, {'ltaM': 'lots'}, content_type='application/json', **self._auth(self.admin))
        self.assertEqual(bad.status_code, 400)

        self._upload(self.alice, _xlsx([ROW]))
        b = self.client.get(f'{BASE}/candidates/{CtcCandidate.objects.get().id}/breakdown/',
                            **self._auth(self.alice)).json()['breakdown']
        lta = next(x for x in b['earnings'] if x['label'].startswith('Leave Travel'))
        self.assertEqual(lta['monthly'], 2000)


class Calculator(TestCase):
    def setUp(self):
        self.s = CtcSettings.current()

    def calc(self, **kw):
        base = dict(ctc_annual=1200000, cadre='M', grade='M1', category='Sales',
                    department='General Trade', designation='Area Sales Manager',
                    work_location='Delhi HO', settings=self.s)
        return calculate(**{**base, **kw})

    def test_components_add_up_to_the_ctc(self):
        for ctc, cadre, grade, cat, loc in [(1200000, 'M', 'M1', 'Sales', 'Delhi HO'),
                                            (300000, 'O', 'O3', 'Plant', 'Delhi'),
                                            (300000, 'O', 'O3', 'Plant', 'Roorkee'),
                                            (3500000, 'C', 'C2', 'HO', 'Mumbai'),
                                            (180000, 'W', 'W2', 'Plant', 'Rajasthan'),
                                            (9000000, 'D', 'D1', 'HO', 'Delhi')]:
            b = self.calc(ctc_annual=ctc, cadre=cadre, grade=grade, category=cat, work_location=loc)
            self.assertNotIn('error', b, (ctc, cadre))
            gross = sum(x['monthly'] for x in b['earnings']) + sum(x['monthly'] for x in b['reimbursements'])
            self.assertEqual(gross, b['grossSalary'])
            self.assertEqual(b['grossSalary'] + b['benefitsTotal'], b['fixedMonthly'])
            self.assertLessEqual(abs(b['totalCtcAnnual'] - ctc), 12, (ctc, cadre))

    def test_basic_is_half_of_gross_and_hra_half_of_basic(self):
        b = self.calc()
        basic, hra = b['earnings'][0]['monthly'], b['earnings'][1]['monthly']
        self.assertAlmostEqual(basic, b['grossSalary'] / 2, delta=1)
        self.assertAlmostEqual(hra, basic / 2, delta=1)
        w = self.calc(ctc_annual=180000, cadre='W', grade='W2', category='Plant', work_location='Rajasthan')
        self.assertAlmostEqual(w['earnings'][1]['monthly'], w['earnings'][0]['monthly'] * 0.2, delta=1)

    def test_vpp_and_gift_come_off_the_top(self):
        b = self.calc()
        vpp, gift = b['otherPayments']
        self.assertEqual(vpp['annual'], 120000)              # 10% of 12 lakh
        self.assertEqual(gift['monthly'], 1250)
        for cadre, grade in (('O', 'O3'), ('W', 'W2')):
            other = self.calc(ctc_annual=300000, cadre=cadre, grade=grade, category='Plant')['otherPayments']
            self.assertEqual(other[1]['annual'], 0, f'cadre {cadre} should get no gift')
        self.assertEqual(self.calc(category='HO', grade='M1')['vppPercent'], 0)
        self.assertEqual(self.calc(category='HO', grade='M3', cadre='M')['vppPercent'], 8)
        self.assertEqual(self.calc(category='Plant', grade='M4')['vppPercent'], 8)

    def test_vpp_sales_table(self):
        self.assertEqual(vpp_percent('Sales', 'M4', 'Alternate Channels', 'Regional Sales Manager')[0], 12)
        self.assertEqual(vpp_percent('Sales', 'M1', 'Alternate Channels', 'key account manager')[0], 12)
        self.assertEqual(vpp_percent('Sales', 'M1', 'General Trade', 'Key Account Manager')[0], 0)
        self.assertEqual(vpp_percent('Sales', 'C2', '', 'NSH')[0], 15)

    def test_esi_and_bonus_only_for_small_salaries(self):
        small = self.calc(ctc_annual=240000, cadre='O', grade='O1', category='Plant', work_location='Rajasthan')
        self.assertLessEqual(small['grossSalary'], 21000)
        names = {x['label']: x['monthly'] for x in small['benefits']}
        self.assertGreater(names['Employer ESI Contribution'], 0)
        self.assertGreater(names['Statutory Bonus'], 0)
        big = {x['label']: x['monthly'] for x in self.calc()['benefits']}
        self.assertEqual((big['Employer ESI Contribution'], big['Statutory Bonus']), (0, 0))

    def test_pf_is_capped_and_mediclaim_follows_grade(self):
        b = {x['label']: x['monthly'] for x in self.calc(ctc_annual=3500000, cadre='C', grade='C2',
                                                          category='HO')['benefits']}
        self.assertEqual(b['Employer PF Contribution'], 1800)
        self.assertEqual(b['Mediclaim Charges'], 950)

    def test_reimbursements_are_trimmed_not_overdrawn(self):
        b = self.calc(ctc_annual=420000, category='HO')
        spl = next(x for x in b['earnings'] if x['label'].startswith('Special'))
        self.assertGreaterEqual(spl['monthly'], 0)
        self.assertTrue(any('reduced' in n for n in b['notes']))

    def test_work_location_resolves_to_a_state(self):
        for typed, state in [('Delhi HO', 'Delhi'), ('UTTAR PRADESH', 'Uttar Pradesh'),
                             ('Roorkee Plant', 'Uttarakhand'), ('Mumbai', 'Maharashtra'),
                             ('Andhra Pradesh', 'Andhra Pradesh'), ('Jammu & Kashmir', 'Jammu & Kashmir'),
                             ('Daman (UT)', 'Daman (UT)'), ('Goa', 'Goa'), ('Atlantis', '')]:
            self.assertEqual(state_for(typed), state, typed)

    def test_basic_never_below_the_state_minimum_wage(self):
        # Rs 3 lakh in Delhi: half of gross is well under Delhi's Rs 22,500, so
        # Basic is lifted to it and HRA shrinks to fit.
        b = self.calc(ctc_annual=300000, cadre='O', grade='O1', category='Plant', work_location='Delhi')
        basic, hra = b['earnings'][0], b['earnings'][1]
        self.assertEqual(basic['monthly'], 22500)
        self.assertIn('Minimum wage, Delhi', basic['note'])
        self.assertIn('Reduced', hra['note'])
        # Same CTC in Rajasthan (Rs 8,950): 50% of gross is higher, so that stands.
        r = self.calc(ctc_annual=300000, cadre='O', grade='O1', category='Plant', work_location='Rajasthan')
        self.assertIn('% of Gross Salary', r['earnings'][0]['note'])
        self.assertGreater(r['earnings'][0]['monthly'], 8950)
        unknown = self.calc(work_location='Atlantis')
        self.assertTrue(any('No minimum wage found' in n for n in unknown['notes']))
        # A CTC that can't cover the minimum wage at all is refused, not fudged.
        low = self.calc(ctc_annual=240000, cadre='O', grade='O1', category='Plant', work_location='Delhi')
        self.assertIn('minimum wage', low['error'])

    def test_unworkable_input_is_an_error_not_a_number(self):
        self.assertIn('error', self.calc(cadre='Z'))
        self.assertIn('error', self.calc(ctc_annual=10000))
