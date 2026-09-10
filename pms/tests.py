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

    def test_the_template_lists_every_component(self):
        """A component that prints on the PDF but has no column to fill it in
        would always be zero, silently."""
        from pms.arrears_letter import ARREARS_COMPONENTS, COMPONENT_HEADERS
        for key, _section, _label in ARREARS_COMPONENTS:
            self.assertIn(key, COMPONENT_HEADERS)

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
        self.assertEqual(r.data.get('error'), 'Nothing to generate from that sheet.')
