"""SalesIQ ingestion — what counts as a sale, and what the dump columns mean.

The Pre-Sales Dump is an ERP export, not a sales report: it carries cancelled
invoices and credit memos in the same sheet as live billing, splits the money
across a dozen columns, and repeats several figures in two forms. These tests
hold the decisions that turn it into numbers a person can act on.
"""
import io

import openpyxl
from datetime import date, datetime

from django.test import Client as RawClient, TestCase as _TestCase


class Client(RawClient):
    """A signed-in SalesIQ owner.

    Every endpoint in this app now requires a session (see
    views/auth.SalesIQView); these tests are about what the figures say, not
    about the gate, so they get one by default. The two classes that ARE
    about the gate use RawClient, which carries no session.
    """

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        from sales.views.auth import SALESIQ_SUPER_ADMIN, issue_session
        self.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session(SALESIQ_SUPER_ADMIN)


class TestCase(_TestCase):
    """so `self.client` is signed in too."""
    client_class = Client

from sales import aop as AOP

from sales.ingest import (PRE_SALES_DUMP, build_template, is_return_type,
                          map_headers, parse_bool)
from sales.models import SalesRecord, SalesUpload


def header_names():
    """The dump's headers as the ERP writes them — '*' is our own marking."""
    return [h.replace(' *', '') for h, _ in PRE_SALES_DUMP]


def a_workbook(rows, headers=None):
    """A workbook with the dump's headers and the given data rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    names = headers or header_names()
    ws.append(names)
    for r in rows:
        ws.append([r.get(n, '') for n in names])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = 'dump.xlsx'
    return buf


def a_row(**over):
    """One live invoice line. Money kept simple so totals are checkable by eye."""
    d = {
        'Order Date': '2026-04-05', 'Type': 'Invoice', 'Cancelled': 'No',
        'Customer No.': 'CUST-001', 'Customer Name': 'Sharma Traders',
        'Zone': 'North', 'Cust.State Code': '07', 'Customer City': 'New Delhi',
        'Item Code': 'APS-HNY-500', 'Item Name': 'APIS Honey 500g',
        'Item Category': 'Honey', 'Quantity': 100, 'Unit Price': 250,
        'Taxable Amount': 20000,
        'Invoice Disc.Amt': 1000, 'Retail Scheme Amt': 500, 'Wholesale Scheme Amt': 500,
        'IGST Amount': 0, 'CGST Amount': 1800, 'SGST Amount': 1800,
    }
    d.update(over)
    return d


def upload(buf):
    return Client().post('/api/sales/upload/', {'file': buf})


class TheTemplateAndTheMapper(TestCase):
    """The template is documentation of what the upload does. If the two
    disagree, the template is a lie."""

    def test_every_column_the_template_marks_as_read_is_actually_read(self):
        col_map, unknown = map_headers(header_names())
        promised = [h.replace(' *', '') for h, used in PRE_SALES_DUMP if used]
        broken = [h for h in promised if h in unknown]
        self.assertEqual(broken, [], f'template promises these are read, mapper drops them: {broken}')

    def test_every_column_the_template_marks_as_ignored_really_is(self):
        """A column shown as grey must not quietly land in a field — that is
        worse than dropping it, because nobody goes looking for it."""
        _, unknown = map_headers(header_names())
        ignored = [h for h, used in PRE_SALES_DUMP if not used]
        leaked = [h for h in ignored if h not in unknown]
        self.assertEqual(leaked, [], f'marked ignored but stored anyway: {leaked}')

    def test_the_template_opens_and_carries_both_sheets(self):
        wb = openpyxl.load_workbook(build_template())
        self.assertEqual(wb.sheetnames, ['Pre Sales Dump', 'How To Use'])
        ws = wb['Pre Sales Dump']
        self.assertEqual([c.value for c in ws[1]], [h for h, _ in PRE_SALES_DUMP])

    def test_the_sample_row_fills_every_column(self):
        """A sample that runs short silently shifts every later value into the
        wrong column, which is how a template teaches the wrong shape."""
        ws = openpyxl.load_workbook(build_template())['Pre Sales Dump']
        self.assertEqual(len(list(ws[2])), len(PRE_SALES_DUMP))

    def test_the_batch_column_stays_text_so_excel_keeps_leading_zeros(self):
        ws = openpyxl.load_workbook(build_template())['Pre Sales Dump']
        col = [h for h, _ in PRE_SALES_DUMP].index('Batch No.') + 1
        letter = ws.cell(row=1, column=col).column_letter
        self.assertEqual(ws[f'{letter}500'].number_format, '@')


class WhatCountsAsASale(TestCase):

    def test_a_cancelled_invoice_is_kept_but_never_counted(self):
        r = upload(a_workbook([a_row(), a_row(**{'Cancelled': 'Yes'})]))
        self.assertEqual(r.status_code, 200, r.content[:300])

        self.assertEqual(SalesRecord.objects.count(), 2)          # both stored
        self.assertEqual(SalesRecord.objects.filter(is_cancelled=True).count(), 1)
        # ...but only one of them is revenue.
        self.assertEqual(float(SalesUpload.objects.get().total_revenue), 20000)

    def test_the_dashboard_does_not_see_cancelled_rows(self):
        upload(a_workbook([a_row(), a_row(**{'Cancelled': 'Yes'})]))
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(float(d.get('total_revenue') or d.get('revenue') or 0), 20000)

    def test_only_a_real_yes_means_cancelled(self):
        """An unrecognised value must read as NOT cancelled — hiding a real
        sale is the worse of the two mistakes."""
        for yes in ('Yes', 'yes', 'Y', 'TRUE', '1', True):
            self.assertTrue(parse_bool(yes), yes)
        for no in ('No', 'N', '', None, 0, False, 'FALSE', 'false', 'maybe', '-'):
            self.assertFalse(parse_bool(no), no)

    def test_a_real_excel_boolean_is_understood(self):
        """The export writes Cancelled as a genuine Excel boolean, which
        openpyxl hands back as Python True/False rather than as text. Excel
        shows those as TRUE/FALSE and files them under Number Filters, so
        they never arrive as the strings they look like."""
        upload(a_workbook([a_row(**{'Cancelled': False}),
                           a_row(**{'Cancelled': True, 'Taxable Amount': 99999})]))
        self.assertEqual(SalesRecord.objects.filter(is_cancelled=True).count(), 1)
        self.assertEqual(SalesRecord.objects.filter(is_cancelled=False).count(), 1)
        # The cancelled 99,999 must not be in the money.
        self.assertEqual(float(SalesUpload.objects.get().total_revenue), 20000)

    def test_the_words_true_and_false_work_too(self):
        """Some exports write the words rather than the boolean."""
        upload(a_workbook([a_row(**{'Cancelled': 'FALSE'}),
                           a_row(**{'Cancelled': 'TRUE', 'Taxable Amount': 99999})]))
        self.assertEqual(SalesRecord.objects.filter(is_cancelled=True).count(), 1)
        self.assertEqual(float(SalesUpload.objects.get().total_revenue), 20000)

    def test_the_type_column_is_the_line_type_not_the_document_type(self):
        """This export writes Item for a product line and G/L Account for a
        charge posted to a ledger account. Reading it as a document type made
        every row look like a document kind nobody recognised."""
        upload(a_workbook([a_row(**{'Type': 'Item'}),
                           a_row(**{'Type': 'G/L Account', 'Invoice No.': 'INV-2'})]))
        self.assertEqual(
            sorted(SalesRecord.objects.values_list('line_type', flat=True)),
            ['G/L Account', 'Item'])
        # ...and none of them is a return.
        self.assertEqual(SalesRecord.objects.filter(is_return=True).count(), 0)

    def test_money_on_a_non_product_line_is_called_out(self):
        """A G/L Account line is real money on the invoice but has no product,
        so it cannot appear in a product breakdown. Without saying so, those
        views quietly add up to less than the headline."""
        d = upload(a_workbook([a_row(**{'Type': 'Item'}),
                               a_row(**{'Type': 'G/L Account', 'Invoice No.': 'INV-2',
                                        'Taxable Amount': 500})])).json()
        blob = ' '.join(d['notes'])
        self.assertIn('G/L Account', blob)
        self.assertIn('no product', blob)

    def test_a_credit_memo_is_flagged_as_a_return(self):
        """Returns come from a Document Type column when the export has one.
        The Type column in this dump does not carry them."""
        upload(a_workbook([a_row(), a_row(**{'Document Type': 'Credit Memo',
                                             'Invoice No.': 'INV-2'})],
                          headers=header_names() + ['Document Type']))
        self.assertEqual(SalesRecord.objects.filter(is_return=True).count(), 1)
        self.assertTrue(is_return_type('Credit Memo'))
        self.assertTrue(is_return_type('Sales Credit Memo'))
        self.assertFalse(is_return_type('Invoice'))
        self.assertFalse(is_return_type('Item'))
        self.assertFalse(is_return_type('G/L Account'))

    def test_the_operator_is_told_what_was_found(self):
        """Silently dropping cancelled rows is right; not saying so is not.

        The two land differently on purpose. Excluding cancelled rows is the
        import doing its job, so it is a note. A credit memo needs a decision
        about its sign, so it is a warning.
        """
        d = upload(a_workbook(
            [a_row(**{'Cancelled': 'Yes'}),
             a_row(**{'Document Type': 'Credit Memo', 'Invoice No.': 'INV-2'})],
            headers=header_names() + ['Document Type'])).json()
        self.assertIn('cancelled', ' '.join(d['notes']).lower())
        self.assertIn('return row', ' '.join(d['warnings']).lower())

    def test_a_clean_import_raises_no_warnings_at_all(self):
        """A normal ERP export used to come back with seven amber warnings,
        every one of them saying the import had worked."""
        d = upload(a_workbook([a_row()])).json()
        self.assertEqual(d['warnings'], [], d['warnings'])
        self.assertTrue(d['notes'])


class TheDumpNamesNoState(TestCase):
    """Only Cust.State Code, which is a GST code. Without translating it the
    state-wise view — the dashboard's default — is blank on a file that
    plainly knows where every sale went."""

    def test_the_gst_code_becomes_a_state_name(self):
        from sales.ingest import state_from_code
        self.assertEqual(state_from_code('07'), 'Delhi')
        self.assertEqual(state_from_code('27'), 'Maharashtra')
        self.assertEqual(state_from_code('33'), 'Tamil Nadu')
        # Excel drops the leading zero off a text code.
        self.assertEqual(state_from_code(7), 'Delhi')
        self.assertEqual(state_from_code('7'), 'Delhi')
        # A full GSTIN starts with its state code.
        self.assertEqual(state_from_code('07AABCA1234A1Z5'), 'Delhi')
        for blank in ('', None):
            self.assertEqual(state_from_code(blank), '', blank)
        # An unrecognised code is kept, not dropped — see
        # StateCodesComeInSeveralSpellings for why.
        self.assertEqual(state_from_code('ZZ'), 'ZZ')

    def test_a_dump_row_lands_in_a_state(self):
        upload(a_workbook([a_row(**{'Cust.State Code': '07'})]))
        self.assertEqual(SalesRecord.objects.get().state, 'Delhi')

    def test_a_sheet_that_names_its_own_state_keeps_that_spelling(self):
        """Filling in a blank is help; overwriting what someone typed is not."""
        buf = a_workbook([{'Date': '2026-04-05', 'State': 'NCR Delhi', 'Amount': 500}],
                         headers=['Date', 'State', 'Amount'])
        upload(buf)
        self.assertEqual(SalesRecord.objects.get().state, 'NCR Delhi')


class StateCodesComeInSeveralSpellings(TestCase):
    """A dump carrying "AP" rather than "37" was leaving every row with no
    state, which emptied the state-wise view and left the AOP sheet's
    sub-regions as the only thing in it."""

    def test_the_two_letter_codes_resolve(self):
        from sales.ingest import state_from_code
        for code, name in (('AP', 'Andhra Pradesh'), ('MH', 'Maharashtra'),
                           ('DL', 'Delhi'), ('BR', 'Bihar'), ('TS', 'Telangana'),
                           ('ap', 'Andhra Pradesh')):
            self.assertEqual(state_from_code(code), name, code)

    def test_an_unknown_code_is_kept_rather_than_dropped(self):
        """Listing "ZZ" is imperfect. Silently omitting every row from that
        state is wrong, and the row had told us where it went."""
        from sales.ingest import state_from_code
        self.assertEqual(state_from_code('ZZ'), 'ZZ')

    def test_a_dump_with_letter_codes_lands_in_real_states(self):
        upload(a_workbook([a_row(**{'Cust.State Code': 'AP'}),
                           a_row(**{'Cust.State Code': 'MH', 'Invoice No.': 'INV-2'})]))
        self.assertEqual(
            sorted(SalesRecord.objects.values_list('state', flat=True)),
            ['Andhra Pradesh', 'Maharashtra'])

    def test_a_sub_region_is_a_territory_and_its_state_is_read_off_it(self):
        """AP-1 and AP-2 are two patches of Andhra Pradesh, and some rows
        carry a customer name there instead. Storing those as states put
        "AP-1" and "Amazon" in the state view beside Delhi."""
        from sales.ingest import state_from_subregion
        self.assertEqual(state_from_subregion('AP-1'), 'Andhra Pradesh')
        self.assertEqual(state_from_subregion('BR-2'), 'Bihar')
        self.assertEqual(state_from_subregion('Amazon'), '')
        self.assertEqual(state_from_subregion('Big Basket'), '')

    def test_the_aop_sheet_keeps_the_territory_and_names_the_state(self):
        upload(aop_workbook([aop_row(**{'Sub-Region': 'AP-1'})]))
        r = SalesRecord.objects.filter(source='plan').first()
        self.assertEqual(r.subzone, 'AP-1')
        self.assertEqual(r.state, 'Andhra Pradesh')

    def test_a_sub_region_naming_a_customer_leaves_the_state_empty(self):
        upload(aop_workbook([aop_row(**{'Sub-Region': 'Amazon'})]))
        r = SalesRecord.objects.filter(source='plan').first()
        self.assertEqual(r.subzone, 'Amazon')
        self.assertEqual(r.state, '')


class WhatTheOperatorIsToldAboutColumns(TestCase):

    def test_the_columns_we_skip_on_purpose_are_not_called_unrecognised(self):
        """Every upload of a normal dump carries all sixteen. Reporting them
        as failures sends somebody hunting for a mapping bug that does not
        exist — every single time."""
        d = upload(a_workbook([a_row()])).json()
        self.assertEqual(d['unrecognised_columns'], [])
        self.assertIn('Value in Lakhs', d['skipped_columns'])
        self.assertIn('IGST %', d['skipped_columns'])
        blob = ' '.join(d['warnings']).lower()
        self.assertNotIn('not recognised', blob)

    def test_a_column_nobody_has_seen_before_IS_reported(self):
        names = header_names() + ['Some New Field']
        rows = [{**a_row(), 'Some New Field': 'x'}]
        d = upload(a_workbook(rows, headers=names)).json()
        self.assertEqual(d['unrecognised_columns'], ['Some New Field'])
        self.assertIn('not recognised', ' '.join(d['warnings']).lower())

    def test_the_counts_come_back_for_the_screen_to_show(self):
        d = upload(a_workbook(
            [a_row(), a_row(**{'Cancelled': 'Yes'}),
             a_row(**{'Document Type': 'Credit Memo', 'Invoice No.': 'INV-3'})],
            headers=header_names() + ['Document Type'])).json()
        self.assertEqual(d['cancelled_rows'], 1)
        self.assertEqual(d['return_rows'], 1)
        self.assertEqual(d['rows'], 3)


class WhatTheMoneyColumnsMean(TestCase):

    def setUp(self):
        upload(a_workbook([a_row()]))
        self.rec = SalesRecord.objects.get()

    def test_taxable_amount_is_the_sales_value(self):
        self.assertEqual(float(self.rec.net_amount), 20000)
        self.assertEqual(float(self.rec.taxable_amount), 20000)

    def test_discount_is_summed_from_its_parts(self):
        """1000 invoice + 500 retail + 500 wholesale. Asking for a pre-totalled
        column instead would let it disagree with the parts beside it."""
        self.assertEqual(float(self.rec.discount), 2000)

    def test_tax_is_summed_from_the_gst_parts(self):
        self.assertEqual(float(self.rec.tax), 3600)
        self.assertEqual(float(self.rec.cgst_amount), 1800)
        self.assertEqual(float(self.rec.sgst_amount), 1800)

    def test_gross_is_reconstructed_when_the_dump_has_no_gross_column(self):
        self.assertEqual(float(self.rec.gross_amount), 22000)   # 20000 + 2000

    def test_value_in_lakhs_is_not_mistaken_for_the_sales_value(self):
        """It is Taxable Amount restated. Reading it as an amount would report
        a fifth of a rupee per rupee sold."""
        upload(a_workbook([a_row(**{'Value in Lakhs': 0.2})]))
        self.assertEqual(float(SalesRecord.objects.latest('id').net_amount), 20000)


class TheColumnsThatAreEasyToMisread(TestCase):

    def test_order_date_wins_over_invoice_and_posting_date(self):
        upload(a_workbook([a_row(**{'Order Date': '2026-04-05',
                                    'Invoice Date': '2026-05-09',
                                    'Posting Date': '2026-06-11'})]))
        rec = SalesRecord.objects.get()
        self.assertEqual(rec.order_date.isoformat(), '2026-04-05')
        self.assertEqual(rec.period.isoformat(), '2026-04-01')
        self.assertEqual(rec.invoice_date.isoformat(), '2026-05-09')
        self.assertEqual(rec.posting_date.isoformat(), '2026-06-11')

    def test_a_row_with_only_an_invoice_date_still_lands_in_a_month(self):
        """Invoice Date stopped being an alias for Order Date when it got its
        own column; it has to stay the fallback or the row is dropped."""
        row = a_row()
        row.pop('Order Date')
        upload(a_workbook([{**row, 'Invoice Date': '2026-07-02'}]))
        rec = SalesRecord.objects.get()
        self.assertEqual(rec.order_date.isoformat(), '2026-07-02')

    def test_shelf_life_dates_are_read_as_dates(self):
        upload(a_workbook([a_row(**{'PKD': '2026-03-01', 'Use By': '2028-02-29'})]))
        rec = SalesRecord.objects.get()
        self.assertEqual(rec.packed_on.isoformat(), '2026-03-01')
        self.assertEqual(rec.use_by.isoformat(), '2028-02-29')

    def test_an_absent_expiry_is_empty_rather_than_a_date(self):
        upload(a_workbook([a_row()]))
        self.assertIsNone(SalesRecord.objects.get().use_by)

    def test_the_customer_and_item_hierarchies_land_where_expected(self):
        upload(a_workbook([a_row(**{
            'Customer District': 'Central Delhi', 'Subzone': 'Delhi NCR',
            'Customer Posting Group(Business type)': 'Domestic Customer',
            'Customer Price Group(warehousetype)': 'Depot',
            'Item Sub Category (Sub Brand)': 'Natural Honey',
            'Variant': 'Squeeze', 'Prod. Group': 'Honey Group',
            'I-CODE': 'IC-4412', 'HSN Code': '04090000', 'MRP': 280,
            'Gross Weight In(Kg)': 62.5, 'Net Weight In(Kg)': 60,
            'V-REMARS': 'Festive dispatch',
        })]))
        r = SalesRecord.objects.get()
        self.assertEqual(r.customer_district, 'Central Delhi')
        self.assertEqual(r.subzone, 'Delhi NCR')
        self.assertEqual(r.business_type, 'Domestic Customer')
        self.assertEqual(r.warehouse_type, 'Depot')
        self.assertEqual(r.sub_category, 'Natural Honey')
        self.assertEqual(r.variant, 'Squeeze')
        self.assertEqual(r.prod_group, 'Honey Group')
        self.assertEqual(r.item_alt_code, 'IC-4412')
        self.assertEqual(r.hsn_code, '04090000')
        self.assertEqual(float(r.mrp), 280)
        self.assertEqual(float(r.net_weight_kg), 60)
        self.assertEqual(r.remarks, 'Festive dispatch')

    def test_a_plain_sales_sheet_still_uploads(self):
        """The generic three-column sheet predates the dump and must keep
        working — not everyone exports from the ERP."""
        buf = a_workbook([{'Date': '2026-04-05', 'State': 'Delhi', 'Amount': 5000}],
                         headers=['Date', 'State', 'Amount'])
        r = upload(buf)
        self.assertEqual(r.status_code, 200, r.content[:300])
        rec = SalesRecord.objects.get()
        self.assertEqual(float(rec.net_amount), 5000)
        self.assertEqual(rec.state, 'Delhi')
        self.assertFalse(rec.is_cancelled)


# ── file 2: AOP vs ACH ──────────────────────────────────────────────────────
AOP_DIMS = ['CHANEL TYPE', 'HEAD', 'GTR HEAD', 'REPORT.INCHARGE', 'REGION',
            'Sub-Region', 'Key', 'I-CODE', 'ITEM NAME', 'BRAND']
FY27 = ['Apr-26', 'May-26', 'Jun-26', 'Jul-26', 'Aug-26', 'Sep-26',
        'Oct-26', 'Nov-26', 'Dec-26', 'Jan-27', 'Feb-27', 'Mar-27']
FY26 = ['Apr-25', 'May-25', 'Jun-25', 'Jul-25', 'Aug-25', 'Sep-25',
        'Oct-25', 'Nov-25', 'Dec-25', 'Jan-26', 'Feb-26', 'Mar-26']
AOP_SUMMARY = ['YTD AOP', 'YTD ACH', 'LYTD ACH', "FY\'26-27 AOP", "FY\'26-27 ACH",
               'LMTD', 'MTD SEC SALES', 'NO OF SFO']
AOP_HEADERS = (AOP_DIMS + [f'{m} AOP' for m in FY27] + FY26 + FY27 + AOP_SUMMARY)


def aop_row(aop=None, cy=None, ly=None, **over):
    """One wide row. Defaults put a figure in April only, so a total is
    checkable without adding twelve numbers in your head."""
    d = {
        'CHANEL TYPE': 'General Trade', 'HEAD': 'Naagesh Mishra',
        'GTR HEAD': 'Anil Mehra', 'REPORT.INCHARGE': 'Vikas Gupta',
        'REGION': 'North', 'Sub-Region': 'Delhi', 'Key': 'x',
        'I-CODE': 'IC-4412', 'ITEM NAME': 'APIS Honey 500g', 'BRAND': 'APIS',
        'NO OF SFO': 4,
        # Totals that must NOT be added to the monthly columns.
        'YTD AOP': 999999, 'YTD ACH': 888888, 'LYTD ACH': 777777,
        "FY\'26-27 AOP": 999999, "FY\'26-27 ACH": 888888,
        'LMTD': 111, 'MTD SEC SALES': 222,
    }
    d['Apr-26 AOP'] = 100000 if aop is None else aop
    d['Apr-26'] = 90000 if cy is None else cy
    d['Apr-25'] = 80000 if ly is None else ly
    d.update(over)
    return d


def aop_workbook(rows):
    return a_workbook(rows, headers=AOP_HEADERS)


class ReadingTheWideSheet(TestCase):

    def test_the_sheet_is_recognised_without_being_told(self):
        self.assertTrue(AOP.looks_like_aop_sheet(AOP_HEADERS))
        self.assertFalse(AOP.looks_like_aop_sheet(header_names()))

    def test_a_month_header_is_read_from_its_text_not_its_position(self):
        self.assertEqual(AOP.parse_month_header('Apr-26'), (date(2026, 4, 1), False))
        self.assertEqual(AOP.parse_month_header('Apr-26 AOP'), (date(2026, 4, 1), True))
        self.assertEqual(AOP.parse_month_header('Mar-27'), (date(2027, 3, 1), False))
        # Two digits mean this century: a plan sheet has no 1926 in it.
        self.assertEqual(AOP.parse_month_header('Jan-27')[0].year, 2027)
        for not_a_month in ('BRAND', 'YTD ACH', 'I-CODE', 'NO OF SFO', ''):
            self.assertIsNone(AOP.parse_month_header(not_a_month), not_a_month)

    def test_one_wide_row_becomes_one_record_per_month_that_has_a_figure(self):
        upload(aop_workbook([aop_row()]))
        # April 2026 carries plan + actual; April 2025 carries last year's.
        self.assertEqual(SalesRecord.objects.count(), 2)
        months = sorted(r.period.isoformat() for r in SalesRecord.objects.all())
        self.assertEqual(months, ['2025-04-01', '2026-04-01'])

    def test_an_empty_month_is_skipped_rather_than_stored_as_zero(self):
        """A zero claims nothing sold. A blank cell in a part-finished year
        is not making that claim."""
        upload(aop_workbook([aop_row()]))
        self.assertEqual(SalesRecord.objects.filter(period__gt=date(2026, 4, 1)).count(), 0)

    def test_plan_and_achievement_land_on_the_same_month(self):
        upload(aop_workbook([aop_row()]))
        cy = SalesRecord.objects.get(period=date(2026, 4, 1))
        self.assertEqual(float(cy.target_amount), 100000)
        self.assertEqual(float(cy.measured_amount), 90000)

    def test_last_year_is_an_actual_with_no_plan_against_it(self):
        upload(aop_workbook([aop_row()]))
        ly = SalesRecord.objects.get(period=date(2025, 4, 1))
        self.assertEqual(float(ly.target_amount), 0)
        self.assertEqual(float(ly.measured_amount), 80000)

    def test_the_business_mapping_is_applied(self):
        """REGION is a zone here, Sub-Region is a state, GTR HEAD is the RSM
        and REPORT.INCHARGE is the ASM."""
        upload(aop_workbook([aop_row()]))
        r = SalesRecord.objects.filter(period=date(2026, 4, 1)).first()
        self.assertEqual(r.zone, 'North')
        self.assertEqual(r.state, 'Delhi')
        self.assertEqual(r.rsm, 'Anil Mehra')
        self.assertEqual(r.asm, 'Vikas Gupta')
        self.assertEqual(r.sales_head, 'Naagesh Mishra')
        self.assertEqual(r.channel, 'General Trade')
        self.assertEqual(r.brand, 'APIS')
        self.assertEqual(r.item_alt_code, 'IC-4412')
        self.assertEqual(r.sfo_count, 4)

    def test_the_year_total_columns_are_never_added_to_the_months(self):
        """YTD ACH is the monthly columns already added up. Storing it beside
        them would report the year roughly twice."""
        upload(aop_workbook([aop_row()]))
        total = sum(float(r.measured_amount) for r in SalesRecord.objects.all())
        self.assertEqual(total, 170000)         # 90,000 + 80,000, nothing else
        self.assertFalse(SalesRecord.objects.filter(net_amount=888888).exists())

    def test_the_operator_is_told_the_totals_were_skipped(self):
        d = upload(aop_workbook([aop_row()])).json()
        self.assertEqual(d['file_kind'], 'aop')
        blob = ' '.join(d['notes']).lower()
        self.assertIn('skipped on purpose', blob)
        self.assertIn('ytd ach', blob)


class WhichFileTheSalesFigureComesFrom(TestCase):
    """Both files describe the same money. Summing both would double it."""

    def test_the_aop_sheet_alone_still_shows_sales(self):
        upload(aop_workbook([aop_row()]))
        cy = SalesRecord.objects.get(period=date(2026, 4, 1))
        self.assertEqual(float(cy.net_amount), 90000)

    def test_the_review_sheet_owns_a_month_both_files_describe(self):
        """The sheet is the record the business reads, so it is the record
        the dashboard shows.

        It used to be a contest the dump could win, and then the year to date
        was the sheet's YTD ACH minus the sheet's September plus the dump's --
        a figure that appears in neither file and reconciles against nothing.
        """
        upload(aop_workbook([aop_row(cy=20000)]))
        upload(a_workbook([a_row()]))           # the Pre-Sales Dump, April 2026
        cy = SalesRecord.objects.get(source='plan', period=date(2026, 4, 1))
        self.assertEqual(float(cy.net_amount), 20000)
        self.assertEqual(float(cy.target_amount), 100000)
        # Both rows keep their own figure -- the dump's is what answers a date
        # range and feeds the customer and SKU panels. What must not happen is
        # the two being added together, and that is a property of the answer,
        # not of the rows.
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(float(d['revenue']), 20000,
                         'both files were counted for the same month')

    def test_history_the_dump_never_covered_is_kept(self):
        """The regression this whole per-month election exists to prevent.

        The real Pre-Sales Dump is a live ERP extract: it holds the month it
        was taken in and nothing else. The review sheet beside it holds two
        years. Letting the dump win outright — which is what it used to do —
        threw eighteen months of the company's history away to keep one month
        of line detail, and left a trend chart that began below zero.
        """
        upload(aop_workbook([aop_row()]))       # April 2025 AND April 2026
        upload(a_workbook([a_row()]))           # dump covers April 2026 only
        ly = SalesRecord.objects.get(source='plan', period=date(2025, 4, 1))
        self.assertEqual(float(ly.net_amount), 80000,
                         'April 2025 was erased by a dump that never covered it')

    def test_a_month_the_dump_only_clipped_stays_with_the_review_sheet(self):
        """A handful of stragglers is not a month's sales.

        The extract catches a few invoices either side of the month it was
        taken in. Treating those as the whole month reported one real file's
        August at Rs 2.5 crore when the review sheet had it at Rs 23.6 crore.
        """
        upload(aop_workbook([aop_row(cy=100000)]))
        upload(a_workbook([a_row(**{'Taxable Amount': 2000})]))
        cy = SalesRecord.objects.get(source='plan', period=date(2026, 4, 1))
        self.assertEqual(float(cy.net_amount), 100000,
                         'a 2% clipping of the month was allowed to stand in for it')

    def test_a_month_of_nothing_but_credit_notes_is_not_a_covered_month(self):
        """Seven of the nine months in the real dump were credit notes only.

        Their totals are negative, so a dashboard that took them as the
        month's sales drew the year opening below the axis.
        """
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row(**{'Taxable Amount': -5000})]))
        cy = SalesRecord.objects.get(source='plan', period=date(2026, 4, 1))
        self.assertEqual(float(cy.net_amount), 90000)
        rev = float(Client().get('/api/sales/overview/').json().get('revenue') or 0)
        self.assertGreater(rev, 0, 'the month came out negative')

    def test_the_order_the_files_arrive_in_does_not_matter(self):
        upload(a_workbook([a_row()]))           # dump first this time
        upload(aop_workbook([aop_row(cy=20000)]))
        d = Client().get('/api/sales/overview/?span=all').json()
        # 20,000 for April 2026 from the review sheet, 80,000 for April 2025.
        # The dump's April 2026 is the same money and is not added again.
        self.assertEqual(float(d['revenue']), 100000)

    def test_removing_the_invoice_data_hands_the_figures_back(self):
        upload(aop_workbook([aop_row()]))
        r = upload(a_workbook([a_row()])).json()
        Client().delete(f"/api/sales/uploads/?id={r['upload_id']}")
        cy = SalesRecord.objects.get(source='plan', period=date(2026, 4, 1))
        self.assertEqual(float(cy.net_amount), 90000,
                         'the dashboard would show zero with a good plan file loaded')

    def test_a_month_in_both_files_is_counted_once(self):
        # April 2026 is in both, at 20,000 each. April 2025 is in the review
        # sheet alone, at 80,000. The right answer counts April 2026 once and
        # keeps April 2025 — it does not drop the history to prove it can
        # avoid double counting.
        upload(aop_workbook([aop_row(cy=20000)]))
        upload(a_workbook([a_row()]))           # 20,000 taxable, April 2026
        # span=all because this is a claim about the whole file, and the
        # dashboard now defaults to the current financial year.
        d = Client().get('/api/sales/overview/?span=all').json()
        rev = float(d.get('total_revenue') or d.get('revenue') or 0)
        self.assertEqual(rev, 100000, f'not 20,000 (April 26) + 80,000 (April 25): {rev}')

    def test_no_month_is_counted_from_both_files_at_once(self):
        """The invariant underneath every total on the dashboard.

        It used to be a property of the rows -- the losing source was zeroed
        in the table. That could not express "the dump answers a date range",
        because a zeroed row answers nothing, so the rule moved into the
        query. Which means this has to be checked on the answer.
        """
        upload(aop_workbook([aop_row(cy=20000)]))
        upload(a_workbook([a_row()]))
        # The sheet has April 2025 at 80,000 and April 2026 at 20,000. The
        # dump has the same April 2026 at 20,000. Counted once: 100,000.
        d = Client().get('/api/sales/overview/?span=all').json()
        self.assertEqual(float(d['revenue']), 100000,
                         'April 2026 is being counted from both files')
        # And per month, so a single wrong month cannot hide inside a right
        # total.
        rows = {r['period']: float(r['revenue'] or 0)
                for r in Client().get('/api/sales/trend/').json()['results']}
        self.assertEqual(rows['2026-04-01'], 20000)
        self.assertEqual(rows['2025-04-01'], 80000)


class TheSheetChecksItself(TestCase):
    """The review sheet states its own totals. Comparing them against the
    months actually read turns every upload into a test of itself."""

    def test_it_says_so_when_the_months_add_up_to_the_stated_totals(self):
        # aop_row's defaults: Apr-26 plan 100,000, Apr-26 actual 90,000,
        # Apr-25 actual 80,000 -- so the sheet's own totals are those sums.
        d = upload(aop_workbook([aop_row(**{
            'YTD AOP': 100000, 'YTD ACH': 90000, 'LYTD ACH': 80000,
            "FY'26-27 AOP": 100000, "FY'26-27 ACH": 90000,
        })])).json()
        blob = ' '.join(d['notes'])
        self.assertIn('agree to the rupee', blob, blob)
        self.assertNotIn('Does not match', blob)

    def test_it_says_so_when_they_do_not(self):
        """A month misread, dropped or double-counted stops these agreeing."""
        d = upload(aop_workbook([aop_row(**{
            'YTD AOP': 999999, 'YTD ACH': 90000, 'LYTD ACH': 80000,
            "FY'26-27 AOP": 999999, "FY'26-27 ACH": 90000,
        })])).json()
        blob = ' '.join(d['notes'])
        self.assertIn('Does not match', blob, blob)
        self.assertIn('999,999', blob)

    def test_the_totals_are_still_never_stored_as_sales(self):
        upload(aop_workbook([aop_row(**{
            'YTD AOP': 100000, 'YTD ACH': 90000, 'LYTD ACH': 80000,
            "FY'26-27 AOP": 100000, "FY'26-27 ACH": 90000,
        })]))
        total = sum(float(r.measured_amount) for r in SalesRecord.objects.all())
        self.assertEqual(total, 170000, 'a summary column was loaded as data')

    def test_secondary_sales_are_reported_but_not_added_to_primary(self):
        d = upload(aop_workbook([aop_row(**{'MTD SEC SALES': 45000})])).json()
        blob = ' '.join(d['notes'])
        self.assertIn('SEC SALES', blob)
        rev = float(Client().get('/api/sales/overview/?span=all').json()['revenue'])
        self.assertEqual(rev, 170000, 'secondary sales were added to primary')


class GroupingByWhatTheLineActuallyIs(TestCase):

    def test_v_remars_can_be_broken_down(self):
        """SALES and the returns that belong in the figure -- SR and GOOD SR,
        stock that came back -- are worth seeing split out.

        SCHEME CN is not among them: the business excludes it from sales, so
        it is not a revenue bucket at all. See TheLinesTheBusinessExcludes.
        """
        upload(a_workbook([
            a_row(**{'V-REMARS': 'SALES'}),
            a_row(**{'V-REMARS': 'SR', 'Taxable Amount': -3000}),
            a_row(**{'V-REMARS': 'SCHEME CN', 'Taxable Amount': -1000}),
        ]))
        rows = {r['name']: r for r in
                Client().get('/api/sales/breakdown/?dim=transaction_type').json()['results']}
        self.assertEqual(float(rows['SALES']['revenue']), 20000)
        self.assertEqual(float(rows['SR']['revenue']), -3000)
        self.assertNotIn('SCHEME CN', rows,
                         'an excluded line was offered as a revenue bucket')


class TheYearRunsAprilToMarch(TestCase):
    """Every one of the business's own files says so: the plan columns run
    Apr-26 to Mar-27 and the summaries are headed FY'26-27."""

    def test_years_are_financial_not_calendar(self):
        upload(aop_workbook([aop_row(cy=20000, ly=10000)]))
        y = Client().get('/api/sales/yoy/').json()
        self.assertIn('FY26-27', y['years'])
        self.assertIn('FY25-26', y['years'])

    def test_the_chart_starts_at_april(self):
        upload(aop_workbook([aop_row()]))
        labels = [r['label'] for r in Client().get('/api/sales/yoy/').json()['results']]
        self.assertEqual(labels[0], 'Apr')
        self.assertEqual(labels[-1], 'Mar')

    def test_a_month_still_to_come_is_not_a_100_percent_collapse(self):
        """October to March of the current year each read as -100% against
        last year: a total collapse, in months that have not happened."""
        upload(aop_workbook([aop_row(cy=20000, ly=10000,
                                     **{'Oct-26 AOP': 50000, 'Oct-25': 40000})]))
        rows = {r['label']: r for r in Client().get('/api/sales/yoy/').json()['results']}
        self.assertIsNone(rows['Oct']['FY26-27'],
                          'a month that has not happened was reported as zero')
        self.assertEqual(float(rows['Oct']['FY25-26']), 40000)

    def test_growth_compares_only_the_months_both_years_have(self):
        upload(aop_workbook([aop_row(cy=20000, ly=10000,
                                     **{'Oct-26 AOP': 50000, 'Oct-25': 40000})]))
        g = Client().get('/api/sales/yoy/').json()['growth']['FY26-27']
        self.assertEqual(g['months'], 1, 'compared a part year against a whole one')
        self.assertEqual(g['pct'], 100.0)          # 20,000 against 10,000


class ComparedWithTheSameMonthsLastYear(TestCase):

    def test_april_is_compared_with_april(self):
        upload(aop_workbook([aop_row(cy=20000, ly=10000)]))
        ly = Client().get('/api/sales/overview/').json()['vs_last_year']
        self.assertEqual(ly['this_year'], 20000)
        self.assertEqual(ly['last_year'], 10000)
        self.assertEqual(ly['growth_pct'], 100.0)
        self.assertEqual(ly['months'], 1)

    def test_nothing_is_claimed_with_only_one_year_loaded(self):
        upload(a_workbook([a_row()]))
        self.assertIsNone(Client().get('/api/sales/overview/').json()['vs_last_year'])


class FreeSamplesAreNotAnExportFault(TestCase):

    def test_a_zero_value_line_the_business_excluded_raises_no_warning(self):
        """All four in the first real file were samples and packaging given
        away, already marked NOT A PART OF SALES. Telling the operator to
        check their amount column sends them after a fault that is not there."""
        d = upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'NOT A PART OF SALES', 'Taxable Amount': 0,
                     'Total Line Amount GST & TCS': 0}),
        ])).json()
        blob = ' '.join(d.get('warnings', []))
        self.assertNotIn('no sales value', blob, f'false alarm: {blob}')

    def test_a_genuinely_missing_value_still_warns(self):
        d = upload(a_workbook([
            a_row(),
            a_row(**{'Taxable Amount': 0, 'Total Line Amount GST & TCS': 0}),
        ])).json()
        self.assertIn('no sales value', ' '.join(d.get('warnings', [])))


class TheDashboardOnlyOffersWhatTheFileCanAnswer(TestCase):

    def test_absent_columns_are_reported_so_their_panels_can_be_hidden(self):
        upload(a_workbook([a_row()]))
        f = Client().get('/api/sales/filters/').json()
        self.assertIn('area', f['absent_dimensions'])
        self.assertIn('zone', f['available_dimensions'])

    def test_a_dimension_with_data_is_never_called_absent(self):
        upload(a_workbook([a_row()]))
        f = Client().get('/api/sales/filters/').json()
        self.assertEqual(set(f['available_dimensions']) & set(f['absent_dimensions']),
                         set())


class AchievementIsComparedLikeForLike(TestCase):
    """Revenue and plan cover different stretches of time. Dividing the two
    totals is what put 508,382%, and then 86%, on this dashboard."""

    def _two_years(self):
        # Plan for Apr and May 2026 only. Actuals for Apr 2025 (last year,
        # no plan) and Apr 2026 (this year, planned).
        upload(aop_workbook([aop_row(aop=100000, cy=60000, ly=500000,
                                     **{'May-26 AOP': 100000})]))

    def test_last_years_sales_do_not_count_towards_this_years_plan(self):
        self._two_years()
        d = Client().get('/api/sales/overview/').json()
        b = d['achievement_basis']
        self.assertEqual(b['months'], 1, f'compared {b["months"]} months, not just April 2026')
        self.assertEqual(b['revenue'], 60000)
        self.assertEqual(b['target'], 100000)
        self.assertEqual(d['achievement_pct'], 60.0)

    def test_months_still_to_come_do_not_count_against_it(self):
        """May is planned and has not happened. Counting it makes a business
        on plan look like one that is missing."""
        self._two_years()
        b = Client().get('/api/sales/overview/').json()['achievement_basis']
        self.assertNotIn('2026-05', str(b['to']), 'an unstarted month was compared')

    def test_the_headline_is_this_year_not_both_years(self):
        """Revenue reads the current financial year. Last year is loaded so
        growth can be shown, not so it can be added to this year's sales."""
        self._two_years()
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(float(d['revenue']), 60000,
                         "last year's 500,000 was added to this year")
        self.assertEqual(float(d['target']), 200000)       # Apr + May plan

    def test_the_whole_file_is_still_reachable(self):
        self._two_years()
        d = Client().get('/api/sales/overview/?span=all').json()
        self.assertEqual(float(d['revenue']), 560000)      # 500k last yr + 60k this

    def test_the_basis_says_which_months_were_compared(self):
        self._two_years()
        b = Client().get('/api/sales/overview/').json()['achievement_basis']
        self.assertEqual(b['from'], '2026-04-01')
        self.assertEqual(b['to'], '2026-04-01')


class PacingRunsOnThePlansOwnPeriod(TestCase):

    def test_the_year_is_not_already_over_because_a_plan_reaches_march(self):
        """Measured across everything loaded, last year's sales made the
        financial year look 95.9% elapsed in September."""
        upload(aop_workbook([aop_row(aop=100000, cy=60000, ly=500000,
                                     **{'Mar-27 AOP': 100000})]))
        p = Client().get('/api/sales/pacing/').json()
        self.assertTrue(p['has_target'])
        self.assertLess(p['elapsed_pct'], 60,
                        f"the year cannot be {p['elapsed_pct']}% gone in April")
        self.assertGreater(p['days_remaining'], 0)


class MonthsTheBusinessHasNotReachedYet(TestCase):
    """A full year of plan is loaded in April. Nine of those months have not
    happened, and zero is not what they sold."""

    def _loaded(self):
        # Plan for April AND May; actuals for April only.
        upload(aop_workbook([aop_row(cy=20000, **{'May-26 AOP': 50000})]))
        upload(a_workbook([a_row()]))

    def test_a_month_with_a_plan_and_no_actuals_reports_no_revenue(self):
        self._loaded()
        pts = {p['label']: p for p in Client().get('/api/sales/trend/').json()['results']}
        self.assertIsNone(pts['May 2026']['revenue'],
                          'a month that has not happened is being reported as zero sales')
        self.assertEqual(float(pts['May 2026']['target']), 50000,
                         'the plan for it should still show')
        self.assertTrue(pts['May 2026']['pending'])

    def test_it_is_not_named_the_worst_month_of_the_year(self):
        self._loaded()
        d = Client().get('/api/sales/trend/').json()
        self.assertNotEqual(d['worst_month']['label'], 'May 2026')

    def test_it_is_not_averaged_into_the_seasonal_index(self):
        """Averaging a month that has run with one that has not halves it."""
        self._loaded()
        by_month = {m['label']: m for m in
                    Client().get('/api/sales/seasonality/').json()['results']}
        self.assertEqual(by_month['May']['samples'], 0,
                         'a month that has not happened was sampled as a zero')
        # April really did run, twice — last year and this.
        self.assertEqual(by_month['Apr']['samples'], 2)

    def test_a_month_that_genuinely_sold_nothing_still_reads_as_zero(self):
        """Only a month with a plan and no actuals is unknown. A month with
        neither is a real, measured zero."""
        upload(a_workbook([a_row()]))
        pts = Client().get('/api/sales/trend/').json()['results']
        self.assertFalse(any(p['pending'] for p in pts))


class SayingHowMuchOfTheBusinessAChartSpeaksFor(TestCase):

    def test_a_breakdown_reports_what_it_cannot_attribute(self):
        upload(a_workbook([
            a_row(**{'Taxable Amount': 30000}),
            a_row(**{'Cust.State Code': '', 'Taxable Amount': 10000}),
        ]))
        d = Client().get('/api/sales/breakdown/?dim=state').json()
        self.assertEqual(d['coverage']['attributed'], 30000)
        self.assertEqual(d['coverage']['unattributed'], 10000)
        self.assertEqual(d['coverage']['pct'], 75.0)

    def test_a_complete_dimension_reports_full_coverage(self):
        upload(a_workbook([a_row()]))
        d = Client().get('/api/sales/breakdown/?dim=zone').json()
        self.assertEqual(d['coverage']['pct'], 100.0)


class TheVRemarsColumn(TestCase):
    """The dump has no Document Type column. V-REMARS is the only place the
    business says what a line actually is."""

    def test_a_sales_return_is_recognised_as_a_return(self):
        upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'SR', 'Taxable Amount': -5000}),
            a_row(**{'V-REMARS': 'GOOD SR', 'Taxable Amount': -2000}),
            a_row(**{'V-REMARS': 'SCHEME CN', 'Taxable Amount': -1000}),
        ]))
        # SR and GOOD SR only. SCHEME CN is excluded from sales rather than
        # netted off as a return, so a line is never filed as both.
        self.assertEqual(SalesRecord.objects.filter(is_return=True).count(), 2,
                         'returns came through as ordinary negative sales')
        self.assertEqual(SalesRecord.objects.filter(is_not_sales=True).count(), 1,
                         'SCHEME CN was not excluded from sales')

    def test_returns_still_reduce_sales(self):
        upload(a_workbook([a_row(),
                           a_row(**{'V-REMARS': 'SR', 'Taxable Amount': -5000})]))
        rev = float(Client().get('/api/sales/overview/').json()['revenue'])
        self.assertEqual(rev, 15000, f'20,000 less a 5,000 return is 15,000, not {rev}')

    def test_not_a_part_of_sales_is_kept_but_never_counted(self):
        upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'NOT A PART OF SALES', 'Taxable Amount': 90000}),
        ]))
        self.assertEqual(SalesRecord.objects.count(), 2, 'the row must survive')
        self.assertEqual(SalesRecord.objects.filter(is_not_sales=True).count(), 1)
        rev = float(Client().get('/api/sales/overview/').json()['revenue'])
        self.assertEqual(rev, 20000, f'freight was counted as sales: {rev}')

    def test_it_is_flagged_from_the_remark_not_the_zone(self):
        """Matching on Zone caught 176 of 239 flagged lines in the real file
        and left Rs 27.5 lakh of freight in revenue."""
        upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'NOT A PART OF SALES', 'Zone': 'EXPORT',
                     'Taxable Amount': 90000}),
        ]))
        rev = float(Client().get('/api/sales/overview/').json()['revenue'])
        self.assertEqual(rev, 20000, 'only rows that also say so in Zone were excluded')

    def test_the_operator_is_told_what_was_left_out(self):
        d = upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'NOT A PART OF SALES', 'Taxable Amount': 90000}),
        ])).json()
        blob = ' '.join(d['notes'])
        self.assertIn('NOT A PART OF SALES', blob)
        self.assertIn('90,000', blob, f'the value reported must match the rows: {blob}')


class AnUnfilledPostIsNotAPerson(TestCase):

    def test_a_vacant_territory_is_marked_as_one(self):
        upload(a_workbook([
            a_row(**{'ASM Name': 'VACANT-TRI', 'Taxable Amount': 30000}),
            a_row(**{'ASM Name': 'Rahul', 'Taxable Amount': 10000}),
        ]))
        rows = {r['name']: r for r in
                Client().get('/api/sales/breakdown/?dim=asm').json()['results']}
        self.assertTrue(rows['Vacant-Tri']['vacant'])
        self.assertFalse(rows['Rahul']['vacant'])

    def test_its_sales_still_count(self):
        """The territory is selling even with nobody in the post."""
        upload(a_workbook([a_row(**{'ASM Name': 'VACANT-HP'})]))
        self.assertEqual(float(Client().get('/api/sales/overview/').json()['revenue']),
                         20000)


class ExcelErrorsAreNotData(TestCase):

    def test_a_broken_lookup_does_not_become_an_item_code(self):
        upload(a_workbook([a_row(**{'I-CODE': '#N/A', 'Customer District': '#REF!'})]))
        r = SalesRecord.objects.first()
        self.assertEqual(r.item_alt_code, '')
        self.assertEqual(r.customer_district, '')

    def test_it_is_not_offered_as_something_to_group_by(self):
        upload(a_workbook([a_row(**{'Customer District': '#N/A'}), a_row()]))
        names = [x['name'] for x in
                 Client().get('/api/sales/breakdown/?dim=district').json()['results']]
        self.assertNotIn('#N/A', names)


class ColumnsThatWereReadAndThenIgnored(TestCase):

    def test_customer_type_can_be_grouped_and_filtered(self):
        """Export / Modern Trade / General Trade is the cleanest split of the
        business in either file, and nothing could reach it."""
        upload(a_workbook([
            a_row(**{'Customer Type': 'Export', 'Taxable Amount': 30000}),
            a_row(**{'Customer Type': 'Modern Trade', 'Taxable Amount': 10000}),
        ]))
        d = Client().get('/api/sales/breakdown/?dim=customer_type').json()
        self.assertEqual(len(d['results']), 2)
        one = Client().get('/api/sales/overview/?customer_type=Export').json()
        self.assertEqual(float(one['revenue']), 30000)

    def test_the_ledger_account_explains_non_product_money(self):
        upload(a_workbook([a_row(**{'Type': 'G/L Account',
                                    'GL Account Name': 'Freight Others'})]))
        r = SalesRecord.objects.first()
        self.assertEqual(r.gl_account_name, 'Freight Others')
        names = [x['name'] for x in
                 Client().get('/api/sales/breakdown/?dim=gl_account').json()['results']]
        self.assertIn('Freight Others', names)


class QuantityIsNotOneUnit(TestCase):
    """Cases, pieces, kilograms and metres all land in the same column."""

    def test_the_split_by_unit_is_reported(self):
        upload(a_workbook([
            a_row(**{'Unit Of Measure Code': 'CASE', 'Quantity': 100}),
            a_row(**{'Unit Of Measure Code': 'NOS', 'Quantity': 500}),
        ]))
        d = Client().get('/api/sales/overview/').json()
        self.assertTrue(d['mixed_units'])
        units = {u['unit']: u['quantity'] for u in d['quantity_by_unit']}
        self.assertEqual(units['CASE'], 100)
        self.assertEqual(units['NOS'], 500)

    def test_a_single_unit_is_not_flagged_as_mixed(self):
        upload(a_workbook([a_row(**{'Unit Of Measure Code': 'CASE'})]))
        self.assertFalse(Client().get('/api/sales/overview/').json()['mixed_units'])


class OnePersonOneRow(TestCase):
    """The review sheet shouts names, the ERP dump does not."""

    def test_the_same_person_spelled_two_ways_is_one_person(self):
        upload(aop_workbook([aop_row(**{'GTR HEAD': 'VAIBHAV MISHRA'})]))
        upload(a_workbook([a_row(**{'RSM Name': 'Vaibhav Mishra'})]))
        names = [r['name'] for r in
                 Client().get('/api/sales/breakdown/?dim=rsm&limit=50').json()['results']]
        self.assertEqual(names.count('Vaibhav Mishra'), 1,
                         f'one person is on the leaderboard twice: {names}')

    def test_their_sales_are_added_together_not_split(self):
        upload(a_workbook([
            a_row(**{'RSM Name': 'VAIBHAV MISHRA', 'Taxable Amount': 30000}),
            a_row(**{'RSM Name': 'Vaibhav Mishra', 'Taxable Amount': 20000}),
        ]))
        rows = Client().get('/api/sales/breakdown/?dim=rsm&limit=50').json()['results']
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(float(rows[0]['revenue']), 50000)

    def test_it_does_not_touch_what_was_sold(self):
        """An item code is case-sensitive; a product name is the brand's to
        spell. Only who somebody is gets restyled."""
        upload(a_workbook([a_row(**{'Item Code': 'APS-HNY-500',
                                    'Item Name': 'APIS Honey 500g'})]))
        r = SalesRecord.objects.first()
        self.assertEqual(r.sku, 'APS-HNY-500')
        self.assertEqual(r.product_name, 'APIS Honey 500g')


class RowsTheBusinessSaysAreNotSales(TestCase):
    """The dump's Zone column carries its own opt-out, and it is not subtle."""

    def test_a_not_a_part_of_sales_row_is_kept_but_never_counted(self):
        upload(a_workbook([
            a_row(),
            a_row(**{'Zone': 'NOT A PART OF SALES', 'Taxable Amount': 500000}),
        ]))
        # Still in the table, so the file reconciles line for line with the ERP.
        self.assertEqual(SalesRecord.objects.count(), 2)
        rev = float(Client().get('/api/sales/overview/').json().get('revenue') or 0)
        self.assertEqual(rev, 20000, f'counted a row marked not-a-sale: {rev}')

    def test_it_is_not_offered_as_a_zone_to_look_at(self):
        upload(a_workbook([
            a_row(),
            a_row(**{'Zone': 'NOT A PART OF SALES', 'Taxable Amount': 500000}),
        ]))
        d = Client().get('/api/sales/breakdown/?dim=zone&limit=50').json()
        names = [r['name'] for r in d['results']]
        self.assertNotIn('NOT A PART OF SALES', names)

    def test_the_uploads_list_and_the_dashboard_agree(self):
        """Two places showing one number. They are counted the same way or
        the operator has to decide which of them is lying."""
        r = upload(a_workbook([
            a_row(),
            a_row(**{'Zone': 'NOT A PART OF SALES', 'Taxable Amount': 500000}),
            # Flagged in V-REMARS only, which is where the business actually
            # records it -- the case the zone-only check missed.
            a_row(**{'V-REMARS': 'NOT A PART OF SALES', 'Taxable Amount': 300000}),
            a_row(**{'Cancelled': 'Yes', 'Taxable Amount': 700000}),
        ])).json()
        listed = float(Client().get('/api/sales/uploads/').json()['results'][0]
                       ['revenue'])
        shown = float(Client().get('/api/sales/overview/').json().get('revenue') or 0)
        self.assertEqual(listed, shown, f'list says {listed}, dashboard says {shown}')


class TellingTheOperatorWhichFileAMonthCameFrom(TestCase):

    def test_the_upload_says_which_months_came_from_where(self):
        upload(aop_workbook([aop_row(cy=20000)]))
        r = upload(a_workbook([a_row()])).json()
        blob = ' '.join(r.get('notes', [])).lower()
        self.assertIn('review sheet', blob)
        self.assertIn('invoice dump', blob)
        self.assertIn('apr 2025', blob)

    def test_nothing_is_claimed_when_only_one_file_is_loaded(self):
        r = upload(a_workbook([a_row()])).json()
        blob = ' '.join(r.get('notes', [])).lower()
        self.assertNotIn('never both at once', blob)


class TheUnitTheReviewSheetIsWrittenIn(TestCase):
    """The review sheet is in lakhs; the dump beside it is in rupees."""

    def test_a_sheet_in_lakhs_is_brought_up_to_rupees(self):
        # A plan cell reading 4.5 is Rs 4,50,000. Read as rupees it put a
        # Rs 319 crore annual plan on the dashboard as Rs 31,927, and every
        # achievement figure came out at 508,382%.
        upload(aop_workbook([aop_row(aop=4.5, cy=3.2, ly=2.8)]))
        cy = SalesRecord.objects.get(period=date(2026, 4, 1))
        self.assertEqual(float(cy.target_amount), 450000)
        self.assertEqual(float(cy.measured_amount), 320000)

    def test_a_sheet_already_in_rupees_is_left_alone(self):
        upload(aop_workbook([aop_row()]))       # 100000 / 90000 / 80000
        cy = SalesRecord.objects.get(period=date(2026, 4, 1))
        self.assertEqual(float(cy.target_amount), 100000)

    def test_the_operator_is_told_the_figures_were_rescaled(self):
        d = upload(aop_workbook([aop_row(aop=4.5, cy=3.2, ly=2.8)])).json()
        blob = ' '.join(d['notes']).lower()
        self.assertIn('lakh', blob)

    def test_the_unit_is_judged_on_the_median_not_one_stray_cell(self):
        rows = [aop_row(aop=4.5, cy=3.2, ly=2.8) for _ in range(5)]
        rows.append(aop_row(aop=999999999, cy=1, ly=1))
        upload(aop_workbook(rows))
        r = SalesRecord.objects.filter(period=date(2026, 4, 1),
                                       target_amount=450000)
        self.assertTrue(r.exists(), 'one huge cell decided the unit for the sheet')


class OneWorkbookTwoSheets(TestCase):
    """How the files actually arrive: both tabs in one .xlsx.

    Reading only wb.active meant whichever tab was selected when the file was
    last saved decided what got imported. The other was dropped silently, and
    if the selected tab was the AOP sheet the dump parser rejected the whole
    upload with "No date column found" — an accurate complaint about a sheet
    nobody meant it to read.
    """

    def _two_sheets(self, active=0, month_headers_as_dates=False):
        wb = openpyxl.Workbook()
        d = wb.active
        d.title = 'PRI SALES DUMP'
        names = header_names()
        d.append(names)
        r = a_row()
        d.append([r.get(n, '') for n in names])

        a = wb.create_sheet('YTD,AOP vs.ACH')
        hdr = []
        for h in AOP_HEADERS:
            if month_headers_as_dates and h in FY26 + FY27:
                mon, yr = h.split('-')
                hdr.append(datetime(2000 + int(yr), [
                    'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul',
                    'Aug', 'Sep', 'Oct', 'Nov', 'Dec'].index(mon) + 1, 1))
            else:
                hdr.append(h)
        a.append(hdr)
        ar = aop_row()
        a.append([ar.get(h, '') for h in AOP_HEADERS])

        wb.active = active
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'APIS Sales.xlsx'
        return buf

    def test_both_sheets_load_from_one_workbook(self):
        d = upload(self._two_sheets()).json()
        self.assertEqual(SalesRecord.objects.filter(source='invoice').count(), 1)
        self.assertEqual(SalesRecord.objects.filter(source='plan').count(), 2)
        self.assertEqual(set(d['sheets']), {'PRI SALES DUMP', 'YTD,AOP vs.ACH'})
        self.assertEqual(d['sheets']['YTD,AOP vs.ACH']['kind'], 'aop')

    def test_it_does_not_matter_which_tab_was_open_when_the_file_was_saved(self):
        """The bug as reported: saving with the YTD tab selected made the
        whole upload fail."""
        for active in (0, 1):
            SalesRecord.objects.all().delete()
            SalesUpload.objects.all().delete()
            r = upload(self._two_sheets(active=active))
            self.assertEqual(r.status_code, 200,
                             f'active tab {active}: {r.content[:200]}')
            self.assertEqual(SalesRecord.objects.count(), 3)

    def test_month_headers_excel_turned_into_dates_still_carry_their_actuals(self):
        """Typing "Apr-26" into a General cell makes Excel store a date and
        only display it as Apr-26. The plain month columns — which are the
        actuals — are exactly the ones this happens to, while the " AOP"
        columns stay text because of the suffix. Reading only the text form
        kept the plan and silently lost every achievement figure."""
        upload(self._two_sheets(month_headers_as_dates=True))
        plan = SalesRecord.objects.filter(source='plan')
        self.assertEqual(plan.count(), 2, 'the actuals columns were dropped')
        cy = plan.get(period=date(2026, 4, 1))
        self.assertEqual(float(cy.target_amount), 100000)
        self.assertEqual(float(cy.measured_amount), 90000)

    def test_every_message_says_which_sheet_it_came_from(self):
        """With two sheets in one file, an unattributed message leaves you
        guessing which half of the workbook it is about."""
        d = upload(self._two_sheets()).json()
        every = d['notes'] + d['warnings']
        self.assertTrue(every)
        for m in every:
            # 'Both sheets:' is the third legitimate case -- a message about
            # the election BETWEEN the two, which belongs to neither alone.
            self.assertTrue(m.startswith('PRI SALES DUMP:')
                            or m.startswith('YTD,AOP vs.ACH:')
                            or m.startswith('Both sheets:'), m)


class TheUploadedFilesList(TestCase):
    """What the list says must match what is in the table. Reported as a
    two-tab workbook showing twice, one line reading "0 rows - Rs 0", and a
    header total that agreed with neither."""

    def _two_sheets(self):
        wb = openpyxl.Workbook()
        d = wb.active
        d.title = 'PRI SALES DUMP'
        names = header_names()
        d.append(names)
        r = a_row()
        d.append([r.get(n, '') for n in names])
        a = wb.create_sheet('YTD,AOP vs.ACH')
        a.append(AOP_HEADERS)
        ar = aop_row()
        a.append([ar.get(h, '') for h in AOP_HEADERS])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'Primary sales data.xlsx'
        return buf

    def test_one_file_makes_one_entry_however_many_sheets_it_has(self):
        upload(self._two_sheets())
        self.assertEqual(SalesUpload.objects.count(), 1)
        self.assertEqual(SalesUpload.objects.get().filename, 'Primary sales data.xlsx')

    def test_the_row_count_matches_the_rows_actually_stored(self):
        upload(self._two_sheets())
        u = SalesUpload.objects.get()
        self.assertEqual(u.row_count, SalesRecord.objects.count())
        self.assertEqual(u.row_count, u.records.count())

    def test_the_list_total_matches_the_sum_of_its_rows(self):
        """The header said 6,308 while the lines under it added to 4,308."""
        upload(self._two_sheets())
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-9'})]))
        d = self.client.get('/api/sales/uploads/').json()
        listed = sum(u['rows'] for u in d['results'])
        self.assertEqual(listed, d['total_rows'])
        self.assertEqual(listed, SalesRecord.objects.count())

    def test_a_file_that_fails_leaves_no_entry_behind(self):
        """Failing used to set a status and delete the records, leaving the
        upload row in the list for good as an empty ghost."""
        wb = openpyxl.Workbook()
        wb.active.append(['Alpha', 'Beta'])
        wb.active.append([1, 2])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'junk.xlsx'
        r = upload(buf)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(SalesUpload.objects.count(), 0)

    def test_a_cancelled_invoice_is_in_the_count_but_not_in_the_money(self):
        upload(a_workbook([a_row(), a_row(**{'Cancelled': True,
                                             'Taxable Amount': 99999})]))
        u = SalesUpload.objects.get()
        self.assertEqual(u.row_count, 2)
        self.assertEqual(float(u.total_revenue), 20000)

    def test_removing_the_file_removes_everything_it_brought(self):
        upload(self._two_sheets())
        u = SalesUpload.objects.get()
        self.client.delete(f'/api/sales/uploads/?id={u.id}')
        self.assertEqual(SalesUpload.objects.count(), 0)
        self.assertEqual(SalesRecord.objects.count(), 0)


class TheHeaderIsNotAlwaysOnRowOne(TestCase):
    """Report exports routinely carry a title or a blank line above the table."""

    def _with_preamble(self, preamble_rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        for row in preamble_rows:
            ws.append(row)
        names = header_names()
        ws.append(names)
        r = a_row()
        ws.append([r.get(n, '') for n in names])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'dump.xlsx'
        return buf

    def test_a_title_row_above_the_headers_is_stepped_over(self):
        r = upload(self._with_preamble([['APIS INDIA — PRIMARY SALES'], []]))
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(SalesRecord.objects.count(), 1)
        self.assertEqual(float(SalesRecord.objects.get().net_amount), 20000)

    def test_a_blank_first_row_is_stepped_over(self):
        r = upload(self._with_preamble([[]]))
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(SalesRecord.objects.count(), 1)

    def test_a_sheet_with_nothing_recognisable_still_reports_clearly(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(['Alpha', 'Beta', 'Gamma'])
        ws.append([1, 2, 3])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'junk.xlsx'
        r = upload(buf)
        self.assertEqual(r.status_code, 400)
        self.assertIn('date', r.json()['error'].lower())

    def test_an_empty_extra_tab_is_not_an_error(self):
        """A blank sheet left in the workbook should be ignored, not fatal."""
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'PRI SALES DUMP'
        names = header_names()
        ws.append(names)
        r = a_row()
        ws.append([r.get(n, '') for n in names])
        wb.create_sheet('Sheet3')
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'x.xlsx'
        res = upload(buf)
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.assertEqual(SalesRecord.objects.count(), 1)


class TheSellingOrganisation(TestCase):
    """Who reports to whom, and what each of them sold. Every other view
    flattens the line, so you could rank ASMs and never see whose team they
    were on, or how many an RSM carries."""

    def setUp(self):
        rows = []
        for rsm, asms in (('Anil Mehra', ['Vikas Gupta', 'Ramesh Singh']),
                          ('Sunil Rao', ['Rohit Deshmukh'])):
            for asm in asms:
                rows.append(a_row(**{'RSM Name': rsm, 'ASM Name': asm,
                                     'Customer Name': f'{asm} Distributors',
                                     'Invoice No.': f'INV-{asm[:3]}'}))
        upload(a_workbook(rows))

    def org(self, **params):
        q = '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(f'/api/sales/org/{"?" + q if q else ""}').json()

    def test_the_tree_is_built_from_the_reporting_line(self):
        d = self.org()
        names = {n['name']: n for n in d['tree']}
        self.assertEqual(set(names), {'Anil Mehra', 'Sunil Rao'})
        self.assertEqual(names['Anil Mehra']['reports'], 2)
        self.assertEqual(names['Sunil Rao']['reports'], 1)

    def test_a_missing_level_is_skipped_not_fatal(self):
        """The Pre-Sales Dump has RSM and ASM but no head. Stopping at the
        first unnamed level left every row unplaced — an empty tree over a
        full table."""
        d = self.org()
        self.assertTrue(d['tree'], 'the tree came back empty')
        self.assertEqual(d['tree'][0]['level'], 'rsm')
        counts = {c['level']: c['count'] for c in d['level_counts']}
        self.assertEqual(counts['sales_head'], 0)
        self.assertEqual(counts['rsm'], 2)
        self.assertEqual(counts['asm'], 3)

    def test_the_figures_roll_up_the_tree(self):
        d = self.org()
        for node in d['tree']:
            self.assertAlmostEqual(
                node['revenue'], sum(c['revenue'] for c in node['children']), places=2,
                msg=f"{node['name']} does not equal its team")
        self.assertAlmostEqual(
            d['totals']['revenue'], sum(n['revenue'] for n in d['tree']), places=2)

    def test_the_total_matches_the_dashboard(self):
        d = self.org()
        ov = self.client.get('/api/sales/overview/').json()
        self.assertAlmostEqual(d['totals']['revenue'], ov['revenue'], places=2)

    def test_it_counts_what_each_person_covers(self):
        d = self.org()
        anil = next(n for n in d['tree'] if n['name'] == 'Anil Mehra')
        self.assertEqual(anil['customers'], 2)      # one per ASM
        self.assertGreaterEqual(anil['skus'], 1)

    def test_the_levels_can_be_geography_instead(self):
        d = self.org(levels='zone,state')
        self.assertEqual(d['levels'], ['zone', 'state'])
        self.assertTrue(d['tree'])
        self.assertEqual(d['tree'][0]['level'], 'zone')

    def test_an_unknown_level_is_ignored_rather_than_queried(self):
        """The level name reaches .values(), so anything not on the list must
        never get through."""
        d = self.org(levels='rsm,password,secret')
        self.assertEqual(d['levels'], ['rsm'])

    def test_filters_apply_to_the_tree_too(self):
        d = self.org(rsm='Anil Mehra')
        self.assertEqual([n['name'] for n in d['tree']], ['Anil Mehra'])

    def test_no_target_reads_as_unknown_not_as_missed(self):
        """0% would say they missed the plan. There is no plan."""
        d = self.org()
        self.assertIsNone(d['tree'][0]['achievement_pct'])


# ── what an audit against the real file turned up ────────────────────────
class PerOrderFiguresOnlyCountInvoicedRows(TestCase):
    """Two thirds of the money on this dashboard arrives from the review
    sheet, which is monthly aggregates: no invoice, no customer, no SKU.
    Dividing all the revenue by the invoices that do exist is a ratio between
    two different populations, and on the real file it reported an average
    order of Rs 30 lakh against a true Rs 1.8 lakh."""

    def setUp(self):
        upload(a_workbook([
            a_row(**{'Invoice No.': 'INV-1', 'Taxable Amount': 10000}),
            a_row(**{'Invoice No.': 'INV-2', 'Taxable Amount': 10000}),
        ]))
        # A row with no invoice on it, standing for a review-sheet month.
        SalesRecord.objects.create(
            upload=SalesUpload.objects.first(),
            order_date=date(2026, 4, 1), period=date(2026, 4, 1),
            invoice_no='', net_amount=980000, quantity=0)

    def overview(self):
        return Client().get('/api/sales/overview/').json()

    def test_a_row_with_no_invoice_is_not_an_order(self):
        """Counting distinct invoice numbers across everything also counts
        the empty string: one phantom order standing in for every aggregate
        row in the file."""
        self.assertEqual(self.overview()['orders'], 2)

    def test_average_order_value_ignores_rows_with_no_invoice(self):
        """20,000 over two invoices, not 1,000,000 over two."""
        self.assertEqual(self.overview()['avg_order_value'], 10000)

    def test_the_headline_still_counts_every_rupee(self):
        """Only the per-order figures narrow; revenue is all of it."""
        self.assertEqual(self.overview()['revenue'], 1000000)

    def test_it_says_how_much_of_the_money_is_invoiced(self):
        d = self.overview()
        self.assertEqual(d['invoiced_revenue'], 20000)
        self.assertEqual(d['invoiced_pct'], 2.0)


class MomentumNeedsSomethingToCompareWith(TestCase):
    """The growth quadrant is entirely about momentum. With nothing before
    the window, every growth comes back None -- which the quadrant read as
    zero, cleared a median of zero, and so declared every single group
    "fast". Half the sales force came out as Stars, "big and growing, protect
    and invest", on no evidence at all, above a chart that plotted nothing
    because the real y-value was null."""

    def setUp(self):
        upload(a_workbook([
            a_row(**{'RSM Name': 'Anil Mehra', 'Invoice No.': 'INV-1',
                     'Taxable Amount': 50000}),
            a_row(**{'RSM Name': 'Sunil Rao', 'Invoice No.': 'INV-2',
                     'Taxable Amount': 10000}),
        ]))

    def matrix(self):
        return Client().get('/api/sales/matrix/?dim=rsm').json()

    def test_it_says_there_is_nothing_to_compare_against(self):
        self.assertFalse(self.matrix()['comparable'])

    def test_nobody_is_called_a_star_on_no_evidence(self):
        for r in self.matrix()['results']:
            self.assertIsNone(r['quadrant'], r)

    def test_the_revenue_is_still_reported(self):
        """Only the momentum half is unknowable. The rupees are real."""
        rows = {r['name']: r for r in self.matrix()['results']}
        self.assertEqual(rows['Anil Mehra']['revenue'], 50000)


class OneQuestionGetsOneAnswer(TestCase):
    """Achievement was computed two ways on the same screen: the tile over
    the months carrying both a plan and a result, and the insights panel over
    revenue-to-date against the whole year's plan. On the real file that read
    73% in one place and 86% in the other, with a gap of Rs 36 Cr against
    Rs 45 Cr -- the exact bug comparable_window was written to kill, fixed in
    one panel and left standing in the other."""

    def setUp(self):
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-1',
                                    'Taxable Amount': 70000})]))
        rec = SalesRecord.objects.first()
        # A month with a plan and a result, and another with plan only --
        # the rest of the year, which must not drag achievement down.
        SalesRecord.objects.filter(id=rec.id).update(
            target_amount=100000, measured_amount=70000)
        SalesRecord.objects.create(
            upload=SalesUpload.objects.first(),
            order_date=date(2027, 1, 1), period=date(2027, 1, 1),
            invoice_no='', net_amount=0, quantity=0, target_amount=100000)

    def test_the_tile_and_the_insight_report_the_same_number(self):
        tile = Client().get('/api/sales/overview/').json()['achievement_pct']
        insights = Client().get('/api/sales/insights/').json()['insights']
        said = [i for i in insights if 'arget' in i['title']]
        self.assertTrue(said, 'no target insight was produced')
        self.assertIn(f'{tile:.0f}%', said[0]['title'])

    def test_a_month_with_a_plan_and_no_sales_yet_is_left_out(self):
        """Counting the rest of the year makes a healthy business read as
        though it is missing target."""
        self.assertEqual(
            Client().get('/api/sales/overview/').json()['achievement_pct'], 70.0)


class ADarkBreakdownSaysWhyItIsDark(TestCase):
    """Two reasons a breakdown has nothing in it, and they want opposite
    actions. Area and Salesperson are in neither file, so adding the column
    is exactly right. Sub Category and Variant ARE columns in the dump — they
    arrive on every row and are blank on every row — so "add this column and
    re-upload" sends somebody to add a column that is already there, and
    nothing changes when they do."""

    def setUp(self):
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-1'})]))

    def detail(self):
        d = Client().get('/api/sales/filters/').json()
        return {x['dim']: x['reason'] for x in d['absent_detail']}

    def test_a_column_the_file_does_not_have_reads_as_missing(self):
        self.assertEqual(self.detail().get('salesperson'), 'missing')
        self.assertEqual(self.detail().get('area'), 'missing')

    def test_a_column_that_is_there_but_blank_reads_as_empty(self):
        """The dump declares both; a_row leaves them blank, as the real
        export does."""
        self.assertEqual(self.detail().get('variant'), 'empty')
        self.assertEqual(self.detail().get('sub_category'), 'empty')

    def test_a_column_that_is_filled_in_is_not_listed_at_all(self):
        SalesRecord.objects.update(variant='Organic')
        self.assertNotIn('variant', self.detail())

    def test_every_absent_dimension_is_accounted_for(self):
        d = Client().get('/api/sales/filters/').json()
        self.assertEqual(sorted(x['dim'] for x in d['absent_detail']),
                         sorted(d['absent_dimensions']))


class WhoMaySignInToSalesIQ(TestCase):
    """Granting SalesIQ in the Admin Console put the tile on somebody's
    dashboard and then this login turned them away: "This email is not
    authorised for SalesIQ." Two allowlists that did not know about each
    other, and no screen anywhere that explained the second one."""

    def setUp(self):
        from accounts.models import AppKey, PortalUser
        self.PortalUser, self.AppKey = PortalUser, AppKey
        self.granted = PortalUser.objects.create(
            email='rsm@apisindia.com', name='RSM', employee_code='E1',
            is_active=True, app_access=[AppKey.SALESIQ])
        self.other = PortalUser.objects.create(
            email='nobody@apisindia.com', name='Nobody', employee_code='E2',
            is_active=True, app_access=[AppKey.HELPDESK])

    def send(self, email):
        return Client().post('/api/sales/login/',
                             {'action': 'send_otp', 'email': email})

    def test_the_console_grant_is_enough(self):
        self.assertEqual(self.send('rsm@apisindia.com').status_code, 200)

    def test_a_colleague_without_the_grant_is_still_refused(self):
        """A company address is not access. This is company-wide revenue."""
        r = self.send('nobody@apisindia.com')
        self.assertEqual(r.status_code, 403)
        self.assertIn('not authorised', r.json()['error'])

    def test_disabling_sign_in_disables_it_here_too(self):
        """Otherwise "disable sign-in" in the console is a lie."""
        self.granted.is_active = False
        self.granted.save(update_fields=['is_active'])
        self.assertEqual(self.send('rsm@apisindia.com').status_code, 403)

    def test_revoking_the_tool_revokes_the_login(self):
        self.granted.app_access = []
        self.granted.save(update_fields=['app_access'])
        self.assertEqual(self.send('rsm@apisindia.com').status_code, 403)

    def test_a_portal_superadmin_gets_in(self):
        self.other.is_superadmin = True
        self.other.save(update_fields=['is_superadmin'])
        self.assertEqual(self.send('nobody@apisindia.com').status_code, 200)

    def test_the_hard_coded_super_admin_always_gets_in(self):
        """Not in the portal tables at all here, and still in."""
        from sales.views.auth import SALESIQ_SUPER_ADMIN
        self.assertEqual(self.send(SALESIQ_SUPER_ADMIN).status_code, 200)

    def test_an_outsider_is_refused(self):
        self.assertEqual(self.send('attacker@gmail.com').status_code, 403)


class SalesIQIsNotOpenToTheInternet(TestCase):
    """Every endpoint inherited APIView, which is no check at all: revenue,
    customers, the sales hierarchy and every invoice line answered anyone who
    knew the URL. The login screen gated the page, and a page is not a gate."""

    READS = ['overview', 'breakdown', 'trend', 'forecast', 'filters', 'insights',
             'uploads', 'pareto', 'matrix', 'movers', 'anomalies', 'seasonality',
             'heatmap', 'rfm', 'cohorts', 'new-repeat', 'yoy', 'pacing', 'price',
             'org', 'export', 'template']

    def test_not_one_of_them_answers_a_stranger(self):
        c = RawClient()
        for e in self.READS:
            self.assertEqual(c.get(f'/api/sales/{e}/').status_code, 401,
                             f'/{e}/ answered without a session')

    def test_nor_do_the_two_that_change_things(self):
        c = RawClient()
        self.assertEqual(c.post('/api/sales/upload/', {}).status_code, 401)
        self.assertEqual(c.delete('/api/sales/uploads/?id=1').status_code, 401)

    def test_a_made_up_token_is_not_a_session(self):
        c = RawClient()
        r = c.get('/api/sales/overview/', HTTP_X_SALESIQ_SESSION='not-a-real-token')
        self.assertEqual(r.status_code, 401)


class OnlyTheOwnerChangesTheData(TestCase):
    """Verifying an OTP answered 'role': 'super_admin' to everybody, hard-
    coded — the one person who owns this data and anybody granted read access
    in the console got the same string, so the page handed both of them the
    upload and delete controls."""

    def setUp(self):
        from accounts.models import AppKey, PortalUser
        from sales.views.auth import SALESIQ_SUPER_ADMIN, issue_session
        self.owner_email = SALESIQ_SUPER_ADMIN
        PortalUser.objects.create(
            email='reader@apisindia.com', name='Reader', employee_code='E1',
            is_active=True, app_access=[AppKey.SALESIQ])
        self.owner = {'HTTP_X_SALESIQ_SESSION': issue_session(SALESIQ_SUPER_ADMIN)}
        self.reader = {'HTTP_X_SALESIQ_SESSION': issue_session('reader@apisindia.com')}

    def test_a_reader_reads(self):
        self.assertEqual(
            Client().get('/api/sales/overview/', **self.reader).status_code, 200)

    def test_a_reader_cannot_upload(self):
        r = Client().post('/api/sales/upload/', {}, **self.reader)
        self.assertEqual(r.status_code, 403)
        self.assertIn('read-only', r.json()['error'])

    def test_a_reader_cannot_delete(self):
        r = Client().delete('/api/sales/uploads/?id=1', **self.reader)
        self.assertEqual(r.status_code, 403)

    def test_a_reader_may_still_see_which_files_are_loaded(self):
        """That is the provenance of every figure on the screen."""
        self.assertEqual(
            Client().get('/api/sales/uploads/', **self.reader).status_code, 200)

    def test_the_owner_may_upload(self):
        """Reaches the view rather than the gate -- 400 for an empty body."""
        r = Client().post('/api/sales/upload/', {}, **self.owner)
        self.assertNotIn(r.status_code, (401, 403))

    def test_the_login_hands_back_the_real_role(self):
        from django.core.cache import cache
        for email, expected in ((self.owner_email, 'super_admin'),
                                ('reader@apisindia.com', 'viewer')):
            cache.set(f'salesiq_otp_{email}', {'code': '123456', 'attempts': 0}, 300)
            d = Client().post('/api/sales/login/', {
                'action': 'verify_otp', 'email': email, 'otp': '123456'}).json()
            self.assertEqual(d['role'], expected, email)
            self.assertEqual(d['can_edit'], expected == 'super_admin')
            self.assertTrue(d['token'])

    def test_the_token_it_issues_actually_works(self):
        from django.core.cache import cache
        cache.set('salesiq_otp_reader@apisindia.com',
                  {'code': '123456', 'attempts': 0}, 300)
        d = Client().post('/api/sales/login/', {
            'action': 'verify_otp', 'email': 'reader@apisindia.com',
            'otp': '123456'}).json()
        r = Client().get('/api/sales/overview/',
                         HTTP_X_SALESIQ_SESSION=d['token'])
        self.assertEqual(r.status_code, 200)


class RevenueMeansThisYear(TestCase):
    """What "revenue" is, when the file holds more than one year of it.

    Both primary files reach back further than the year in progress: the
    review sheet carries last year's actuals beside this year's so the
    dashboard can show growth. Every endpoint totalled the lot, so the
    headline read Rs 298 crore -- eighteen months of two financial years --
    under a subtitle comparing it with six months of last year, and it
    matched nothing in the review sheet the business actually reads.
    """

    def _two_years(self):
        # 500,000 last year, 60,000 this April against a 100,000 plan.
        upload(aop_workbook([aop_row(aop=100000, cy=60000, ly=500000)]))

    def test_the_window_defaults_to_the_financial_year(self):
        self._two_years()
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(float(d['revenue']), 60000)

    def test_the_screen_is_told_which_year_it_is_showing(self):
        """A default nobody can see is a default nobody can correct."""
        self._two_years()
        w = Client().get('/api/sales/overview/').json()['filters']['window']
        self.assertEqual(w['basis'], 'financial_year')
        self.assertEqual(w['label'], 'FY26-27')
        self.assertEqual(w['from'], '2026-04-01')
        self.assertEqual(w['to'], '2027-03-31')

    def test_an_explicit_date_wins_over_the_default(self):
        self._two_years()
        d = Client().get('/api/sales/overview/?from=2025-04-01').json()
        self.assertNotIn('window', d['filters'])

    def test_the_same_window_reaches_the_breakdowns(self):
        """The headline and the tables under it have to describe the same
        stretch of time, or one of them is lying about the other."""
        self._two_years()
        d = Client().get('/api/sales/breakdown/?dim=zone').json()
        self.assertEqual(sum(float(r['revenue']) for r in d['results']), 60000)

    def test_the_trend_line_still_sees_last_year(self):
        """History is the subject of a trend chart, not noise around it."""
        self._two_years()
        d = Client().get('/api/sales/trend/').json()
        months = [r['period'] for r in d['results']]
        self.assertIn('2025-04-01', str(months), f'last year fell off the chart: {months}')

    def test_growth_against_last_year_survives_the_default(self):
        self._two_years()
        ly = Client().get('/api/sales/overview/').json()['vs_last_year']
        self.assertEqual(ly['this_year'], 60000)
        self.assertEqual(ly['last_year'], 500000)

    def test_an_empty_table_does_not_invent_a_window(self):
        d = Client().get('/api/sales/overview/').json()
        self.assertNotIn('window', d['filters'])


class ThePriorPeriodIsTheSameLength(TestCase):
    """A month that has only a plan is not a month that has happened.

    A financial year is loaded with twelve months of plan the day it opens,
    and those rows carry a date. Measuring the current window off them put
    its end in the following February, so "the preceding window of equal
    length" ran eleven months back and was compared against six months of
    sales -- reported as growth.
    """

    def test_an_unstarted_month_does_not_stretch_the_window(self):
        upload(aop_workbook([aop_row(aop=100000, cy=60000, ly=500000,
                                     **{'Mar-27 AOP': 100000})]))
        p = Client().get('/api/sales/overview/').json()['period']
        self.assertLess(p['to'], '2026-05-01',
                        f"the window runs to {p['to']}, months nothing happened in")


class WhichFileAnswersTheQuestionAsked(TestCase):
    """Two files, two different questions, and the filter decides which.

    The review sheet is month-wise and year-wise: twelve columns a year, no
    days in it at all, and its YTD ACH column is the figure the business
    reads. The invoice dump is date-wise: one row per invoice line, dated to
    the day, but only the month it was extracted in.

    So asking by month or by year reads the sheet, and asking for a date
    range reads the dump. Letting both answer either question is what put a
    year-to-date figure on screen that appears in neither file.
    """

    def _both(self):
        # Review sheet: Apr 2025 at 80,000 and Apr 2026 at 90,000.
        # Dump: one invoice line on 5 Apr 2026 at 20,000.
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-001'})]))

    def test_by_year_the_money_is_the_review_sheets(self):
        self._both()
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(float(d['revenue']), 90000,
                         "the dump's April was added to or swapped for the sheet's")
        self.assertEqual(d['filters']['source'], 'review_sheet')

    def test_a_date_range_is_answered_by_the_dump(self):
        self._both()
        d = Client().get('/api/sales/overview/?from=2026-04-01&to=2026-04-30').json()
        self.assertEqual(float(d['revenue']), 20000,
                         'the review sheet answered a question about days')
        self.assertEqual(d['filters']['source'], 'invoice_dump')

    def test_a_date_range_inside_the_month_is_not_a_whole_month(self):
        """The sheet's rows all sit on the 1st, so leaving them in scope
        answered "the 4th to the 6th" with the entire month's sales."""
        self._both()
        d = Client().get('/api/sales/overview/?from=2026-04-04&to=2026-04-06').json()
        self.assertEqual(float(d['revenue']), 20000)

    def test_the_invoice_panels_survive_a_month_wise_view(self):
        """The sheet has no customers, SKUs or invoices. Measured over the
        money queryset these read zero against real revenue; they are read
        off the dump instead and labelled as covering its months only."""
        self._both()
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(d['customers'], 1)
        self.assertEqual(d['skus'], 1)
        self.assertEqual(d['orders'], 1)
        self.assertEqual(float(d['quantity']), 100)

    def test_the_screen_is_told_how_much_of_the_money_those_panels_cover(self):
        self._both()
        d = Client().get('/api/sales/overview/').json()
        # 20,000 of invoice detail against 90,000 of revenue on screen.
        self.assertEqual(d['invoiced_revenue'], 20000)
        self.assertAlmostEqual(d['invoiced_pct'], 22.2, places=1)

    def test_an_order_size_is_measured_over_orders_not_over_everything(self):
        self._both()
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(float(d['avg_order_value']), 20000,
                         'revenue the sheet reported was divided by invoice count')


class ThePostingGroupIsNotTheSalesChannel(TestCase):
    """DOMESTIC / EXPORT is how the ledger posts a sale, not how it was sold."""

    def test_domestic_does_not_appear_as_a_channel(self):
        upload(a_workbook([a_row(**{'Gen. Bus. Posting Group': 'DOMESTIC'})]))
        channels = set(SalesRecord.objects.values_list('channel', flat=True))
        self.assertNotIn('DOMESTIC', channels)

    def test_the_two_files_do_not_describe_channel_in_different_words(self):
        """GT and OT from the review sheet beside DOMESTIC from the dump is
        one filter list holding two vocabularies for two different things."""
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row(**{'Gen. Bus. Posting Group': 'DOMESTIC'})]))
        d = Client().get('/api/sales/filters/').json()
        self.assertNotIn('DOMESTIC', d.get('channel', []))

    def test_it_is_called_a_skipped_column_not_an_unknown_one(self):
        """"Not recognised" sends somebody hunting for a mapping that should
        not exist."""
        r = upload(a_workbook([a_row()])).json()
        blob = ' '.join(r.get('warnings', [])).lower()
        self.assertNotIn('gen. bus. posting group', blob)


class OneTileIsMeasuredOffOneFile(TestCase):
    """The headline and the line under it have to come from the same place.

    The original complaint was a tile reading Rs 298 Cr over "vs Rs 76.97 Cr
    same 6 months last year" -- a number covering eighteen months above a
    comparison covering six. Picking a date range reintroduced it in a new
    form: the money came off the dump and the comparison beneath it off the
    review sheet.
    """

    def _both(self):
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-001'})]))

    def test_a_date_range_has_no_same_months_last_year(self):
        """The review sheet has no days, so there is no same fortnight last
        year for it to answer with."""
        self._both()
        d = Client().get('/api/sales/overview/?from=2026-04-04&to=2026-04-06').json()
        self.assertIsNone(d['vs_last_year'])

    def test_by_year_it_still_does(self):
        self._both()
        d = Client().get('/api/sales/overview/').json()
        self.assertIsNotNone(d['vs_last_year'])
        self.assertEqual(d['vs_last_year']['last_year'], 80000)

    def test_the_prior_period_comes_off_the_same_file_as_the_headline(self):
        self._both()
        d = Client().get('/api/sales/overview/?from=2026-04-01&to=2026-04-30').json()
        self.assertEqual(d['filters']['source'], 'invoice_dump')
        # March 2026 in the review sheet must not become the dump's prior
        # period. The dump reaches one month, so there is nothing behind it.
        self.assertFalse(d['prev_period_has_data'])


class ABreakdownComesFromTheFileThatHasTheColumn(TestCase):
    """The review sheet can be split by zone, brand, RSM and a few more. It
    has no customers, SKUs or categories at all, so a split by one of those
    read off it comes back empty -- which is most of the time, because the
    sheet is what answers a month or a year."""

    def _both(self):
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-001'})]))

    def test_a_customer_split_survives_a_month_wise_view(self):
        self._both()
        d = Client().get('/api/sales/breakdown/?dim=customer').json()
        self.assertTrue(d['results'], 'the customer chart emptied out')
        self.assertEqual(d['results'][0]['name'], 'Sharma Traders')
        self.assertEqual(d['filters']['source'], 'invoice_dump')

    def test_a_zone_split_is_the_review_sheets_and_ties_to_the_headline(self):
        self._both()
        d = Client().get('/api/sales/breakdown/?dim=zone').json()
        head = Client().get('/api/sales/overview/').json()
        self.assertEqual(sum(float(r['revenue']) for r in d['results']),
                         float(head['revenue']),
                         'the zone chart and the headline disagree')

    def test_the_customer_panels_do_not_empty_out_either(self):
        self._both()
        for path in ('rfm/', 'cohorts/', 'new-repeat/', 'pareto/?dim=customer'):
            r = Client().get('/api/sales/' + path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.json()['filters'].get('source'), 'invoice_dump', path)


class TheLinesTheBusinessExcludes(TestCase):
    """Which V-REMARS verdicts leave the sales figure, and which stay in.

    The rule as the business states it: NOT A PART OF SALES and SCHEME CN are
    not sales. Everything else is, returns included.

    SCHEME CN used to be filed as a return, which nets it off by sign rather
    than dropping it -- so a date-wise figure came out lower than the sheet by
    the value of those lines. That mismatch is the reason this class exists,
    and the arithmetic below is what distinguishes netting off from excluding:
    a 1,000 credit note netted off gives 19,000, excluded gives 20,000.
    """

    def test_a_scheme_credit_note_is_dropped_not_netted_off(self):
        upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'SCHEME CN', 'Taxable Amount': -1000}),
        ]))
        rev = float(Client().get('/api/sales/overview/').json()['revenue'])
        self.assertEqual(rev, 20000,
                         f'19,000 means the credit note was netted off instead '
                         f'of excluded; got {rev}')

    def test_it_is_dropped_on_a_date_range_too(self):
        """The date-wise view reads the invoice dump, which is where the
        mismatch against the sheet was actually seen."""
        upload(a_workbook([
            a_row(**{'Order Date': '2026-04-05'}),
            a_row(**{'Order Date': '2026-04-05', 'V-REMARS': 'SCHEME CN',
                     'Taxable Amount': -1000}),
        ]))
        r = Client().get('/api/sales/overview/?from=2026-04-01&to=2026-04-30').json()
        self.assertEqual(float(r['revenue']), 20000, r.get('applied'))

    def test_both_spellings_of_not_a_part_of_sale_are_excluded(self):
        """The file writes SALES, the rule is spoken as "sale". A one-letter
        mismatch would silently put freight back into revenue."""
        upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'NOT A PART OF SALES', 'Taxable Amount': 50000}),
            a_row(**{'V-REMARS': 'Not a part of sale', 'Taxable Amount': 40000}),
        ]))
        self.assertEqual(SalesRecord.objects.filter(is_not_sales=True).count(), 3 - 1)
        rev = float(Client().get('/api/sales/overview/').json()['revenue'])
        self.assertEqual(rev, 20000, f'freight reached revenue: {rev}')

    def test_a_real_return_still_reduces_the_figure(self):
        """The counterpart to the rule: SR and GOOD SR are stock genuinely
        coming back, they belong in the figure, and the minus sign is the
        point. Excluding them too would overstate sales."""
        upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'SR', 'Taxable Amount': -5000}),
            a_row(**{'V-REMARS': 'GOOD SR', 'Taxable Amount': -2000}),
        ]))
        rev = float(Client().get('/api/sales/overview/').json()['revenue'])
        self.assertEqual(rev, 13000,
                         f'20,000 less returns of 7,000 is 13,000, not {rev}')
        self.assertEqual(SalesRecord.objects.filter(is_not_sales=True).count(), 0,
                         'a genuine return was excluded from sales')

    def test_an_excluded_line_is_kept_in_the_table(self):
        """Excluded from the figure, not deleted: the file still has to
        reconcile line for line with the ERP."""
        upload(a_workbook([
            a_row(),
            a_row(**{'V-REMARS': 'SCHEME CN', 'Taxable Amount': -1000}),
        ]))
        self.assertEqual(SalesRecord.objects.count(), 2)


class TheMigrationThatRefiledTheOldRows(TestCase):
    """The flags are decided at import, so fixing the importer fixes nothing
    already loaded. Migration 0012 re-runs the rule over what is there.

    Tests apply migrations to an empty database, so the suite passing proves
    only that the migration runs. This proves it does something.

    The rows come through the real importer and are then forced back to the
    OLD classification, which is exactly the state the live tables are in:
    real rows, stale flags.
    """

    def setUp(self):
        from django.apps import apps
        import importlib
        self.apps = apps
        self.mig = importlib.import_module('sales.migrations.0012_exclude_scheme_cn')

        upload(a_workbook([
            a_row(**{'V-REMARS': 'SALES'}),
            a_row(**{'V-REMARS': 'SCHEME CN', 'Taxable Amount': -1000}),
            a_row(**{'V-REMARS': 'NOT A PART OF SALES', 'Taxable Amount': 5000}),
            a_row(**{'V-REMARS': 'SR', 'Taxable Amount': -500}),
        ]))
        # Undo the new rule, leaving the table as the old importer left it:
        # SCHEME CN filed as a return, nothing excluded from sales.
        SalesRecord.objects.filter(remarks='SCHEME CN').update(
            is_not_sales=False, is_return=True)
        SalesRecord.objects.filter(remarks='NOT A PART OF SALES').update(
            is_not_sales=False)

    def _flags(self, remark):
        r = SalesRecord.objects.get(remarks=remark)
        return r.is_not_sales, r.is_return

    def test_it_refiles_a_credit_note_and_spares_a_genuine_return(self):
        self.mig.apply_rule(self.apps, None)
        self.assertEqual(self._flags('SCHEME CN'), (True, False),
                         'SCHEME CN must be excluded and not also a return')
        self.assertEqual(self._flags('NOT A PART OF SALES')[0], True,
                         'freight was not excluded')
        self.assertEqual(self._flags('SR'), (False, True),
                         'a genuine return was refiled')
        self.assertEqual(self._flags('SALES')[0], False,
                         'an ordinary sale was excluded')

    def test_the_figure_moves_by_the_value_of_the_excluded_lines(self):
        """What the mismatch looked like: a credit note netted off and freight
        counted, so 19,500 of real sales reported as 23,500."""
        before = float(Client().get('/api/sales/overview/').json()['revenue'])
        self.assertEqual(before, 23500, f'fixture is not as assumed: {before}')
        self.mig.apply_rule(self.apps, None)
        after = float(Client().get('/api/sales/overview/').json()['revenue'])
        self.assertEqual(after, 19500,
                         f'20,000 of sales less a 500 return is 19,500, not {after}')

    def test_the_reverse_puts_a_credit_note_back(self):
        self.mig.apply_rule(self.apps, None)
        self.mig.restore(self.apps, None)
        self.assertEqual(self._flags('SCHEME CN'), (False, True),
                         'reverse did not restore the old filing')


class BothFilesCallTheChannelTheSameThing(TestCase):
    """GT and OT, from a column both files spell CHANEL TYPE.

    The dump's channel aliases used to list `channel`, `sales channel`,
    `trade channel` and `route to market` -- none of which the file has. So
    every invoice row carried a blank channel, and because a date range reads
    the dump, filtering by GT on a date range returned nothing at all while
    the same filter on a month returned the sheet's figure. Two views of one
    business disagreeing because of a column name.
    """

    def test_the_dump_reads_its_channel_column(self):
        upload(a_workbook([
            a_row(**{'Chanel Type': 'GT'}),
            a_row(**{'Chanel Type': 'OT', 'Taxable Amount': 5000}),
        ]))
        self.assertEqual(
            sorted(SalesRecord.objects.values_list('channel', flat=True)),
            ['GT', 'OT'], 'the dump did not read CHANEL TYPE')

    def test_filtering_by_gt_on_a_date_range_returns_the_gt_figure(self):
        """The failure as it was seen: a date range plus channel=GT gave 0."""
        upload(a_workbook([
            a_row(**{'Chanel Type': 'GT'}),
            a_row(**{'Chanel Type': 'OT', 'Taxable Amount': 5000}),
        ]))
        r = Client().get('/api/sales/overview/'
                         '?from=2026-04-01&to=2026-04-30&channel=GT').json()
        self.assertEqual(float(r['revenue']), 20000,
                         f'GT on a date range gave {r["revenue"]}, not 20,000')

    def test_the_channel_breakdown_is_not_empty_on_a_date_range(self):
        upload(a_workbook([
            a_row(**{'Chanel Type': 'GT'}),
            a_row(**{'Chanel Type': 'OT', 'Taxable Amount': 5000}),
        ]))
        rows = {r['name']: float(r['revenue']) for r in
                Client().get('/api/sales/breakdown/'
                             '?dim=channel&from=2026-04-01&to=2026-04-30')
                .json()['results']}
        self.assertEqual(rows.get('GT'), 20000, rows)
        self.assertEqual(rows.get('OT'), 5000, rows)

    def test_the_posting_group_still_does_not_reach_the_channel(self):
        """DOMESTIC / EXPORT is an accounting posting group. It was once read
        as the channel and must not come back now that a real one exists."""
        upload(a_workbook([
            a_row(**{'Chanel Type': 'GT', 'Gen. Bus. Posting Group': 'DOMESTIC'}),
        ]))
        self.assertEqual(
            list(SalesRecord.objects.values_list('channel', flat=True)), ['GT'])
