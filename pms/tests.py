from django.test import TestCase

# Create your tests here.


class ArrearsStructure(TestCase):
    """The Arrears Compensation Structure: the arithmetic, and the flow.

    The arithmetic gets its own tests because this document tells someone what
    they are owed. Getting it wrong is not a display bug.
    """

    FULL = {
        'basic': 0, 'hra': 135417, 'cea': 3000, 'lta': 15000, 'special': 36833,
        'meal': 5200, 'telephone': 6000, 'health': 0, 'books': 5000,
        'uniform': 10000, 'fuel': 5000, 'driver': 3000, 'car_lease': 0,
        'emp_pf': 1800, 'emp_esi': 0, 'mediclaim': 0, 'nps': 0, 'income_tax': 0,
        'variable': 43333, 'gift': 1250,
    }

    def test_the_subtotals_add_up(self):
        from pms.arrears_letter import totals
        t = totals(self.FULL)
        self.assertEqual(t['gross_earnings'], 190250)
        self.assertEqual(t['reimbursement'], 34200)
        self.assertEqual(t['gross'], 224450)
        self.assertEqual(t['deductions'], 1800)

    def test_in_hand_is_gross_less_deductions(self):
        """The spreadsheet this was modelled on ADDS the deductions, so its
        "Total In Hand" came out above its own Gross - impossible for a figure
        defined as what is left after deductions."""
        from pms.arrears_letter import totals
        t = totals(self.FULL)
        self.assertEqual(t['in_hand'], t['gross'] - t['deductions'])
        self.assertEqual(t['in_hand'], 222650)
        self.assertLess(t['in_hand'], t['gross'])

    def test_quarterly_payments_sit_on_top_of_in_hand(self):
        from pms.arrears_letter import totals
        t = totals(self.FULL)
        self.assertEqual(t['other_payments'], 44583)
        self.assertEqual(t['total_with_other'], t['in_hand'] + t['other_payments'])

    def test_blank_and_junk_amounts_count_as_nothing(self):
        """Uploaded sheets carry empty cells, stray text and comma-formatted
        numbers. None of those may become a wrong figure on a salary document."""
        from pms.arrears_letter import totals
        t = totals({'hra': '', 'cea': None, 'lta': 'n/a', 'special': '1,500'})
        self.assertEqual(t['gross_earnings'], 1500)

    def test_a_missing_component_is_simply_zero(self):
        from pms.arrears_letter import totals
        self.assertEqual(totals({})['gross'], 0)
        self.assertEqual(totals({})['in_hand'], 0)

    def test_the_pdf_builds_and_carries_no_missing_glyphs(self):
        """reportlab's built-in fonts have no rupee sign, and a black box on a
        salary statement is not acceptable - so amounts read "Rs."."""
        from pms.arrears_letter import generate_arrears_pdf

        class L:
            employee_name, department, designation = 'Test Person', 'Sales', 'Manager'
            cadre, grade, paid_days = 'M', 'M5', '30'
            salary_breakup = ArrearsStructure.FULL
        data = generate_arrears_pdf(L()).read()
        self.assertGreater(len(data), 2000)
        self.assertTrue(data.startswith(b'%PDF'))

    def test_the_letter_always_fits_one_page(self):
        """The salary structure ran onto a second page, which left the totals
        stranded away from the rows they total. It auto-fits now - and this
        has to keep holding as components or detail fields are added, since
        each new row is what pushes it over."""
        from pypdf import PdfReader
        from pms.arrears_letter import (generate_arrears_pdf, ARREARS_COMPONENTS,
                                        EMP_FIELDS)

        class Worst:
            """Every component filled with a wide figure and every detail
            field carrying a long value - the tallest the letter can get."""
            employee_name = 'Ramachandran Venkataraman Subramanian'
            salary_breakup = {k: 9876543 for k, _s, _l in ARREARS_COMPONENTS}
        for attr, _label in EMP_FIELDS:
            setattr(Worst, attr, 'A Deliberately Long Sample Value 1234567')

        for case in (Worst, type('Empty', (Worst,), {'salary_breakup': {}})):
            with self.subTest(case=case.__name__):
                pages = len(PdfReader(generate_arrears_pdf(case())).pages)
                self.assertEqual(pages, 1, f'{case.__name__} spilled onto {pages} pages')

    # ── month-wise distribution ─────────────────────────────────────────
    MONTHS = [
        {'month': 'Apr 2026', 'present_days': '30',
         'components': {'basic': {'old': 27000, 'new': 28000}}},
        {'month': 'May 2026', 'present_days': '30',
         'components': {'basic': {'old': 27000, 'new': 28000}}},
        {'month': 'Jun 2026', 'present_days': '30',
         'components': {'basic': {'old': 27000, 'new': 28000}}},
        {'month': 'Jul 2026', 'present_days': '25',
         'components': {'basic': {'old': 25000, 'new': 25500}}},
    ]

    def test_the_difference_is_computed_not_read(self):
        """A difference that could disagree with the two figures beside it is
        a difference nobody can check."""
        from pms.arrears_letter import month_rows
        rows = month_rows(self.MONTHS)
        self.assertEqual([r['components']['basic']['diff'] for r in rows],
                         [1000, 1000, 1000, 500])

    def test_the_months_keep_the_order_they_were_given(self):
        """The order the arrears period runs in is information."""
        from pms.arrears_letter import month_rows
        self.assertEqual([r['month'] for r in month_rows(self.MONTHS)],
                         ['Apr 2026', 'May 2026', 'Jun 2026', 'Jul 2026'])

    def test_a_components_arrears_is_its_monthly_differences_added_up(self):
        from pms.arrears_letter import totals_from_months
        self.assertEqual(totals_from_months(self.MONTHS)['basic'], 3500)

    def test_a_months_net_takes_deductions_off_rather_than_adding_them(self):
        """Same sign convention as the summary page, or the two pages tell
        different stories about the same month."""
        from pms.arrears_letter import month_rows
        rows = month_rows([{'month': 'Apr 2026', 'present_days': '30', 'components': {
            'basic': {'old': 1000, 'new': 2000},      # +1000 earning
            'emp_pf': {'old': 100, 'new': 400},       # +300 deduction
            'variable': {'old': 0, 'new': 50},        # +50 quarterly
        }}])
        self.assertEqual(rows[0]['net'], 1000 - 300 + 50)

    def test_no_monthly_detail_leaves_the_old_behaviour_untouched(self):
        """Sheets uploaded before this existed have no Monthly rows, and must
        keep generating exactly what they did."""
        from pms.arrears_letter import totals_from_months
        self.assertEqual(totals_from_months([]), {})
        self.assertEqual(totals_from_months(None), {})

    def test_the_distribution_page_is_landscape_and_only_appears_when_earned(self):
        from pypdf import PdfReader
        from pms.arrears_letter import generate_arrears_pdf

        class L:
            employee_name, department, designation = 'Test Person', 'Sales', 'Manager'
            cadre, grade, paid_days = 'M', 'M5', '30'
            salary_breakup = ArrearsStructure.FULL
            master_breakup = {'basic': {'old': 27000, 'new': 28000}}
            monthly_breakup = []

        plain = PdfReader(generate_arrears_pdf(L()))
        self.assertEqual(len(plain.pages), 1)

        L.monthly_breakup = self.MONTHS
        pages = PdfReader(generate_arrears_pdf(L())).pages
        self.assertEqual(len(pages), 2)
        self.assertGreater(pages[1].mediabox.width, pages[1].mediabox.height)
        self.assertEqual(pages[0].mediabox.width, plain.pages[0].mediabox.width)

    def test_every_column_heading_fits_its_column(self):
        """"Difference" is one long word in a narrow column and broke as
        "Differenc e"; a figure that breaks mid-number is worse still."""
        import re
        from pypdf import PdfReader
        from pms.arrears_letter import generate_arrears_pdf, ARREARS_COMPONENTS

        for count in (1, 4, 8, 12):
            with self.subTest(months=count):
                class L:
                    employee_name, department, designation = 'Test Person', 'Sales', 'Mgr'
                    cadre, grade, paid_days = 'M', 'M5', '30'
                    salary_breakup = {}
                    master_breakup = {k: {'old': 1234567, 'new': 2345678}
                                      for k, _s, _l in ARREARS_COMPONENTS}
                    monthly_breakup = [
                        {'month': f'Mon{i} 2026', 'present_days': '30',
                         'components': {k: {'old': 1234567, 'new': 2345678}
                                        for k, _s, _l in ARREARS_COMPONENTS}}
                        for i in range(count)]
                text = re.sub(r'\s+', ' ', PdfReader(generate_arrears_pdf(L())).pages[1].extract_text())
                self.assertNotIn('Differenc e', text)
                # a lakh-grouped figure split across a wrap
                self.assertIsNone(re.search(r'\d,\d{2},\d\s', text), text[:200])

    def test_the_template_carries_a_sheet_for_the_master_and_the_months(self):
        import io, openpyxl
        from django.test import Client
        wb = openpyxl.load_workbook(
            io.BytesIO(Client().get('/api/pms/arrears/template/').content))
        self.assertEqual(wb.sheetnames, ['Arrears', 'Master', 'Monthly'])
        heads = [c.value for c in wb['Monthly'][1]]
        self.assertEqual(heads[:3], ['Employee ID *', 'Month *', 'Present Days'])
        self.assertIn('Basic Salary Old', heads)
        self.assertIn('Basic Salary New', heads)

    def test_the_template_lists_every_component(self):
        """A component that prints on the PDF but has no column to fill it in
        would always be zero, silently."""
        from pms.arrears_letter import ARREARS_COMPONENTS, COMPONENT_HEADERS
        for key, _section, _label in ARREARS_COMPONENTS:
            self.assertIn(key, COMPONENT_HEADERS)

    def test_a_dead_batch_stops_being_reported_as_running(self):
        """The worker is a daemon thread, so it dies with the process. While
        the batch still said "running" the page polled it every 1.2 seconds
        for ever - a run that had already stopped, hit forty times a minute
        until the tab was closed."""
        from datetime import timedelta
        from django.test import Client
        from django.utils import timezone
        from pms.models import ArrearsLetterBatch

        b = ArrearsLetterBatch.objects.create(batch_id='B-STALL', total=10,
                                              processed=3, status='running')
        c = Client()
        self.assertEqual(c.get('/api/pms/arrears/batch/B-STALL/').data['status'], 'running')

        # auto_now would overwrite a normal save, so move the heartbeat directly
        ArrearsLetterBatch.objects.filter(pk=b.pk).update(
            updated_at=timezone.now() - timedelta(minutes=6))
        d = c.get('/api/pms/arrears/batch/B-STALL/').data
        self.assertEqual(d['status'], 'error')
        self.assertTrue(any('restarted' in e for e in d['errors']))

    def test_history_says_when_it_is_showing_only_part_of_itself(self):
        """A capped list that does not admit it is capped reads as "that
        statement was never generated" - the one conclusion a salary document
        must never invite. The delete confirmation has to be the real total
        too, or Clear asks for a number the server will always reject."""
        from django.test import Client
        from pms.models import ArrearsLetter

        ArrearsLetter.objects.bulk_create([
            ArrearsLetter(employee_code=f'E{i:04d}', employee_name=f'Person {i}',
                          status='generated') for i in range(540)])
        c = Client()
        d = c.get('/api/pms/arrears/history/').data
        self.assertEqual(d['total'], 540)
        self.assertEqual(d['returned'], 500)
        self.assertTrue(d['truncated'])

        # what the page shows as the number to type must be what the server wants
        r = c.delete('/api/pms/arrears/history/',
                     data={'confirm_count': str(d['returned'])},
                     content_type='application/json')
        self.assertEqual(r.status_code, 400)
        r = c.delete('/api/pms/arrears/history/',
                     data={'confirm_count': str(d['total'])},
                     content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(ArrearsLetter.objects.count(), 0)

    def test_the_sample_row_in_the_template_is_not_imported(self):
        """The template ships a filled example so the shape is obvious. An
        example you must remember to delete is a trap."""
        import io, openpyxl
        from django.test import Client
        c = Client()
        wb = openpyxl.load_workbook(io.BytesIO(c.get('/api/pms/arrears/template/').content))
        codes = [r[1] for r in wb.active.iter_rows(min_row=2, values_only=True)]
        self.assertTrue(any(str(x).startswith('SAMPLE-') for x in codes))

        buf = io.BytesIO()
        wb.save(buf); buf.seek(0); buf.name = 'x.xlsx'
        r = c.post('/api/pms/arrears/upload/', {'file': buf, 'send_emails': 'false'})
        self.assertEqual(r.status_code, 400)          # nothing real left to do
        # And it says WHY, because "nothing to generate" sends someone hunting
        # for a fault in a sheet that is simply still empty.
        self.assertIn('example row', r.data.get('error', ''))
        self.assertIn('upload again', r.data.get('error', ''))
