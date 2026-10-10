"""SalesIQ ingestion — what counts as a sale, and what the dump columns mean.

The Pre-Sales Dump is an ERP export, not a sales report: it carries cancelled
invoices and credit memos in the same sheet as live billing, splits the money
across a dozen columns, and repeats several figures in two forms. These tests
hold the decisions that turn it into numbers a person can act on.
"""
import io

import openpyxl
from datetime import date, datetime

from django.db.models import F, Sum
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
from sales import review as REVIEW
from sales import report as REPORT

from sales.ingest import (PRE_SALES_DUMP, build_template, is_return_type,
                          map_headers, parse_bool, pick_sheets)
from sales.models import (SalesRecord, SalesUpload,
                          ReviewSnapshot, ReviewRow,
                          ReportRecipient, UploaderGrant)


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
        # Finished-goods code: the SKU tile counts only FG-prefixed codes,
        # because the same column also carries raw material and packaging.
        'Item Code': 'FG-HNY-500', 'Item Name': 'APIS Honey 500g',
        'Sales Order No.': 'SO-1001',
        'Item Category': 'Honey', 'Quantity': 100, 'Unit Price': 250,
        'Taxable Amount': 20000,
        'Invoice Disc.Amt': 1000, 'Retail Scheme Amt': 500, 'Wholesale Scheme Amt': 500,
        'IGST Amount': 0, 'CGST Amount': 1800, 'SGST Amount': 1800,
    }
    d.update(over)
    return d


def upload(buf, name=None, **params):
    """`name` matters: the as-of date is read off the file name."""
    if name:
        buf.name = name
    url = '/api/sales/upload/'
    if params:
        from urllib.parse import urlencode
        url += '?' + urlencode(params)
    return Client().post(url, {'file': buf})


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
# The ID columns sit exactly where the real sheet puts them: each people
# column followed by its own pair. Both pairs are headed the same thing --
# the sheet tells them apart with a trailing full stop, which the header
# normaliser strips -- so only their position says whose they are.
AOP_DIMS = ['CHANEL TYPE', 'HEAD', 'GTR HEAD', 'BIZOM ID', 'APIS ID',
            'REPORT.INCHARGE', 'BIZOM ID.', 'APIS ID.', 'REGION',
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
        'GTR HEAD': 'Anil Mehra', 'BIZOM ID': 'BZ900', 'APIS ID': 'AP100',
        'REPORT.INCHARGE': 'Vikas Gupta', 'BIZOM ID.': 'BZ901',
        'APIS ID.': 'AP200',
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
        # I-CODE is the line number within the territory, not a product
        # code -- read, and deliberately not stored. See aop.IGNORED.
        self.assertEqual(r.item_alt_code, '')
        self.assertEqual(r.sfo_count, 4)

    def test_each_persons_id_columns_are_the_ones_beside_them(self):
        """Both pairs normalise to the same header text, so position is the
        only thing that says whose they are. Mapped by name, the ASM would
        have come out carrying the RSM's ID."""
        upload(aop_workbook([aop_row()]))
        r = SalesRecord.objects.filter(period=date(2026, 4, 1)).first()
        self.assertEqual(r.rsm_code, 'AP100')
        self.assertEqual(r.asm_code, 'AP200')

    def test_the_apis_id_wins_and_bizom_stands_in_where_it_is_missing(self):
        upload(aop_workbook([aop_row(**{'APIS ID': '', 'APIS ID.': ''})]))
        r = SalesRecord.objects.filter(period=date(2026, 4, 1)).first()
        # Prefixed, so a Bizom 900 can never be read as an APIS 900.
        self.assertEqual(r.rsm_code, 'BZ-BZ900')
        self.assertEqual(r.asm_code, 'BZ-BZ901')

    def test_a_whole_numbered_id_is_not_stored_as_a_float(self):
        """Excel hands back 10432 as 10432.0, and the two spellings would
        count as two people."""
        self.assertEqual(AOP._id_text(10432.0), '10432')
        self.assertEqual(AOP._id_text(10432), '10432')
        self.assertEqual(AOP._id_text(None), '')

    def test_the_id_columns_are_not_reported_as_columns_we_did_not_read(self):
        _, _, unknown = AOP.map_columns(AOP_HEADERS)
        leaked = [h for h in unknown if 'ID' in h.upper()]
        self.assertEqual(leaked, [], f'ID columns reported as unread: {leaked}')

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
        d = Client().get('/api/sales/overview/?month_from=2025-04&month_to=2027-03').json()
        # 20,000 for April 2026, from the review sheet. The dump's April 2026
        # is the same money and is not added again, and April 2025 is below
        # the display floor -- asking for it in the URL does not bring it
        # back, which is the point of a floor.
        self.assertEqual(float(d['revenue']), 20000)

    def test_removing_the_invoice_data_hands_the_figures_back(self):
        upload(aop_workbook([aop_row()]))
        r = upload(a_workbook([a_row()])).json()
        Client().delete(f"/api/sales/uploads/?id={r['upload_id']}")
        cy = SalesRecord.objects.get(source='plan', period=date(2026, 4, 1))
        self.assertEqual(float(cy.net_amount), 90000,
                         'the dashboard would show zero with a good plan file loaded')

    def test_a_month_in_both_files_is_counted_once(self):
        # April 2026 is in both files, at 20,000 each, and must be counted
        # once. April 2025 is in the review sheet alone and must not appear
        # at all -- it is below the display floor, loaded for growth maths
        # rather than for the screen.
        upload(aop_workbook([aop_row(cy=20000)]))
        upload(a_workbook([a_row()]))           # 20,000 taxable, April 2026
        d = Client().get('/api/sales/overview/?month_from=2025-04&month_to=2027-03').json()
        rev = float(d.get('total_revenue') or d.get('revenue') or 0)
        # Counted once, not twice. April 2025 is loaded and is deliberately
        # not in this figure: it is below the display floor.
        self.assertEqual(rev, 20000, f'April 2026 counted from both files: {rev}')

    def test_no_month_is_counted_from_both_files_at_once(self):
        """The invariant underneath every total on the dashboard.

        It used to be a property of the rows -- the losing source was zeroed
        in the table. That could not express "the dump answers a date range",
        because a zeroed row answers nothing, so the rule moved into the
        query. Which means this has to be checked on the answer.
        """
        upload(aop_workbook([aop_row(cy=20000)]))
        upload(a_workbook([a_row()]))
        # The sheet has April 2026 at 20,000 and the dump has the same April
        # 2026 at 20,000. Counted once: 20,000.
        d = Client().get('/api/sales/overview/?month_from=2025-04&month_to=2027-03').json()
        self.assertEqual(float(d['revenue']), 20000,
                         'April 2026 is being counted from both files')
        # And per month, so a single wrong month cannot hide inside a right
        # total -- including the month that must not be there at all.
        rows = {r['period']: float(r['revenue'] or 0)
                for r in Client().get('/api/sales/trend/').json()['results']}
        self.assertEqual(rows['2026-04-01'], 20000)
        self.assertNotIn('2025-04-01', rows, 'last year reached the trend line')


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
        rev = float(Client().get('/api/sales/overview/?month_from=2025-04&month_to=2027-03').json()['revenue'])
        # 90,000 for April 2026. Last year's 80,000 is loaded and below the
        # display floor; secondary sales are reported in the notes and never
        # added to either.
        self.assertEqual(rev, 90000, 'secondary sales were added to primary')


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

    def test_last_year_cannot_be_reached_by_asking_for_it(self):
        """A window naming April 2025 does not bring April 2025 back. The
        floor is not a default to be overridden from the URL -- last year is
        loaded for growth arithmetic, and the moment it can be put on screen
        by typing a date, it is on screen."""
        self._two_years()
        d = Client().get('/api/sales/overview/?month_from=2025-04&month_to=2027-03').json()
        self.assertEqual(float(d['revenue']), 60000)       # this year alone
        self.assertEqual(d['filters']['display_from'], '2026-04')

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
        # Stored exactly as written, FG prefix or not. The SKU tile counts
        # only FG codes, but the importer does not edit what it was given.
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
            # Two more legitimate cases beside a single sheet: 'Both
            # sheets:' for the election BETWEEN them, which belongs to
            # neither alone, and 'This workbook:' for what was read out of
            # the file and what was left alone in it.
            self.assertTrue(m.startswith('PRI SALES DUMP:')
                            or m.startswith('YTD,AOP vs.ACH:')
                            or m.startswith('Both sheets:')
                            or m.startswith('This workbook:'), m)


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

    def test_people_are_counted_by_their_id_not_by_their_name(self):
        """Two spellings of one RSM is one person; two people sharing a name
        is two. Counted on the name, this strip was wrong in both directions
        at once."""
        SalesRecord.objects.all().delete()
        upload(aop_workbook([
            aop_row(**{'GTR HEAD': 'Anil Mehra', 'APIS ID': 'AP100'}),
            aop_row(**{'GTR HEAD': 'ANIL  MEHRA', 'APIS ID': 'AP100'}),
            aop_row(**{'GTR HEAD': 'Anil Mehra', 'APIS ID': 'AP777'}),
        ]))
        counts = {c['level']: c['count'] for c in self.org()['level_counts']}
        self.assertEqual(counts['rsm'], 2)

    def test_a_person_on_both_files_is_one_person(self):
        """The dump carries no ID columns, so its rows fall back to the name.
        Counting the fallback beside the ID reported the same RSM twice."""
        SalesRecord.objects.all().delete()
        upload(aop_workbook([aop_row(**{'GTR HEAD': 'Anil Mehra',
                                        'APIS ID': 'AP100'})]))
        upload(a_workbook([a_row(**{'RSM Name': 'Anil Mehra',
                                    'Order Date': '2026-04-05'})]))
        counts = {c['level']: c['count'] for c in self.org()['level_counts']}
        self.assertEqual(counts['rsm'], 1)

    def test_customers_are_counted_the_way_the_headline_tile_counts_them(self):
        """One account spelled two ways is one customer: it has one code.
        This panel read the name and disagreed with the tile above it."""
        SalesRecord.objects.all().delete()
        upload(a_workbook([
            a_row(**{'Customer No.': 'C-1', 'Customer Name': 'Sharma Agency', 'Sales Order No.': 'SO-1'}),
            a_row(**{'Customer No.': 'C-1', 'Customer Name': 'SHARMA AGENCIES', 'Sales Order No.': 'SO-2'}),
            a_row(**{'Customer No.': 'C-2', 'Customer Name': 'Verma Stores', 'Sales Order No.': 'SO-3'}),
        ]))
        self.assertEqual(self.org()['totals']['customers'], 2)

    def test_coverage_is_the_places_reached_not_a_sum_of_counts(self):
        """Two ASMs both selling in Delhi is ONE state the branch covers.
        Added up per group instead, one head was reported as covering 55
        sub-regions, which is more than the company has."""
        SalesRecord.objects.all().delete()
        upload(a_workbook([
            a_row(**{'RSM Name': 'Anil Mehra', 'ASM Name': 'Vikas Gupta',
                     'Cust.State Code': '07', 'Sales Order No.': 'SO-1'}),
            a_row(**{'RSM Name': 'Anil Mehra', 'ASM Name': 'Ramesh Singh',
                     'Cust.State Code': '07', 'Sales Order No.': 'SO-2'}),
        ]))
        top = self.org()['tree'][0]
        self.assertEqual(top['name'], 'Anil Mehra')
        self.assertEqual(top['states'], 1)
        for child in top['children']:
            self.assertLessEqual(child['states'], top['states'],
                                 'a branch covers less ground than its parent')

    def test_the_response_says_which_levels_the_invoice_file_reaches(self):
        """Customers and SKUs are invoice facts. If the dump carries no head
        column then every head shows nought of both -- which is a claim that
        nobody bought anything, not an admission that this file cannot say.
        The screen has to be able to tell the two apart."""
        d = self.org()
        reach = d['detail_reach']
        self.assertEqual(reach['levels_named'], ['rsm', 'asm'])
        self.assertNotIn('sales_head', reach['levels_named'])
        self.assertGreater(reach['lines'], 0)

    def test_nobody_appears_as_a_branch_at_nought(self):
        """The two files spell the organisation differently, and where the
        review sheet owns a month the dump's rows for it are deliberately
        zeroed so the same rupee is not counted twice. Those zeroed rows
        still name an ASM, so every dump spelling the sheet did not share
        arrived as its own branch at Rs 0 -- beside colleagues at tens of
        crores, with 73 customers written underneath it. Read straight, that
        said the man sold nothing."""
        SalesRecord.objects.all().delete()
        upload(aop_workbook([aop_row(**{'GTR HEAD': 'Anil Mehra',
                                        'REPORT.INCHARGE': 'Vikas Gupta'})]))
        upload(a_workbook([
            a_row(**{'RSM Name': 'Anil Mehra', 'ASM Name': 'Vikas Gupta',
                     'Order Date': '2026-04-05', 'Customer No.': 'C-1'}),
            # Spelled only the dump's way -- no such person on the sheet.
            a_row(**{'RSM Name': 'Hariom', 'ASM Name': 'Hariom',
                     'Order Date': '2026-04-05', 'Customer No.': 'C-2'}),
        ]))

        def walk(nodes):
            for n in nodes:
                yield n
                yield from walk(n.get('children') or [])

        d = self.org()
        empty = [n['name'] for n in walk(d['tree'])
                 if not n['revenue'] and not n['target']]
        self.assertEqual(empty, [], f'branches at nought: {empty}')
        self.assertNotIn('Hariom', [n['name'] for n in walk(d['tree'])])

    def test_detail_that_matches_no_branch_still_counts_in_the_total(self):
        """Dropping the branch must not drop the customer. The figure cannot
        be attributed to a person, so it belongs in the total and nowhere
        else -- and the response says how much of it there is."""
        SalesRecord.objects.all().delete()
        upload(aop_workbook([aop_row(**{'GTR HEAD': 'Anil Mehra',
                                        'REPORT.INCHARGE': 'Vikas Gupta'})]))
        upload(a_workbook([
            a_row(**{'RSM Name': 'Anil Mehra', 'ASM Name': 'Vikas Gupta',
                     'Order Date': '2026-04-05', 'Customer No.': 'C-1'}),
            a_row(**{'RSM Name': 'Hariom', 'ASM Name': 'Hariom',
                     'Order Date': '2026-04-05', 'Customer No.': 'C-2'}),
        ]))
        d = self.org()
        self.assertEqual(d['totals']['customers'], 2)
        self.assertGreater(d['detail_reach']['unplaced'], 0)

    def test_a_dump_on_its_own_still_builds_its_tree(self):
        """The rule is "no revenue and no plan", not "came from the dump".
        An install with no review sheet has real money on its dump rows."""
        SalesRecord.objects.all().delete()
        upload(a_workbook([a_row(**{'RSM Name': 'Anil Mehra',
                                    'ASM Name': 'Vikas Gupta'})]))
        d = self.org()
        self.assertTrue(d['tree'])
        self.assertEqual(d['tree'][0]['name'], 'Anil Mehra')

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
    sheet, which is monthly aggregates: no order, no customer, no SKU.
    Dividing all the revenue by the orders that do exist is a ratio between
    two different populations, and on the real file it reported an average
    order of Rs 30 lakh against a true Rs 1.8 lakh.

    An order is a Sales Order No. One order can be invoiced more than once,
    so counting invoices counted the same order twice.
    """

    def setUp(self):
        upload(a_workbook([
            a_row(**{'Sales Order No.': 'SO-1', 'Invoice No.': 'INV-1',
                     'Taxable Amount': 10000}),
            a_row(**{'Sales Order No.': 'SO-2', 'Invoice No.': 'INV-2',
                     'Taxable Amount': 10000}),
        ]))
        # A row with no order on it, standing for a review-sheet month.
        SalesRecord.objects.create(
            upload=SalesUpload.objects.first(),
            order_date=date(2026, 4, 1), period=date(2026, 4, 1),
            sales_order_no='', invoice_no='', net_amount=980000, quantity=0)

    def overview(self):
        return Client().get('/api/sales/overview/').json()

    def test_a_row_with_no_order_number_is_not_an_order(self):
        """Counting distinct order numbers across everything also counts the
        empty string: one phantom order standing in for every aggregate row
        in the file."""
        self.assertEqual(self.overview()['orders'], 2)

    def test_average_order_value_ignores_rows_with_no_order(self):
        """20,000 over two orders, not 1,000,000 over two."""
        self.assertEqual(self.overview()['avg_order_value'], 10000)

    def test_the_headline_still_counts_every_rupee(self):
        """Only the per-order figures narrow; revenue is all of it."""
        self.assertEqual(self.overview()['revenue'], 1000000)

    def test_it_says_how_much_of_the_money_is_invoiced(self):
        d = self.overview()
        self.assertEqual(d['invoiced_revenue'], 20000)
        self.assertEqual(d['invoiced_pct'], 2.0)


class OneOrderInvoicedTwiceIsStillOneOrder(TestCase):
    """The order count is distinct Sales Order No., which is what the
    business means by an order. Counting invoice numbers instead split one
    order into as many orders as it had invoices, and made the average order
    value smaller than it really is."""

    def test_two_invoices_against_one_order_count_once(self):
        upload(a_workbook([
            a_row(**{'Sales Order No.': 'SO-1', 'Invoice No.': 'INV-1',
                     'Taxable Amount': 10000}),
            a_row(**{'Sales Order No.': 'SO-1', 'Invoice No.': 'INV-2',
                     'Taxable Amount': 10000}),
        ]))
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(d['orders'], 1, 'one order was counted as two')
        self.assertEqual(d['avg_order_value'], 20000,
                         'the order value was halved across its two invoices')



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

    def test_the_trend_line_starts_at_the_display_floor(self):
        """It used to open on last May and run a year of actuals against an
        AOP of zero, because last year has no plan in this file. Twelve
        months at 0% of plan reads as a broken dashboard."""
        self._two_years()
        d = Client().get('/api/sales/trend/').json()
        months = [str(r['period']) for r in d['results']]
        self.assertTrue(months, 'the trend came back empty')
        self.assertTrue(all(m >= '2026-04' for m in months),
                        f'something before the floor is on the chart: {months}')

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
    """Two files, and the review sheet answers for every month it covers.

    The sheet is month-wise and year-wise: twelve columns a year, no days in
    it at all, and its YTD ACH column is the figure the business reads. It is
    also the only file carrying the target. The invoice dump is one row per
    invoice line, dated to the day, but covering a single month.

    The window is a range of MONTHS, so the sheet can always answer it. Day
    ranges used to be offered and quietly swapped which file replied -- the
    same stretch of time asked by month and by day came back with two
    different figures, and the day version had no target at all. A month
    range asked of one file gives one answer.
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

    def test_a_month_range_is_answered_by_the_review_sheet(self):
        self._both()
        d = Client().get('/api/sales/overview/'
                         '?month_from=2026-04&month_to=2026-04').json()
        self.assertEqual(float(d['revenue']), 90000,
                         "the dump answered a month the sheet speaks for")
        self.assertEqual(d['filters']['source'], 'review_sheet')

    def test_a_month_range_carries_the_target_with_it(self):
        """The point of reading the sheet: the dump has no target at all, so
        a window it answered could show achievement against nothing."""
        self._both()
        d = Client().get('/api/sales/overview/'
                         '?month_from=2026-04&month_to=2026-04').json()
        self.assertGreater(float(d['target']), 0,
                           'a month range came back with no target')

    def test_a_saved_link_with_full_dates_still_resolves_to_its_months(self):
        """Links were shared while the screen asked by day. They must not
        break, and they must answer as the month they fall in."""
        self._both()
        d = Client().get('/api/sales/overview/?from=2026-04-04&to=2026-04-06').json()
        self.assertEqual(float(d['revenue']), 90000,
                         'a day range did not widen to its month')
        self.assertEqual(d['filters']['month_from'], '2026-04')
        self.assertEqual(d['filters']['month_to'], '2026-04')

    def test_the_window_is_reported_as_months(self):
        self._both()
        f = Client().get('/api/sales/overview/'
                         '?month_from=2026-04&month_to=2026-09').json()['filters']
        self.assertEqual((f['month_from'], f['month_to']), ('2026-04', '2026-09'))
        # Still given as dates too, so anything reading the old keys keeps
        # working -- and the end is the LAST day of the month, not the 1st.
        self.assertEqual((f['from'], f['to']), ('2026-04-01', '2026-09-30'))

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
    comparison covering six. A day range reintroduced it in a new form: the
    money came off the dump and the comparison beneath it off the sheet.

    Asking by month settles it. One file answers the headline AND the
    comparison, and because the sheet carries last year's months beside this
    year's, a month window has a real year-on-year figure instead of none.
    """

    def _both(self):
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-001'})]))

    def test_a_month_window_does_have_same_months_last_year(self):
        """What asking by month buys: the sheet holds Apr-25 beside Apr-26,
        so April against last April is a figure both files agree on. A day
        range could not answer this at all."""
        self._both()
        d = Client().get('/api/sales/overview/'
                         '?month_from=2026-04&month_to=2026-04').json()
        self.assertIsNotNone(d['vs_last_year'],
                             'a month window came back with no year-on-year')
        self.assertEqual(d['vs_last_year']['last_year'], 80000)

    def test_by_year_it_still_does(self):
        self._both()
        d = Client().get('/api/sales/overview/').json()
        self.assertIsNotNone(d['vs_last_year'])
        self.assertEqual(d['vs_last_year']['last_year'], 80000)

    def test_the_prior_period_comes_off_the_same_file_as_the_headline(self):
        """Both read the sheet now, so there is no file boundary for the
        comparison to fall across."""
        self._both()
        d = Client().get('/api/sales/overview/'
                         '?month_from=2026-04&month_to=2026-04').json()
        self.assertEqual(d['filters']['source'], 'review_sheet')
        self.assertEqual(float(d['revenue']), 90000,
                         'the headline did not come off the sheet')


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


class TheWindowOffersOnlyMonthsThatExist(TestCase):
    """The pickers are filled from the files, not from a calendar.

    A free month input let somebody choose a month neither file covers, and
    the empty dashboard that came back looks exactly like a bad month of
    trading. A list of the months actually present cannot say that.

    "All history" is gone from the same control. What it showed was both
    financial years of the review sheet added together -- eighteen months of
    two years standing where the business reads its year to date, which is
    the figure that appears in neither file. The same width is still
    reachable by naming the months, where the screen says which ones.
    """

    def test_the_filters_endpoint_lists_the_months_present(self):
        upload(aop_workbook([aop_row()]))
        months = Client().get('/api/sales/filters/').json()['months']
        self.assertIn('2026-04', months)

    def test_a_month_below_the_display_floor_is_not_offered(self):
        """Last year is loaded for growth arithmetic. Offering it in the
        picker is a way around the floor that the picker itself provides --
        choose April 2025 and the year the dashboard stops showing is back on
        screen."""
        upload(aop_workbook([aop_row()]))
        months = Client().get('/api/sales/filters/').json()['months']
        self.assertNotIn('2025-04', months)
        self.assertTrue(all(m >= '2026-04' for m in months), months)

    def test_every_month_offered_is_one_a_file_covers(self):
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row()]))
        months = set(Client().get('/api/sales/filters/').json()['months'])
        real = {d.strftime('%Y-%m') for d in
                SalesRecord.objects.exclude(period=None)
                                   .values_list('period', flat=True)
                if d.strftime('%Y-%m') >= '2026-04'}
        self.assertEqual(months, real, 'a month was offered that no file has')

    def test_there_is_no_way_to_turn_the_window_off(self):
        """span=all used to do it. The parameter is gone, so a stray one in a
        saved link must be ignored rather than quietly widening the figure to
        both financial years."""
        upload(aop_workbook([aop_row()]))
        plain = Client().get('/api/sales/overview/').json()
        stray = Client().get('/api/sales/overview/?span=all').json()
        self.assertEqual(float(stray['revenue']), float(plain['revenue']),
                         'span=all still widened the window')
        self.assertEqual(float(plain['revenue']), 90000,
                         'the default window is not the financial year')


class TheFilterListComesFromTheFileThatAnswers(TestCase):
    """Region and Sub-Region are the sheet's words for two columns the dump
    also writes to, in its own vocabulary.

    The sheet heads them REGION and Sub-Region and fills them with GTR01 and
    CHD (TRI). The dump's Zone and state columns land in the same two fields
    as North and a state code. Offering both at once put two vocabularies in
    one dropdown -- the same fault the channel had when DOMESTIC sat beside
    GT -- and picking a dump value filtered money that comes off the sheet,
    emptying the dashboard with nothing to say why.

    So the file that answers a figure supplies the values you may filter it
    by. Customer, SKU and city exist only on an invoice and still read the
    dump.
    """

    def _both(self):
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-001'})]))

    def test_region_offers_the_sheets_words_not_the_dumps(self):
        self._both()
        opts = Client().get('/api/sales/filters/').json()
        sheet_zones = set(SalesRecord.objects
                          .exclude(source=SalesRecord.SOURCE_INVOICE)
                          .exclude(zone='').values_list('zone', flat=True))
        self.assertTrue(sheet_zones, 'fixture has no sheet zone to check')
        self.assertEqual(set(opts['zone']), sheet_zones,
                         "the dump's zone vocabulary reached the Region list")

    def test_a_dump_only_dimension_still_reads_the_dump(self):
        """Customers are on no sheet, so scoping them to it would empty a
        dropdown that was working."""
        self._both()
        opts = Client().get('/api/sales/filters/').json()
        self.assertIn('Sharma Traders', opts['customer_name'])

    def test_every_value_offered_actually_filters_to_something(self):
        """The point of the rule: nothing in a dropdown returns an empty
        dashboard."""
        self._both()
        opts = Client().get('/api/sales/filters/').json()
        for field in ('zone', 'state', 'channel'):
            for value in opts[field]:
                d = Client().get(f'/api/sales/overview/?{field}={value}').json()
                self.assertGreater(
                    float(d['revenue']), 0,
                    f'{field}={value} was offered but returns no revenue')


class TheFourTilesAreCountedOffTheColumnsTheBusinessNamed(TestCase):
    """SKUs, Customers, Orders and Quantity, each from one named column of
    the dump, each counted distinct, and all of them after the same
    exclusions the money gets.

    Every one of these was counted off something close but not equal:
    customers by name rather than code, SKUs over the whole Item Code column
    rather than finished goods, orders by invoice rather than by order. Each
    difference is small on one row and large over a file, which is why the
    tiles never agreed with what the business counts by hand.
    """

    def rows(self):
        return [
            # Two orders, one customer, two finished goods.
            a_row(**{'Sales Order No.': 'SO-1', 'Customer No.': 'C-1',
                     'Item Code': 'FG-001', 'Quantity': 10}),
            a_row(**{'Sales Order No.': 'SO-2', 'Customer No.': 'C-1',
                     'Item Code': 'FG-002', 'Quantity': 5}),
            # Same order invoiced twice -- still one order.
            a_row(**{'Sales Order No.': 'SO-2', 'Customer No.': 'C-1',
                     'Invoice No.': 'INV-9', 'Item Code': 'FG-002',
                     'Quantity': 1}),
            # Not a finished good: packaging riding on a sales invoice.
            a_row(**{'Sales Order No.': 'SO-3', 'Customer No.': 'C-2',
                     'Item Code': 'PKG-500', 'Quantity': 100}),
        ]

    def test_skus_count_finished_goods_only(self):
        upload(a_workbook(self.rows()))
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(d['skus'], 2, 'a non-FG item code was counted as a SKU')

    def test_customers_are_counted_by_code_not_by_name(self):
        upload(a_workbook([
            a_row(**{'Customer No.': 'C-1', 'Customer Name': 'Sharma Traders'}),
            a_row(**{'Customer No.': 'C-1', 'Customer Name': 'SHARMA TRADERS '}),
        ]))
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(d['customers'], 1,
                         'one customer spelt two ways counted twice')

    def test_orders_are_distinct_sales_order_numbers(self):
        upload(a_workbook(self.rows()))
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(d['orders'], 3, 'SO-2 was counted once per invoice')

    def test_quantity_is_the_quantity_column_added_up(self):
        upload(a_workbook(self.rows()))
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(float(d['quantity']), 116)

    # -- the exclusions apply to all four --------------------------------

    def test_b2b_and_export_are_left_out(self):
        """Neither is primary sales, and the sheet that carries the plan has
        neither in it -- so counting them put customers and orders on screen
        the target beside them was never set against."""
        upload(a_workbook([
            a_row(**{'Sales Order No.': 'SO-1', 'Customer No.': 'C-1',
                     'Item Code': 'FG-001'}),
            a_row(**{'Sales Order No.': 'SO-2', 'Customer No.': 'C-2',
                     'Item Code': 'FG-002', 'Zone': 'B2B'}),
            a_row(**{'Sales Order No.': 'SO-3', 'Customer No.': 'C-3',
                     'Item Code': 'FG-003', 'Zone': 'EXPORT'}),
        ]))
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(d['orders'], 1, 'B2B or Export reached the order count')
        self.assertEqual(d['customers'], 1)
        self.assertEqual(d['skus'], 1)

    def test_the_zone_match_does_not_care_about_case(self):
        """The dump writes these by hand. An exact match let one row reading
        "Export" through while excluding "EXPORT" beside it."""
        upload(a_workbook([
            a_row(**{'Sales Order No.': 'SO-1', 'Customer No.': 'C-1',
                     'Item Code': 'FG-001'}),
            a_row(**{'Sales Order No.': 'SO-2', 'Customer No.': 'C-2',
                     'Item Code': 'FG-002', 'Zone': 'Export'}),
            a_row(**{'Sales Order No.': 'SO-3', 'Customer No.': 'C-3',
                     'Item Code': 'FG-003', 'Zone': 'b2b'}),
        ]))
        self.assertEqual(Client().get('/api/sales/overview/').json()['orders'], 1)

    def test_an_excluded_remark_is_left_out_of_the_tiles_too(self):
        upload(a_workbook([
            a_row(**{'Sales Order No.': 'SO-1', 'Customer No.': 'C-1',
                     'Item Code': 'FG-001'}),
            a_row(**{'Sales Order No.': 'SO-2', 'Customer No.': 'C-2',
                     'Item Code': 'FG-002', 'V-REMARS': 'SCHEME CN'}),
            a_row(**{'Sales Order No.': 'SO-3', 'Customer No.': 'C-3',
                     'Item Code': 'FG-003', 'V-REMARS': 'NOT A PART OF SALES'}),
        ]))
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(d['orders'], 1, 'an excluded line reached the tiles')
        self.assertEqual(d['customers'], 1)

    # -- and they move with the filters ----------------------------------

    def test_the_tiles_narrow_with_a_filter(self):
        """The whole point: filter the screen and these have to agree with
        what is left, or the tiles describe a different population from the
        revenue above them."""
        upload(a_workbook([
            a_row(**{'Sales Order No.': 'SO-1', 'Customer No.': 'C-1',
                     'Item Code': 'FG-001', 'Zone': 'North', 'Quantity': 10}),
            a_row(**{'Sales Order No.': 'SO-2', 'Customer No.': 'C-2',
                     'Item Code': 'FG-002', 'Zone': 'South', 'Quantity': 7}),
        ]))
        d = Client().get('/api/sales/overview/?zone=North').json()
        self.assertEqual(d['orders'], 1)
        self.assertEqual(d['customers'], 1)
        self.assertEqual(d['skus'], 1)
        self.assertEqual(float(d['quantity']), 10)


class RegionIsNotOfferedTwice(TestCase):
    """The sheet's REGION is read into `zone` and labelled Region on screen.

    A separate `region` dimension existed for a column neither primary file
    has, so the dashboard showed Region in the filter row, populated from the
    sheet, AND in the "no column for these" list telling the reader to add a
    column their file already has. One name, two answers, both on screen at
    once.
    """

    def test_region_is_not_offered_as_its_own_breakdown(self):
        upload(aop_workbook([aop_row()]))
        d = Client().get('/api/sales/filters/').json()
        self.assertNotIn('region', d['dimensions'],
                         'Region is offered separately from Zone again')
        self.assertNotIn('region', d.get('absent_dimensions', []),
                         'Region is still being reported as a missing column')

    def test_the_sheets_region_is_reachable_as_zone(self):
        """Removing the duplicate must not lose the column -- it is the one
        the sheet actually fills."""
        upload(aop_workbook([aop_row()]))
        d = Client().get('/api/sales/filters/').json()
        self.assertIn('zone', d['available_dimensions'])
        self.assertTrue(d['zone'], 'the sheet REGION values are not offered')

    def test_a_breakdown_by_zone_still_answers(self):
        upload(aop_workbook([aop_row()]))
        rows = Client().get('/api/sales/breakdown/?dim=zone').json()['results']
        self.assertTrue(rows, 'the Region breakdown came back empty')


class SubRegionKeepsTheSheetsOwnWords(TestCase):
    """The sheet's Sub-Region is MH-1, KA-5, Lulu, More -- selling
    territories, not states.

    The importer keeps them verbatim in `subzone` and puts a state name
    DERIVED from the code into `state` (MH-1 -> Maharashtra), so that this
    sheet and the invoice dump name states the same way. Labelling `state`
    Sub-Region therefore showed the derivation in place of the sheet's own
    words -- Andhra Pradesh where the file says AP-1.
    """

    def test_the_sheets_sub_region_is_stored_verbatim(self):
        # A code where the territory and the state it derives to are plainly
        # different -- the fixture's default 'Delhi' is both, which would
        # pass this test without proving anything.
        upload(aop_workbook([aop_row(**{'Sub-Region': 'MH-1'})]))
        row = SalesRecord.objects.filter(source='plan').first()
        self.assertEqual(row.subzone, 'MH-1',
                         'the sheet Sub-Region was not kept as written')
        self.assertNotEqual(row.state, 'MH-1',
                            'state should hold the derived state name')

    def test_the_sub_region_list_holds_no_dump_vocabulary(self):
        """subzone is filled by BOTH files -- the dump writes Delhi NCR into
        it -- so without scoping the list would mix the two."""
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row(**{'Subzone': 'Delhi NCR'})]))
        offered = set(Client().get('/api/sales/filters/').json()['subzone'])
        from_sheet = set(SalesRecord.objects.filter(source='plan')
                         .exclude(subzone='')
                         .values_list('subzone', flat=True))
        self.assertEqual(offered, from_sheet)
        self.assertNotIn('Delhi NCR', offered,
                         "the dump's subzone vocabulary reached Sub-Region")


class OnlyB2BAndExportLeaveTheFigure(TestCase):
    """The zones that are not primary sales, and only those.

    This was briefly a whitelist derived from the review sheet -- keep the
    zones the sheet has a plan for. It reads well and it is wrong: it also
    dropped CPC, which the sheet has no plan for and which the business
    nonetheless counts as sales. The figure has to be the one arrived at by
    hand, so the rule is the two zones named and nothing else.
    """

    def _rows(self):
        upload(a_workbook([
            a_row(**{'Zone': 'GTR01', 'Sales Order No.': 'SO-1',
                     'Customer No.': 'C-1', 'Item Code': 'FG-1'}),
            a_row(**{'Zone': 'CPC', 'Sales Order No.': 'SO-2',
                     'Customer No.': 'C-2', 'Item Code': 'FG-2'}),
            a_row(**{'Zone': 'MT', 'Sales Order No.': 'SO-3',
                     'Customer No.': 'C-3', 'Item Code': 'FG-3'}),
            a_row(**{'Zone': 'B2B', 'Sales Order No.': 'SO-4',
                     'Customer No.': 'C-4', 'Item Code': 'FG-4'}),
            a_row(**{'Zone': 'EXPORT', 'Sales Order No.': 'SO-5',
                     'Customer No.': 'C-5', 'Item Code': 'FG-5'}),
        ]))

    def test_b2b_and_export_leave_and_nothing_else_does(self):
        self._rows()
        d = Client().get('/api/sales/overview/').json()
        self.assertEqual(d['orders'], 3, 'GTR01, CPC and MT must all count')
        self.assertEqual(d['customers'], 3)
        self.assertEqual(d['skus'], 3)

    def test_cpc_is_counted(self):
        """It has no plan against it in the review sheet, and it is still a
        sale. Dropping it put the tiles 26 orders below the hand count."""
        self._rows()
        d = Client().get('/api/sales/overview/?zone=CPC').json()
        self.assertEqual(d['orders'], 1, 'CPC was dropped from the figure')
        for gone in ('B2B', 'EXPORT'):
            self.assertEqual(
                Client().get(f'/api/sales/overview/?zone={gone}').json()['orders'],
                0, f'{gone} is still being counted')

    def test_the_match_does_not_care_about_case(self):
        upload(a_workbook([
            a_row(**{'Zone': 'GTR01', 'Sales Order No.': 'SO-1',
                     'Customer No.': 'C-1', 'Item Code': 'FG-1'}),
            a_row(**{'Zone': 'Export', 'Sales Order No.': 'SO-2',
                     'Customer No.': 'C-2', 'Item Code': 'FG-2'}),
            a_row(**{'Zone': 'b2b', 'Sales Order No.': 'SO-3',
                     'Customer No.': 'C-3', 'Item Code': 'FG-3'}),
        ]))
        self.assertEqual(Client().get('/api/sales/overview/').json()['orders'], 1)

    def test_the_comparison_uses_the_same_rule_as_the_headline(self):
        """money_base feeds the prior period and the year-on-year line. When
        it applied a different zone rule, the comparison counted a zone the
        headline had dropped and the growth between them was an artefact."""
        from sales.views.filters import in_plan_scope
        self._rows()
        scoped = in_plan_scope(SalesRecord.objects.filter(source='invoice'))
        self.assertEqual(
            set(scoped.values_list('zone', flat=True).distinct()),
            {'GTR01', 'CPC', 'MT'})


# ── the forecast, and whether it can answer for itself ──────────────────────
class TheForecastExplainsItself(TestCase):
    """A projection nobody can account for is not usable in a review. Every
    figure the forecast panel shows has to be traceable to either the data or
    a stated rule."""

    def setUp(self):
        # 19 months of actuals -- all of FY26 plus April to October of FY27 --
        # against a full twelve months of plan. That leaves November to March
        # as months the plan reaches and the history does not, which is
        # exactly the stretch the AOP line exists to cover.
        row = {}
        for i, m in enumerate(FY26):
            row[m] = 50000 + i * 1000
        for i, m in enumerate(FY27[:7]):
            row[m] = 62000 + i * 1000
        for i, m in enumerate(FY27):
            row[f'{m} AOP'] = 70000 + i * 1000
        upload(aop_workbook([aop_row(**row)]))

    def fc(self, **params):
        q = '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(f'/api/sales/forecast/{"?" + q if q else ""}').json()

    def test_the_plan_is_drawn_across_the_forecast_not_only_the_history(self):
        d = self.fc(periods=6)
        future = [p for p in d['points'] if p['aop'] is not None]
        self.assertTrue(future, 'no AOP on any forecast month')
        self.assertEqual([p['label'] for p in future],
                         ['Nov 2026', 'Dec 2026', 'Jan 2027', 'Feb 2027', 'Mar 2027'])

    def test_the_forecast_stays_with_the_plan_instead_of_running_off(self):
        """The whole point of anchoring. A pure extrapolation reads the slope
        of the months it was given and keeps going; against a rising half
        year that projected a second half well clear of anything the business
        had planned for. Every month now sits within touching distance of its
        own AOP."""
        for p in self.fc(periods=6)['points']:
            self.assertIsNotNone(p['aop'])
            ratio = p['value'] / p['aop']
            self.assertGreater(ratio, 0.5, f"{p['label']} is miles under plan")
            self.assertLess(ratio, 1.5, f"{p['label']} has run away from plan")

    def test_the_plan_carries_the_month_shape(self):
        """A month the plan puts higher than its neighbour is forecast
        higher. That shape is the plan's own -- the festive quarter, the
        launch calendar -- not a curve fitted to a few months of history."""
        pts = self.fc(periods=6)['points']
        aop_order = [p['label'] for p in sorted(pts, key=lambda x: x['aop'])]
        fc_order = [p['label'] for p in sorted(pts, key=lambda x: x['value'])]
        self.assertEqual(aop_order, fc_order)

    def test_the_level_is_the_rate_we_have_been_running_at(self):
        """Not the plan copied back. If the business has been achieving 90%
        of plan, the forecast is 90% of plan -- and halving every actual
        halves it."""
        rate = self.fc(periods=6)['run_rate']['rate_pct']
        SalesRecord.objects.filter(source=SalesRecord.SOURCE_INVOICE).delete()
        SalesRecord.objects.filter(measured_amount__gt=0).update(
            measured_amount=F('measured_amount') / 2,
            net_amount=F('net_amount') / 2)
        halved = self.fc(periods=6)['run_rate']['rate_pct']
        self.assertAlmostEqual(halved, rate / 2, places=0)

    def test_the_rate_is_shown_with_the_months_it_was_read_from(self):
        """A percentage nobody can check is not usable in a review."""
        rr = self.fc(periods=6)['run_rate']
        self.assertEqual(rr['months'], len(rr['by_month']))
        self.assertGreaterEqual(rr['high_pct'], rr['rate_pct'])
        self.assertLessEqual(rr['low_pct'], rr['rate_pct'])
        self.assertGreaterEqual(rr['best']['pct'], rr['worst']['pct'])

    def test_a_month_nobody_set_a_plan_for_is_not_counted_as_a_miss(self):
        from sales.forecasting import run_rate_against_plan
        from datetime import date as _d
        rate, _half, ratios, _capped = run_rate_against_plan([
            (_d(2026, 4, 1), 90.0, 100.0),
            (_d(2026, 5, 1), 50.0, 0.0),      # no plan set -- skipped
            (_d(2026, 6, 1), 90.0, 100.0),
        ])
        self.assertEqual(len(ratios), 2)
        self.assertAlmostEqual(rate, 0.9, places=6)

    def test_the_rate_is_a_ratio_of_sums_not_an_average_of_ratios(self):
        from sales.forecasting import run_rate_against_plan
        from datetime import date as _d
        """A month carrying a small plan and an ordinary month's invoicing
        produces a ratio of three or four. Averaged in beside the rest it
        dragged the whole rate up on the strength of the month that mattered
        least. Summing the money on each side first gives every rupee one
        vote instead of giving every month one vote -- which is also what
        the business reads off its own sheet as YTD ACH over YTD AOP."""
        rate, _half, _r, _c = run_rate_against_plan([
            (_d(2026, 4, 1), 90.0, 100.0),
            (_d(2026, 5, 1), 90.0, 100.0),
            (_d(2026, 6, 1), 60.0, 5.0),      # tiny plan, ordinary month
        ])
        # Mean of ratios would be (0.9 + 0.9 + 12.0) / 3 = 466%.
        self.assertLess(rate * 100, 150)

    def test_without_a_plan_it_falls_back_to_extrapolating_the_history(self):
        SalesRecord.objects.filter(target_amount__gt=0).update(target_amount=0)
        d = self.fc(periods=6)
        self.assertNotEqual(d['method'], 'plan-anchored')
        self.assertIn('Holt', d['spec']['name'])

    def test_forecast_and_plan_are_totalled_over_the_same_months(self):
        """The plan stops in March, and so does the forecast: past the plan
        there is no month shape to anchor to, and a straight line drawn out
        of the end of the year is not a forecast."""
        d = self.fc(periods=6)
        v = d['vs_aop']
        self.assertEqual(v['months'], 5)
        self.assertEqual(len(d['points']), 5, 'forecast ran past the plan')
        covered = [p for p in d['points'] if p['aop'] is not None]
        self.assertAlmostEqual(v['aop_total'], sum(p['aop'] for p in covered), places=2)
        self.assertAlmostEqual(v['forecast_total'], sum(p['value'] for p in covered), places=2)
        self.assertAlmostEqual(v['gap'], v['forecast_total'] - v['aop_total'], places=2)

    def test_a_quantity_forecast_carries_no_plan_line(self):
        """The plan is in rupees. Anchoring a forecast in cases to it would
        be scaling one unit by another, and drawing it beside one would be a
        line with no meaning on that axis."""
        d = self.fc(periods=6, metric='quantity')
        self.assertNotEqual(d['method'], 'plan-anchored')
        self.assertTrue(all(p['aop'] is None for p in d['points']))
        self.assertNotIn('vs_aop', d)

    def test_the_panel_can_say_which_model_ran_and_why(self):
        d = self.fc(periods=6)
        spec = d['spec']
        self.assertEqual(spec['name'], 'Run rate against AOP')
        self.assertEqual(spec['seasonality'], 'from the AOP')
        # Bullets, not paragraphs: the panel is scanned by somebody being
        # asked a question in a review, and prose is the one shape that
        # cannot be scanned.
        for key in ('why', 'reads', 'seasonality_why', 'band', 'floor'):
            self.assertIsInstance(spec[key], list, key)
            self.assertTrue(spec[key], key)
            for line in spec[key]:
                self.assertLess(len(line), 180, f'{key}: {line}')

    def test_every_forecast_month_names_what_is_in_it(self):
        for p in self.fc(periods=6)['points']:
            self.assertTrue(p['calendar'], f"{p['label']} has nothing beside it")

    def test_seasonality_is_only_claimed_once_there_is_enough_history(self):
        """A seasonal index off 19 months would be one observation per month
        dressed up as a pattern."""
        for p in self.fc(periods=6)['points']:
            self.assertNotIn('seasonal_index', p)

    def test_with_two_full_years_the_model_shows_its_seasonal_working(self):
        from sales.forecasting import forecast_series
        from datetime import date as _d
        pts, lift = [], {10: 1.6, 11: 1.4}      # a festive October and November
        for i in range(30):
            m = _d(2024 + (3 + i) // 12, (3 + i) % 12 + 1, 1)
            pts.append((m, 100000 * lift.get(m.month, 1.0)))
        d = forecast_series(pts, periods=6)
        self.assertEqual(d['method'], 'holt-winters')
        self.assertEqual(d['spec']['seasonality'], 'multiplicative')
        by_month = {p['period'][5:7]: p for p in d['points']}
        if '10' in by_month:
            self.assertGreater(by_month['10']['seasonal_pct'], 20,
                               'a 60% festive October was not reported as a lift')

    def test_the_month_window_narrows_the_view_not_the_model(self):
        """With Apr-Sep selected the forecast was fitted on six months of a
        rising ramp, which dropped it out of the seasonal model into a
        straight line and projected +78% on the next six. Two full years
        were loaded the whole time. A window says which months are being
        looked at, not which months the model may learn from."""
        full = self.fc(periods=6)
        narrowed = self.fc(periods=6, month_from='2026-04', month_to='2026-09')
        self.assertEqual(narrowed['history_months'], full['history_months'])
        self.assertEqual([p['value'] for p in narrowed['points']],
                         [p['value'] for p in full['points']])

    def test_a_dimension_filter_does_still_narrow_the_model(self):
        """A forecast for one region is a different series from a forecast
        for the company, and that is the question being asked."""
        d = self.fc(periods=6, zone='Nowhere')
        self.assertEqual(d['history_months'], 0)

    def test_the_panel_says_it_is_ignoring_the_month_filter(self):
        self.assertIn('not on the months selected', self.fc(periods=6)['window_note'])

    def test_the_band_is_explained_rather_than_just_drawn(self):
        """And explained for the model that actually ran. The plan-anchored
        band is the run rate's own variability, which does not compound with
        the horizon -- borrowing the widening from a model that is not being
        used would be a correction for an error this one does not make."""
        spec = self.fc(periods=6)['spec']
        band = ' '.join(spec['band'])
        self.assertIn('% of plan', band)
        self.assertIn('does not widen', band)
        self.assertIn('5%', ' '.join(spec['floor']))


# ── the blueprint's KPI rules ───────────────────────────────────────────────
class TheControlTowerRules(TestCase):
    """Red/Amber/Green and run rate, from the APIS AI Sales & Collection
    Control Tower blueprint, section 4 and section 7. Only the limbs that P1
    primary sales can actually answer -- collections, outstanding and the
    daily grain are not loaded."""

    def setUp(self):
        # Six months done, six months of plan still ahead of the business.
        row = {}
        for m in FY27[:6]:
            row[m] = 60000
        for m in FY27:
            row[f'{m} AOP'] = 100000
        upload(aop_workbook([aop_row(**row)]))

    def ov(self, **params):
        q = '&'.join(f'{k}={v}' for k, v in params.items())
        return self.client.get(f'/api/sales/overview/{"?" + q if q else ""}').json()

    # ── the bands ───────────────────────────────────────────────────────
    def test_the_bands_are_the_blueprints(self):
        from sales.status import rag
        self.assertEqual(rag(69.9), 'red')
        self.assertEqual(rag(70.0), 'amber')
        self.assertEqual(rag(99.9), 'amber')
        self.assertEqual(rag(100.0), 'green')
        self.assertEqual(rag(140.0), 'green')

    def test_the_gap_the_blueprint_leaves_at_ninety_to_a_hundred_is_amber(self):
        """The blueprint says RED under 70, AMBER 70-90, GREEN over 100, and
        names nothing for 90-100. A branch at 94% has to come out somewhere
        or every screen decides for itself. It is a watch: the blueprint's
        own GREEN is 'over 100% OR on/above required run-rate', so what it
        cares about is being on course, and 94% is not."""
        from sales.status import rag
        self.assertEqual(rag(94.0), 'amber')

    def test_no_plan_is_no_status_rather_than_a_pass_or_a_fail(self):
        """A branch nobody set an AOP for has not passed and has not failed.
        Green because nothing was asked of it, and red because it cleared
        nothing, are both inventions."""
        from sales.status import rag, band
        self.assertIsNone(rag(None))
        self.assertIsNone(band(None))

    def test_the_overview_carries_the_status_and_says_what_it_means(self):
        d = self.ov()
        self.assertEqual(d['status']['status'], 'red')     # 60% of plan
        self.assertIn('70', d['status']['meaning'])
        self.assertTrue(d['status']['label'])

    def test_one_rule_reaches_the_tree_and_the_breakdowns_alike(self):
        """A name in the org tree and the same name on a leaderboard cannot
        come out different colours."""
        org = self.client.get('/api/sales/org/').json()
        brk = self.client.get('/api/sales/breakdown/?dim=rsm').json()
        tree = {n['name']: n['status'] for n in org['tree']}
        for r in brk['results']:
            if r['name'] in tree and r.get('status'):
                self.assertEqual(r['status'], tree[r['name']], r['name'])

    # ── run rate ────────────────────────────────────────────────────────
    def test_the_run_rate_is_what_happened_over_the_months_it_happened_in(self):
        d = self.ov()
        basis = d['run_rate_basis']
        self.assertEqual(basis['months_elapsed'], 6)
        self.assertAlmostEqual(d['run_rate'],
                               d['achievement_basis']['revenue'] / 6, places=2)

    def test_the_required_rate_makes_up_the_shortfall_as_well(self):
        """The blueprint's "remaining target GAP / remaining periods", and
        gap is the operative word.

        Dividing the remaining PLAN instead forgave every rupee already
        missed: six months in at 60% of plan it said "needs 100,000 a
        month", and delivering exactly that lands the year 240,000 short --
        the same shortfall the screen reports two cards to the left.

        Fixture: 6 months done at 60,000 against 100,000 of plan each, and
        6 months of plan still to come. Owed: 1,200,000 planned less
        360,000 sold = 840,000, over 6 months = 140,000 a month."""
        d = self.ov()
        basis = d['run_rate_basis']
        self.assertEqual(basis['months_ahead'], 6)
        self.assertAlmostEqual(basis['full_plan'], 1200000, places=2)
        self.assertAlmostEqual(basis['still_owed'], 840000, places=2)
        self.assertAlmostEqual(d['required_run_rate'], 140000, places=2)
        # And it is strictly more than just finishing the plan would need.
        self.assertGreater(d['required_run_rate'], basis['target_ahead'] / 6)

    def test_a_business_already_ahead_is_not_asked_for_a_negative_pace(self):
        SalesRecord.objects.filter(source=SalesRecord.SOURCE_PLAN,
                                   measured_amount__gt=0).update(
            measured_amount=500000, net_amount=500000)
        d = self.ov()
        self.assertTrue(d['run_rate_basis']['already_ahead'])
        self.assertEqual(d['required_run_rate'], 0.0)

    def test_the_rate_is_reported_per_month_and_says_so(self):
        """The blueprint asks for these per working day. The review sheet --
        the only file carrying the plan -- has no days in it: every row is a
        month, stored on the 1st. A daily rate off it would be a monthly
        figure divided by a number of days nobody measured."""
        self.assertEqual(self.ov()['run_rate_basis']['per'], 'month')

    def test_it_says_how_much_the_pace_has_to_lift(self):
        d = self.ov()
        lift = d['run_rate_basis']['lift_needed_pct']
        # Running at 60,000 a month and needing 140,000 to land the plan --
        # which is a different business from needing 100,000, and the
        # difference is the half year already behind.
        self.assertAlmostEqual(lift, 133.3, places=1)

    def test_a_finished_year_asks_for_no_lift_rather_than_dividing_by_nought(self):
        SalesRecord.objects.filter(measured_amount=0).update(measured_amount=1,
                                                             net_amount=1)
        d = self.ov()
        self.assertEqual(d['run_rate_basis']['months_ahead'], 0)
        self.assertIsNone(d['required_run_rate'])
        self.assertIsNone(d['run_rate_basis']['lift_needed_pct'])

    def test_the_bands_can_be_moved_without_a_deploy(self):
        """These thresholds put five of the first seven real branches in RED
        and none in GREEN. That may be right -- a stretch plan the business
        habitually runs under -- but a screen where everything is red stops
        being read, and which it is is not a judgement this code can make. So
        the blueprint's numbers are the default and a settings line moves
        them."""
        from django.test import override_settings
        from sales import status as st
        with override_settings(SALESIQ_RED_BELOW=55, SALESIQ_GREEN_AT=95):
            self.assertEqual(st.rag(60), 'amber')   # red under the default
            self.assertEqual(st.rag(96), 'green')   # amber under the default
            self.assertIn('55', st.band(60)['meaning'])
        # Read per call, so nothing leaks past the override.
        self.assertEqual(st.rag(60), 'red')
        self.assertEqual(st.rag(96), 'amber')

    def test_the_meaning_is_built_from_the_bands_actually_in_force(self):
        """A module-level f-string would have frozen the defaults into every
        message at import, so a server that moved the red line to 55 would go
        on printing 'Under 70%' beside it."""
        d = self.ov()
        self.assertIn(str(int(d['status']['thresholds']['red_below'])),
                      d['status']['meaning'])

    def test_the_tree_says_how_many_landed_in_each_band(self):
        """So a wall of red reads as a statement about how the plan was set,
        rather than as nineteen separate accusations."""
        t = self.client.get('/api/sales/org/').json()['status_tally']
        self.assertEqual(t['total'], t['red'] + t['amber'] + t['green'] + t['unrated'])

    def test_the_tally_counts_each_person_once(self):
        """Counted down the whole tree, an RSM would be counted again inside
        his own team."""
        d = self.client.get('/api/sales/org/').json()
        self.assertEqual(d['status_tally']['total'], len(d['tree']))

    def test_the_headline_strip_is_answerable_from_one_comparison(self):
        """Revenue, AOP, gap and percentage have to be four readings of ONE
        comparison, or the strip contradicts itself in public.

        It did: the percentage was like-for-like, the gap was the window's
        whole revenue less the window's whole plan, and the bar was a third
        ratio again -- so a real screen read "79% of AOP" above a bar sitting
        at a third, beside "behind by Rs 212.73 Cr". Each was arithmetically
        correct. No two answered the same question. These assertions are what
        the screen needs in order to be drawn from one of them."""
        d = self.ov()
        basis = d['achievement_basis']

        # The percentage is that basis, and nothing else.
        self.assertAlmostEqual(d['achievement_pct'],
                               basis['revenue'] / basis['target'] * 100, places=1)
        # The gap is the same two numbers, subtracted rather than divided.
        self.assertAlmostEqual(d['gap_to_target'],
                               basis['target'] - basis['revenue'], places=2)
        # And the basis is a real subset of the window, not the whole of it:
        # six months of plan are loaded, six have results, so here they agree
        # -- but the plan must never exceed the window's own total.
        self.assertLessEqual(basis['target'], d['target'] + 0.01)

    def test_the_plan_for_months_with_results_is_sent_apart_from_the_window_total(self):
        """The screen needs both and must not confuse them: the plan for the
        months that have happened is the denominator of the percentage, the
        window's full plan is context."""
        d = self.ov()
        self.assertIn('target', d['achievement_basis'])
        self.assertIn('target', d)
        # Six months done out of twelve planned, so the window total is the
        # larger of the two and the basis covers only what has happened.
        self.assertEqual(d['achievement_basis']['months'], 6)
        self.assertGreater(d['target'], d['achievement_basis']['target'])

    def test_a_filter_the_invoice_file_cannot_answer_is_named(self):
        """The sheet has CHANEL TYPE and the dump's export does not, so every
        dump row holds a blank channel. Filter to GT and the money narrows
        correctly off the sheet while every panel built out of invoice detail
        empties — and "no categories" reads as "nothing sold in GT", which is
        false. The screen has to be able to say which filter did it."""
        SalesRecord.objects.all().delete()
        upload(aop_workbook([aop_row(**{'CHANEL TYPE': 'GT'})]))
        upload(a_workbook([a_row(**{'Order Date': '2026-04-05'})]))   # no channel
        d = self.client.get('/api/sales/overview/?channel=GT').json()
        self.assertIn('channel', d['filters_blind_to_invoices'])

    def test_a_filter_both_files_carry_is_not_named(self):
        SalesRecord.objects.all().delete()
        upload(a_workbook([a_row(**{'Zone': 'North', 'Order Date': '2026-04-05'})]))
        d = self.client.get('/api/sales/overview/?zone=North').json()
        self.assertEqual(d['filters_blind_to_invoices'], [])

    # ── governance ──────────────────────────────────────────────────────
    def test_the_screen_can_say_when_the_data_was_last_loaded(self):
        """Every figure here is as old as the last upload. Without saying so
        a screen looks live when it may be a fortnight stale."""
        fresh = self.ov()['data_refreshed']
        self.assertIsNotNone(fresh['at'])
        self.assertTrue(fresh['file'])
        self.assertEqual(fresh['uploads'], 1)


# ── read against the real sheet ─────────────────────────────────────────────
class TheRealReviewSheet(TestCase):
    """Written from screenshots of the live YTD AOP vs ACH file rather than
    from an idealised fixture. Everything here is a row shape that is
    actually in it."""

    def test_a_vacant_territory_writes_NA_in_both_id_columns(self):
        """And NA is not an ID. The sheet has many unfilled territories and
        every one of them carries the same literal "NA", so read as an ID
        they collapse into a single ASM -- the headcount falls by however
        many vacancies there are, minus one, and the error grows as more
        seats are left open. Exactly backwards."""
        hdr = ['CHANEL TYPE', 'HEAD', 'GTR HEAD', 'BIZOM ID', 'APIS ID',
               'REPORT.INCHARGE', 'BIZOM ID.', 'APIS ID.', 'REGION', 'Sub-Region']
        dims, _, _ = AOP.map_columns(hdr)
        cols = AOP.map_person_codes(hdr, dims)
        row = ['GT', 'ARUN MISHRA', 'MOHINDER SHARMA', 2298, 'SL04492',
               'VACANT-TRI', 'NA', 'NA', 'GTR01', 'CHD (TRI)']
        codes = AOP.read_person_codes(row, cols)
        self.assertEqual(codes['rsm_code'], 'SL04492')
        self.assertEqual(codes['asm_code'], '')

    def test_the_other_ways_the_sheet_says_nothing_here(self):
        for blank in ('NA', 'n/a', 'NIL', '-', '--', 'None', '', '  ', None):
            self.assertEqual(AOP._id_text(blank), '', repr(blank))

    def test_a_bizom_id_arrives_as_a_number_and_an_apis_id_as_text(self):
        """2298 and SL04492, side by side in the same row."""
        self.assertEqual(AOP._id_text(2298), '2298')
        self.assertEqual(AOP._id_text(2298.0), '2298')
        self.assertEqual(AOP._id_text('SL04492'), 'SL04492')

    def test_an_unfilled_territory_is_not_counted_as_a_manager(self):
        """Counting VACANT-TRI made the ASM figure the number of
        TERRITORIES, which rises as the company leaves more seats open."""
        SalesRecord.objects.all().delete()
        upload(aop_workbook([
            aop_row(**{'REPORT.INCHARGE': 'Vikas Gupta', 'APIS ID.': 'SL01'}),
            aop_row(**{'REPORT.INCHARGE': 'VACANT-TRI', 'APIS ID.': 'NA',
                       'BIZOM ID.': 'NA', 'Sub-Region': 'CHD (TRI)'}),
            aop_row(**{'REPORT.INCHARGE': 'VACANT-DL', 'APIS ID.': 'NA',
                       'BIZOM ID.': 'NA', 'Sub-Region': 'DL-1'}),
        ]))
        d = self.client.get('/api/sales/org/').json()
        asm = next(c for c in d['level_counts'] if c['level'] == 'asm')
        self.assertEqual(asm['count'], 1)       # one actual manager
        self.assertEqual(asm['vacant'], 2)      # two seats open, said separately

    def test_the_sheets_own_newer_columns_are_known_rather_than_unread(self):
        """Yesterday Billing appeared on the sheet after this importer was
        written, and was being reported back as a column it did not
        understand. It is understood and not loaded: every other money column
        here is a month, and a single day's billing added among them would be
        counted as one."""
        hdr = AOP_HEADERS + ['Yesterday Billing']
        _, _, unknown = AOP.map_columns(hdr)
        self.assertIn('Yesterday Billing', unknown)
        self.assertIn('Yesterday Billing', AOP.IGNORED)

    def test_yesterday_billing_is_never_read_as_a_month(self):
        self.assertIsNone(AOP.parse_month_header('Yesterday Billing'))

    def test_i_code_is_a_line_number_not_a_product_code(self):
        """Its values run 1, 2, 3 down each territory's block -- which is why
        Key can be built from Sub-Region plus I-CODE, giving "CHD (TRI)1". It
        was stored as item_alt_code, which is a trap rather than a bug today:
        nothing shows that field, but the day somebody adds it to the filter
        list the product dropdown fills with 1.00, 2.00, 3.00."""
        upload(aop_workbook([aop_row(**{'I-CODE': 1.0, 'ITEM NAME': 'HONEY'})]))
        r = SalesRecord.objects.filter(period=date(2026, 4, 1)).first()
        self.assertEqual(r.item_alt_code, '')
        self.assertEqual(r.product_name, 'HONEY')
        self.assertIn('I-CODE', AOP.IGNORED)

    def test_the_summary_columns_are_still_recognised_beside_the_new_one(self):
        """Adding entries to IGNORED must not have displaced the totals it
        already carried -- they are what the import checks itself against."""
        for col in ('Key', 'YTD AOP', 'YTD ACH', 'LYTD ACH', 'LMTD',
                    'MTD SEC SALES'):
            self.assertIn(col, AOP.IGNORED, col)

    def test_a_month_can_be_negative_because_the_sheet_writes_returns_that_way(self):
        """Apr-25 carries -0.80 and -0.10 on real rows."""
        upload(aop_workbook([aop_row(ly=-0.80)]))
        ly = SalesRecord.objects.get(period=date(2025, 4, 1))
        self.assertLess(float(ly.measured_amount), 0)


# -- the daily GTR-head review sheet ----------------------------------------
REVIEW_HEADERS = [
    'CHANEL TYPE', 'REGION', 'GTR HEAD', 'NO OF SFO', 'LMTD', 'Sep-26 AOP',
    'YESTERDAY BILLING', 'MTD Sep-26 PRI SALES', 'MTD Sec sales', 'AOP ACH %',
    'Growth Over LM', 'Backlog TGT FTM', 'YTD AOP', 'YTD ACH', 'YTD ACH %',
    'BACKLOG TGT YTD', "FY'26-27 AOP", "FY'26-27 ACH", 'FY ACH %', 'BACKLOG FY',
]

# Mohinder Sharma's line, exactly as it reads on the sheet (lakhs).
GTR01 = ['GT', 'GTR01', 'MOHINDER SHARMA', 20, 57.61, 114.08, 21.09, 83.91,
         77.50, 0.74, 0.46, 30.17, 619.69, 300.75, 0.49, 318.94,
         1423.73, 300.75, 0.21, 1122.98]
# Gulshan Kumar, whose REGION cell is merged upward on the real sheet.
GTR04B = ['GT', 'GTR04 B', 'GULSHAN KUMAR', 23, 86.86, 91.44, 6.25, 110.33,
          94.54, 1.21, 0.27, -18.89, 496.69, 454.59, 0.92, 42.10,
          1141.15, 454.59, 0.40, 686.56]


def review_workbook(rows, headers=None):
    """A workbook shaped like the real review file: a merged banner row above
    the headers, then the head rows, then the subtotals."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([None, None, None, None, "MTD FOR SEPT'26"])
    ws.append(list(headers or REVIEW_HEADERS))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


class TheDailyReviewSheet(TestCase):

    def test_it_is_recognised_as_its_own_sheet(self):
        self.assertTrue(REVIEW.looks_like_review_sheet(REVIEW_HEADERS))

    def test_it_is_not_mistaken_for_the_aop_sheet_or_the_dump(self):
        """It carries LMTD, YTD AOP and MTD SEC SALES, which the AOP sheet
        carries too -- so those cannot be what tells them apart."""
        self.assertFalse(AOP.looks_like_aop_sheet(REVIEW_HEADERS))
        self.assertFalse(REVIEW.looks_like_review_sheet(AOP_HEADERS))

    def test_the_month_is_read_off_the_column_headers(self):
        _, _, as_of, _ = REVIEW.map_columns(REVIEW_HEADERS)
        self.assertEqual(as_of, date(2026, 9, 1))

    def test_every_column_on_the_sheet_is_understood(self):
        _, _, _, unknown = REVIEW.map_columns(REVIEW_HEADERS)
        self.assertEqual(unknown, [])

    def test_the_mtd_primary_column_carries_the_month_in_its_name(self):
        cols, _, _, _ = REVIEW.map_columns(REVIEW_HEADERS)
        self.assertEqual(cols['mtd_primary'], REVIEW_HEADERS.index('MTD Sep-26 PRI SALES'))
        self.assertEqual(cols['month_target'], REVIEW_HEADERS.index('Sep-26 AOP'))

    def test_the_fy_columns_survive_the_apostrophe_and_the_dash(self):
        cols, _, _, _ = REVIEW.map_columns(REVIEW_HEADERS)
        self.assertIn('fy_target', cols)
        self.assertIn('fy_actual', cols)

    def test_it_loads_into_its_own_tables_and_not_into_sales(self):
        """The whole point. Every figure on this sheet is a sum of rows
        already held, so one loaded as a sale is a rupee counted twice."""
        before = SalesRecord.objects.count()
        upload(review_workbook([GTR01, GTR04B]))
        self.assertEqual(SalesRecord.objects.count(), before)
        self.assertEqual(ReviewRow.objects.filter(is_total=False).count(), 2)

    def test_lakhs_are_converted_to_rupees_like_the_aop_sheet(self):
        upload(review_workbook([GTR01, GTR04B]))
        snap = ReviewSnapshot.objects.first()
        self.assertEqual(snap.source_unit, 'lakhs')
        me = snap.rows.get(region='GTR01')
        self.assertEqual(float(me.mtd_primary), 83.91 * 100_000)
        self.assertEqual(float(me.fy_target), 1423.73 * 100_000)

    def test_the_percentages_we_compute_match_the_ones_the_sheet_prints(self):
        upload(review_workbook([GTR01]))
        me = ReviewSnapshot.objects.first().rows.get(region='GTR01')
        self.assertEqual(round(me.month_pct), 74)      # sheet: 74%
        self.assertAlmostEqual(me.ytd_pct, 48.5, places=1)   # sheet prints 49%
        self.assertEqual(round(me.fy_pct), 21)         # sheet: 21%
        self.assertEqual(round(me.growth_pct), 46)     # sheet: 46%

    def test_the_backlogs_we_compute_match_the_ones_the_sheet_prints(self):
        upload(review_workbook([GTR01]))
        me = ReviewSnapshot.objects.first().rows.get(region='GTR01')
        self.assertAlmostEqual(me.month_backlog / 100_000, 30.17, places=2)
        self.assertAlmostEqual(me.ytd_backlog / 100_000, 318.94, places=2)
        self.assertAlmostEqual(me.fy_backlog / 100_000, 1122.98, places=2)

    def test_a_backlog_can_be_negative_when_the_head_is_ahead(self):
        upload(review_workbook([GTR04B]))
        me = ReviewSnapshot.objects.first().rows.get(region='GTR04 B')
        self.assertAlmostEqual(me.month_backlog / 100_000, -18.89, places=2)

    def test_sales_against_no_plan_are_not_reported_as_zero_percent(self):
        """The handover row: ARUN MISHRA (ARNAB GHOSH) bills 30.30 against a
        blank AOP. Reported as 0% it tells the person they achieved nothing."""
        handover = ['GT', '', 'ARUN MISHRA ( ARNAB GHOSH)', 0, 0.0, 0.0, 15.22,
                    30.30, 0.0, 0, 0, -30.30, 0.0, 30.30, 0, -30.30,
                    0.0, 30.30, 0, -30.30]
        upload(review_workbook([GTR01, handover]))
        me = ReviewSnapshot.objects.first().rows.get(head_name__startswith='ARUN')
        self.assertIsNone(me.month_pct)
        self.assertIsNone(me.growth_pct)

    def test_a_merged_region_cell_is_carried_down_to_the_row_beneath_it(self):
        """GTR04 A spans two rows on the real sheet, so the second arrives
        with no region at all and would drop out of every grouping."""
        handover = ['', '', 'ARUN MISHRA ( ARNAB GHOSH)', 0, 0.0, 0.0, 15.22,
                    30.30, 0.0, 0, 0, -30.30, 0.0, 30.30, 0, -30.30,
                    0.0, 30.30, 0, -30.30]
        upload(review_workbook([GTR01, handover]))
        me = ReviewSnapshot.objects.first().rows.get(head_name__startswith='ARUN')
        self.assertEqual(me.region, 'GTR01')
        self.assertEqual(me.channel, 'GT')

    def test_subtotal_rows_are_kept_apart_from_the_heads_they_are_made_of(self):
        total = ['GT Total', '', '', 43, 144.47, 205.52, 27.34, 194.24,
                 172.04, 0.95, 0.34, 11.28, 1116.38, 755.34, 0.68, 361.04,
                 2564.88, 755.34, 0.29, 1809.54]
        upload(review_workbook([GTR01, GTR04B, total]))
        snap = ReviewSnapshot.objects.first()
        self.assertEqual(snap.rows.filter(is_total=True).count(), 1)
        self.assertEqual(snap.row_count, 2)

    def test_the_head_is_matched_to_an_apis_id_from_the_aop_sheet(self):
        """A name is not an identity. A report addressed to a spelling goes
        to nobody."""
        upload(aop_workbook([aop_row(**{'GTR HEAD': 'MOHINDER SHARMA',
                                        'APIS ID': 'SL04492'})]))
        upload(review_workbook([GTR01]))
        me = ReviewSnapshot.objects.first().rows.get(region='GTR01')
        self.assertEqual(me.head_code, 'SL04492')

    def test_the_report_endpoint_answers_for_one_head(self):
        upload(review_workbook([GTR01, GTR04B]))
        d = self.client.get('/api/sales/review/report/?head=GTR01').json()
        self.assertEqual(d['head']['head_name'], 'Mohinder Sharma')
        self.assertEqual(round(d['head']['month_pct']), 74)

    def test_the_report_says_what_the_rest_of_the_year_has_to_run_at(self):
        """The most useful number on the sheet, and the one it does not
        print: 1,122.98 of backlog over the six months from October."""
        upload(review_workbook([GTR01]))
        d = self.client.get('/api/sales/review/report/?head=GTR01').json()
        self.assertEqual(d['year']['months_elapsed'], 6)
        self.assertEqual(d['year']['months_remaining'], 6)
        self.assertAlmostEqual(d['year']['required_monthly'] / 100_000,
                               1122.98 / 6, places=1)

    def test_the_report_ranks_a_head_only_against_its_own_channel(self):
        """A GT head measured against E-COM, whose field force is zero and
        whose plan is phased differently, is a comparison that reads as a
        judgement and means nothing."""
        ecom = ['OT', 'E-COM', 'HARI OM SOLANKI', 0, 225.46, 260.39, 12.46,
                274.14, 274.14, 1.05, 0.22, -13.74, 1555.72, 1102.77, 0.71,
                452.95, 3843.91, 1102.77, 0.29, 2741.14]
        upload(review_workbook([GTR01, GTR04B, ecom]))
        d = self.client.get('/api/sales/review/report/?head=GTR01').json()
        self.assertEqual(d['rank']['month_pct']['of'], 2)
        self.assertEqual(d['rank']['month_pct']['position'], 2)

    def test_a_head_can_be_asked_for_by_id_region_or_name(self):
        upload(review_workbook([GTR01]))
        for key in ('GTR01', 'MOHINDER SHARMA', 'mohinder sharma'):
            r = self.client.get(f'/api/sales/review/report/?head={key}')
            self.assertEqual(r.status_code, 200, key)

    def test_asking_for_a_head_that_is_not_on_the_sheet_says_so(self):
        upload(review_workbook([GTR01]))
        r = self.client.get('/api/sales/review/report/?head=GTR99')
        self.assertEqual(r.status_code, 404)

    def test_the_import_reports_a_disagreement_with_the_sheets_own_total(self):
        """An importer that quietly prefers its own sum is one nobody can
        check."""
        wrong = ['Grand Total', '', '', 43, 144.47, 205.52, 27.34, 999.99,
                 172.04, 0, 0, 0, 1116.38, 755.34, 0, 0,
                 2564.88, 755.34, 0, 0]
        r = upload(review_workbook([GTR01, GTR04B, wrong]))
        snap = ReviewSnapshot.objects.first()
        self.assertTrue(any('Grand Total' in w for w in snap.warnings), snap.warnings)

    def test_the_snapshot_is_filed_under_the_month_the_sheet_reports(self):
        """Not under the day it was uploaded -- a sheet uploaded late still
        belongs to the month it describes."""
        upload(review_workbook([GTR01]))
        self.assertEqual(ReviewSnapshot.objects.first().as_of_month, date(2026, 9, 1))

    def test_deleting_a_snapshot_takes_its_rows_with_it(self):
        upload(review_workbook([GTR01, GTR04B]))
        snap = ReviewSnapshot.objects.first()
        self.client.delete(f'/api/sales/review/{snap.id}/')
        self.assertEqual(ReviewRow.objects.count(), 0)


class TheReportFile(TestCase):
    """The file that actually gets sent to a person."""

    def setUp(self):
        upload(review_workbook([GTR01, GTR04B]))

    def test_it_renders_a_whole_html_document(self):
        r = self.client.get('/api/sales/review/report/file/?head=GTR01')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertTrue(body.startswith('<!doctype html>'))
        self.assertIn('Mohinder Sharma', body)

    def test_it_is_self_contained(self):
        """It has to survive being emailed, opened offline and printed. A
        report that needs the network to render arrives blank."""
        body = self.client.get('/api/sales/review/report/file/?head=GTR01').content.decode()
        self.assertNotIn('<script', body.lower())
        self.assertNotIn('http://', body)
        self.assertNotIn('<link', body.lower())

    def test_the_figures_in_the_file_are_the_sheets_own(self):
        body = self.client.get('/api/sales/review/report/file/?head=GTR01').content.decode()
        for figure in ('83.91', '114.08', '619.69', '300.75', '1,423.73',
                       '1,122.98', '30.17', '318.94'):
            self.assertIn(figure, body, figure)

    def test_it_says_what_the_rest_of_the_year_needs(self):
        """1,122.98 over six months. The number the sheet does not print."""
        body = self.client.get('/api/sales/review/report/file/?head=GTR01').content.decode()
        self.assertIn('187.16', body)

    def test_it_splits_the_annual_backlog_into_its_two_halves(self):
        """804.04 is the months ahead carrying their own plan; 318.94 is
        catching up on months already closed. The sheet prints only the sum,
        and they are different problems."""
        body = self.client.get('/api/sales/review/report/file/?head=GTR01').content.decode()
        self.assertIn('804.04', body)
        self.assertIn('134.01', body)      # required just to stay on plan

    def test_downloading_names_the_file_after_the_head_and_the_date(self):
        r = self.client.get('/api/sales/review/report/file/?head=GTR01&download=1')
        self.assertIn('attachment', r['Content-Disposition'])
        self.assertIn('GTR01', r['Content-Disposition'])

    def test_the_bundle_holds_one_file_per_head(self):
        """One file each rather than one document with everybody in it: a
        head who can read the whole sheet is being shown their peers'
        numbers whether or not that was intended."""
        import zipfile, io as _io
        r = self.client.get('/api/sales/review/bundle/')
        self.assertEqual(r.status_code, 200)
        z = zipfile.ZipFile(_io.BytesIO(r.content))
        self.assertEqual(len(z.namelist()), 2)
        self.assertTrue(all(n.endswith('.html') for n in z.namelist()))

    def test_each_file_in_the_bundle_is_that_heads_own_report(self):
        import zipfile, io as _io
        z = zipfile.ZipFile(_io.BytesIO(self.client.get('/api/sales/review/bundle/').content))
        mine = next(n for n in z.namelist() if 'GTR01' in n)
        body = z.read(mine).decode()
        self.assertIn('Mohinder Sharma', body)
        self.assertNotIn('Gulshan Kumar', body.split('Against the other heads')[0])

    def test_a_head_ahead_of_plan_is_not_told_they_are_behind(self):
        """The opening line is assembled from what is true of this head, not
        from one template with the numbers swapped in."""
        body = self.client.get('/api/sales/review/report/file/?head=GTR04 B').content.decode()
        self.assertIn('ahead of target', body)
        self.assertNotIn('behind target', body)

    def test_no_plan_is_not_rendered_as_zero_per_cent(self):
        handover = ['GT', 'GTR05', 'ARUN MISHRA ( ARNAB GHOSH)', 0, 0.0, 0.0,
                    15.22, 30.30, 0.0, 0, 0, -30.30, 0.0, 30.30, 0, -30.30,
                    0.0, 30.30, 0, -30.30]
        ReviewSnapshot.objects.all().delete()
        upload(review_workbook([GTR01, handover]))
        body = self.client.get(
            '/api/sales/review/report/file/?head=GTR05').content.decode()
        self.assertIn('--', body)

    def test_lakhs_are_grouped_the_indian_way(self):
        self.assertEqual(REPORT.lakh(1122.98 * 100_000), '1,122.98')
        self.assertEqual(REPORT.lakh(83.91 * 100_000), '83.91')
        self.assertEqual(REPORT.lakh(31927.46 * 100_000), '31,927.46')

    def test_a_name_with_a_slash_in_it_cannot_escape_the_zip(self):
        self.assertNotIn('/', REPORT._safe('GTR04 A/B'))
        self.assertNotIn('..', REPORT._safe('../../etc/passwd'))


# -- the daily workbook -----------------------------------------------------
def named_workbook(sheets):
    """A workbook whose tabs are named, in order. `sheets` is
    [(title, [header row, *data rows])]."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets:
        ws = wb.create_sheet(title=title)
        header = list(rows[0])
        ws.append(header)
        for r in rows[1:]:
            # The other helpers build a row as {header: value}; a plain list
            # is taken as already laid out.
            ws.append([r.get(h) for h in header] if isinstance(r, dict) else list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = 'Daily MIS.xlsx'
    return buf


class PickingTheRightTabs(TestCase):
    """The file uploaded each morning has many sheets. Three are ours."""

    def test_the_three_tabs_are_recognised_by_name(self):
        chosen, ignored = pick_sheets(
            ['Region Summary', 'Sub-Region', 'Cover', 'YTD,AOP vs.ACH', 'PRI SALES DUMP'])
        self.assertEqual(chosen['review'], 'Region Summary')
        self.assertEqual(chosen['aop'], 'YTD,AOP vs.ACH')
        self.assertEqual(chosen['dump'], 'PRI SALES DUMP')
        self.assertEqual(ignored, ['Sub-Region', 'Cover'])

    def test_punctuation_in_a_tab_name_carries_no_meaning(self):
        """"YTD,AOP vs.ACH" and "YTD AOP vs ACH" are the same tab named by
        two different people."""
        for t in ('YTD,AOP vs.ACH', 'YTD AOP vs ACH', 'ytd aop vs. ach'):
            self.assertEqual(pick_sheets([t])[0].get('aop'), t, t)

    def test_sub_region_is_not_read_although_it_looks_identical(self):
        """It is the same report cut finer, so its headers match Region
        Summary exactly. Routed on headers alone both would load and every
        head would be counted once per sub-region as well."""
        self.assertTrue(REVIEW.looks_like_review_sheet(REVIEW_HEADERS))
        chosen, ignored = pick_sheets(['Region Summary', 'Sub-Region'])
        self.assertNotIn('Sub-Region', chosen.values())
        self.assertIn('Sub-Region', ignored)

    def test_only_the_named_tabs_are_loaded_from_a_real_workbook(self):
        sub = list(GTR01); sub[1] = 'CHD (TRI)'
        upload(named_workbook([
            ('Cover',          [['Daily MIS', None]]),
            ('Region Summary', [REVIEW_HEADERS, GTR01, GTR04B]),
            ('Sub-Region',     [REVIEW_HEADERS, sub, sub, sub]),
        ]))
        self.assertEqual(ReviewSnapshot.objects.count(), 1)
        self.assertEqual(ReviewRow.objects.filter(is_total=False).count(), 2)

    def test_the_upload_says_what_it_read_and_what_it_left_alone(self):
        d = upload(named_workbook([
            ('Region Summary', [REVIEW_HEADERS, GTR01]),
            ('Sub-Region',     [REVIEW_HEADERS, GTR01]),
            ('Notes',          [['anything']]),
        ])).json()
        every = ' '.join(d['notes'])
        self.assertIn('Region Summary', every)
        self.assertIn('left alone', every)
        self.assertIn('Sub-Region', every)

    def test_a_renamed_tab_still_works_on_its_headers(self):
        """A single-sheet export saved out of one of these tabs is a
        perfectly good upload. Refusing it because somebody renamed the tab
        is the kind of strictness that gets worked around, not fixed."""
        upload(named_workbook([('Sheet1', [REVIEW_HEADERS, GTR01])]))
        self.assertEqual(ReviewRow.objects.filter(is_total=False).count(), 1)


class UploadedEveryMorning(TestCase):
    """Both dashboard sheets restate the whole year every time."""

    def test_the_same_file_twice_does_not_double_the_sales(self):
        """Appended, day two reports every figure twice and day three three
        times -- a dashboard that climbs steadily for reasons that have
        nothing to do with selling."""
        upload(a_workbook([a_row(**{'Invoice No.': 'INV-1'}),
                           a_row(**{'Invoice No.': 'INV-2'})]))
        first = SalesRecord.objects.count()
        total = float(SalesRecord.objects.aggregate(s=Sum('net_amount'))['s'])

        upload(a_workbook([a_row(**{'Invoice No.': 'INV-1'}),
                           a_row(**{'Invoice No.': 'INV-2'})]))
        self.assertEqual(SalesRecord.objects.count(), first)
        self.assertEqual(float(SalesRecord.objects.aggregate(s=Sum('net_amount'))['s']),
                         total)

    def test_the_aop_sheet_replaces_the_previous_aop_sheet(self):
        upload(aop_workbook([aop_row(aop=100000)]))
        upload(aop_workbook([aop_row(aop=120000)]))
        plan = SalesRecord.objects.filter(source=SalesRecord.SOURCE_PLAN,
                                          period=date(2026, 4, 1))
        self.assertEqual(plan.count(), 1)
        self.assertEqual(float(plan.get().target_amount), 120000)

    def test_a_sheet_only_replaces_its_own_kind(self):
        """A morning with no dump attached must not wipe the dump."""
        upload(a_workbook([a_row()]))
        invoices = SalesRecord.objects.filter(source=SalesRecord.SOURCE_INVOICE).count()
        upload(aop_workbook([aop_row()]))
        self.assertEqual(
            SalesRecord.objects.filter(source=SalesRecord.SOURCE_INVOICE).count(),
            invoices)

    def test_the_replaced_file_disappears_rather_than_reading_nought_rows(self):
        """An entry saying 0 rows reads as a failed import, not a replaced
        one."""
        upload(a_workbook([a_row()]))
        upload(a_workbook([a_row()]))
        self.assertEqual(SalesUpload.objects.count(), 1)

    def test_a_file_that_keeps_half_its_sheets_keeps_an_honest_row_count(self):
        """A two-tab workbook whose dump is superseded still holds its AOP
        rows. Its stored count has to come back to what is left, or the list
        header disagrees with the lines under it."""
        upload(named_workbook([
            ('YTD,AOP vs.ACH',  [AOP_HEADERS, aop_row()]),
            ('PRI SALES DUMP',  [header_names(), a_row()]),
        ]))
        upload(a_workbook([a_row()]))
        for u in SalesUpload.objects.all():
            self.assertEqual(u.row_count, u.records.count(), u.filename)
        d = self.client.get('/api/sales/uploads/').json()
        self.assertEqual(sum(u['rows'] for u in d['results']), d['total_rows'])

    def test_review_snapshots_are_kept_rather_than_replaced(self):
        """Each is a dated statement of where people stood that morning,
        nothing on the dashboard reads them, and the history is the point."""
        upload(review_workbook([GTR01]))
        upload(review_workbook([GTR01]))
        self.assertEqual(ReviewSnapshot.objects.count(), 2)

    def test_the_report_is_built_from_the_most_recent_morning(self):
        upload(review_workbook([GTR01]))
        later = list(GTR01); later[7] = 99.99       # MTD primary moved on
        upload(review_workbook([later]))
        d = self.client.get('/api/sales/review/report/?head=GTR01').json()
        self.assertAlmostEqual(d['head']['mtd_primary'] / 100_000, 99.99, places=2)

    def test_the_upload_says_what_it_replaced(self):
        upload(a_workbook([a_row()]))
        d = upload(a_workbook([a_row()])).json()
        self.assertTrue(any('replaced' in n for n in d['notes']), d['notes'])


class WhoMayLoadTheMorningFile(TestCase):
    """Uploading is a daily chore; owning the data is not the same thing."""

    def setUp(self):
        from accounts.models import AppKey, PortalUser
        from sales.views.auth import issue_session
        PortalUser.objects.create(email='reader@apisindia.com', is_active=True,
                                  app_access=[AppKey.SALESIQ])
        self.reader = {'HTTP_X_SALESIQ_SESSION': issue_session('reader@apisindia.com')}

    def test_a_reader_cannot_upload(self):
        r = RawClient().post('/api/sales/upload/', {}, **self.reader)
        self.assertEqual(r.status_code, 403)

    def test_granting_upload_access_lets_them_upload(self):
        self.client.post('/api/sales/uploaders/edit/',
                         {'email': 'reader@apisindia.com', 'name': 'A Reader'},
                         content_type='application/json')
        r = RawClient().post('/api/sales/upload/', {}, **self.reader)
        self.assertNotEqual(r.status_code, 403)     # refused for a missing file now

    def test_an_uploader_still_cannot_clear_everything(self):
        """The one action with no way back stays with the owner even though
        loading the file does not."""
        UploaderGrant.objects.create(email='reader@apisindia.com')
        r = RawClient().delete('/api/sales/uploads/', **self.reader)
        self.assertEqual(r.status_code, 403)

    def test_an_uploader_may_undo_one_bad_morning_file(self):
        UploaderGrant.objects.create(email='reader@apisindia.com')
        upload(a_workbook([a_row()]))
        u = SalesUpload.objects.get()
        r = RawClient().delete(f'/api/sales/uploads/?id={u.id}', **self.reader)
        self.assertEqual(r.status_code, 200)

    def test_a_plain_reader_still_cannot_delete_one_file(self):
        """Moving the owner check onto the clear-all branch let a reader
        through the single-file branch, where the only thing stopping them
        was the id not existing."""
        upload(a_workbook([a_row()]))
        u = SalesUpload.objects.get()
        r = RawClient().delete(f'/api/sales/uploads/?id={u.id}', **self.reader)
        self.assertEqual(r.status_code, 403)

    def test_an_uploader_cannot_grant_it_to_anybody_else(self):
        """Otherwise the distinction between the two roles is decorative."""
        UploaderGrant.objects.create(email='reader@apisindia.com')
        r = RawClient().post('/api/sales/uploaders/edit/',
                             {'email': 'someone@apisindia.com'},
                             content_type='application/json', **self.reader)
        self.assertEqual(r.status_code, 403)

    def test_revoking_takes_the_access_away(self):
        g = UploaderGrant.objects.create(email='reader@apisindia.com')
        self.client.delete(f'/api/sales/uploaders/edit/{g.id}/')
        r = RawClient().post('/api/sales/upload/', {}, **self.reader)
        self.assertEqual(r.status_code, 403)


class WhoGetsWhichReport(TestCase):

    def setUp(self):
        upload(review_workbook([GTR01, GTR04B]))

    def test_a_head_is_added_against_their_apis_id(self):
        r = self.client.post('/api/sales/recipients/edit/',
                             {'role': 'head', 'head_key': 'GTR01',
                              'name': 'Mohinder Sharma', 'email': 'm@apisindia.com'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 201)
        d = self.client.get('/api/sales/recipients/').json()
        self.assertTrue(d['recipients'][0]['matched'])

    def test_a_head_needs_a_key_so_the_report_knows_whose_territory(self):
        r = self.client.post('/api/sales/recipients/edit/',
                             {'role': 'head', 'email': 'm@apisindia.com'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_heads_with_nobody_against_them_are_named(self):
        """A head with no recipient is one whose report is built every
        morning and sent to no one."""
        d = self.client.get('/api/sales/recipients/').json()
        self.assertEqual(len(d['heads_without_a_recipient']), 2)

    def test_the_template_lists_every_head_with_an_empty_email(self):
        """Filled in and uploaded back, this is the whole of setting it up."""
        import openpyxl
        r = self.client.get('/api/sales/recipients/template/')
        self.assertEqual(r.status_code, 200)
        ws = openpyxl.load_workbook(io.BytesIO(r.content)).worksheets[0]
        cells = [[('' if c is None else str(c)) for c in row]
                 for row in ws.iter_rows(values_only=True)]
        flat = [' '.join(row) for row in cells]
        self.assertTrue(any('GTR01' in f for f in flat), flat[:8])
        self.assertTrue(any('Mohinder Sharma' in f for f in flat))
        # The head rows carry a key and no email: the email is the work.
        head = next(row for row in cells if row and row[0] == 'head')
        self.assertEqual(head[4], '')

    def test_the_list_is_one_sheet_of_rows_and_nothing_else(self):
        """It carried a title, a hint row, a section banner above each block
        and a second tab. All of it was furniture the importer then had to
        recognise and skip, and the once it misread a line of it the screen
        filled with errors about rows nobody had typed."""
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(
            self.client.get('/api/sales/recipients/template/').content))
        self.assertEqual(wb.sheetnames, ['Recipients'])
        first = [c.value for c in wb.worksheets[0][1]]
        self.assertEqual(first, ['role', 'key', 'region', 'name', 'email',
                                 'regions_covered'])

    def test_the_filled_in_workbook_can_be_uploaded_back(self):
        import openpyxl
        r = self.client.get('/api/sales/recipients/template/')
        wb = openpyxl.load_workbook(io.BytesIO(r.content))
        ws = wb.worksheets[0]
        for row in ws.iter_rows():
            if row[0].value == 'head' and row[1].value == 'GTR01':
                row[4].value = 'mohinder@apisindia.com'
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'report recipients.xlsx'
        d = self.client.post('/api/sales/recipients/import/',
                             {'file': buf}).json()
        self.assertEqual(d['added'], 1, d)
        self.assertEqual(d['skipped'], [])
        self.assertTrue(ReportRecipient.objects.filter(
            email='mohinder@apisindia.com', head_key='GTR01').exists())

    def test_a_workbook_is_read_by_its_bytes_not_its_extension(self):
        """The reader decided on the name. A workbook that arrived called
        .csv -- which is what Excel leaves you with if you edit the download
        and pick Save rather than Save As -- went to the text reader, which
        duly parsed the compressed zip bytes and reported every line of a
        correctly filled list as malformed."""
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(
            self.client.get('/api/sales/recipients/template/').content))
        for row in wb.worksheets[0].iter_rows():
            if row[0].value == 'head' and row[1].value == 'GTR01':
                row[4].value = 'mohinder@apisindia.com'
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = 'report recipients.csv'          # the wrong extension
        d = self.client.post('/api/sales/recipients/import/',
                             {'file': buf}).json()
        self.assertEqual(d['added'], 1, d)
        self.assertEqual(d['skipped'], [])

    def test_a_workbook_that_cannot_be_opened_says_so_once(self):
        """Not as a line-by-line reading of its compressed bytes. One
        sentence naming the file, not thirty-six naming rows that do not
        exist."""
        buf = io.BytesIO(b'PK' + bytes([3, 4]) + b'this is not a workbook')
        buf.name = 'report recipients.xlsx'
        r = self.client.post('/api/sales/recipients/import/', {'file': buf})
        self.assertEqual(r.status_code, 400)
        d = r.json()
        self.assertIn('Cannot read that file', d['error'])
        self.assertNotIn('skipped', d)

    def test_a_csv_saved_by_excel_on_windows_is_read(self):
        """CRLF line endings. Without newline='' csv.reader refuses the whole
        file: "new-line character seen in unquoted field"."""
        raw = ('role,key,region,name,email,regions_covered\r\n'
               'head,GTR01,GTR01,Mohinder Sharma,m@apisindia.com,\r\n'
               'head,GTR04 B,GTR04 B,Gulshan Kumar,g@apisindia.com,\r\n')
        buf = io.BytesIO(raw.encode('utf-8'))
        buf.name = 'report recipients.csv'
        d = self.client.post('/api/sales/recipients/import/', {'file': buf}).json()
        self.assertEqual(d['added'], 2, d)
        self.assertEqual(d['skipped'], [])

    def test_a_csv_with_a_byte_order_mark_is_read(self):
        """Excel writes one on "CSV UTF-8". It lands on the first cell, so the
        header row stops looking like a header."""
        raw = ('\ufeffrole,key,region,name,email,regions_covered\r\n'
               'head,GTR01,GTR01,Mohinder Sharma,m@apisindia.com,\r\n')
        buf = io.BytesIO(raw.encode('utf-8'))
        buf.name = 'report recipients.csv'
        d = self.client.post('/api/sales/recipients/import/', {'file': buf}).json()
        self.assertEqual(d['added'], 1, d)

    def test_a_tab_separated_paste_out_of_excel_is_read(self):
        """Copying a block out of Excel puts TABS on the clipboard, not
        commas -- read as CSV every row came back as a single cell."""
        text = ('head\tGTR01\tGTR01\tMohinder Sharma\tm@apisindia.com\t\n'
                'head\tGTR04 B\tGTR04 B\tGulshan Kumar\tg@apisindia.com\t\n')
        d = self.client.post('/api/sales/recipients/import/', {'text': text},
                             content_type='application/json').json()
        self.assertEqual(d['added'], 2, d)

    def _csv_upload(self, text, name='report recipients.csv'):
        buf = io.BytesIO(text.encode('utf-8'))
        buf.name = name
        return self.client.post('/api/sales/recipients/import/',
                                {'file': buf}).json()

    def test_a_semicolon_separated_file_is_read(self):
        """Excel uses the machine's list separator when it saves a CSV --
        comma here, semicolon across much of Europe. Assumed to be a comma,
        every row came back as a single cell."""
        d = self._csv_upload(
            'role;key;region;name;email;regions_covered\r\n'
            'head;GTR01;GTR01;Mohinder Sharma;m@apisindia.com;\r\n'
            'head;GTR04 B;GTR04 B;Gulshan Kumar;g@apisindia.com;\r\n')
        self.assertEqual(d['added'], 2, d)

    def test_a_tab_separated_file_is_read(self):
        """"Text (Tab delimited)" is one of the save-as options."""
        d = self._csv_upload(
            'role\tkey\tregion\tname\temail\tregions_covered\r\n'
            'head\tGTR01\tGTR01\tMohinder Sharma\tm@apisindia.com\t\r\n')
        self.assertEqual(d['added'], 1, d)

    def test_a_comma_inside_a_name_cannot_outvote_the_real_separator(self):
        """Counting the character would pick the comma here. The separator is
        whichever one yields the most columns."""
        d = self._csv_upload(
            'role;key;region;name;email;regions_covered\r\n'
            'head;GTR01;GTR01;"Sharma, Mohinder";m@apisindia.com;\r\n')
        self.assertEqual(d['added'], 1, d)

    def test_every_row_failing_the_same_way_is_reported_as_one_fault(self):
        """Sixteen identical complaints is not sixteen mistakes, it is one:
        the file was not read in the shape it was written."""
        r = self.client.post(
            '/api/sales/recipients/import/',
            {'text': '\n'.join('head|GTR0%d|x' % i for i in range(1, 9))},
            content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('did not come through as a table', r.json()['error'])

    def test_a_manager_with_no_territory_is_reported_not_silently_added(self):
        """An empty coverage list means no territories, never all of them --
        so a manager row with an email and no regions is somebody who would
        receive an empty report every morning."""
        # role, key, region, name, email, regions_covered -- the last empty
        text = 'manager,,,A Manager,mgr@apisindia.com,\n'
        d = self.client.post('/api/sales/recipients/import/', {'text': text},
                             content_type='application/json').json()
        self.assertEqual(d['added'], 0)
        self.assertEqual(len(d['skipped']), 1)
        self.assertIn('covers no territory', d['skipped'][0])

    def test_pasting_the_filled_in_template_adds_everybody(self):
        text = ('role,key_or_regions,name,email\n'
                'head,GTR01,Mohinder Sharma,m@apisindia.com\n'
                'head,GTR04 B,Gulshan Kumar,g@apisindia.com\n')
        d = self.client.post('/api/sales/recipients/import/', {'text': text},
                             content_type='application/json').json()
        self.assertEqual(d['added'], 2)
        self.assertEqual(d['skipped'], [])

    def test_one_bad_line_does_not_throw_away_the_good_ones(self):
        """A silent partial import is worse than either accepting or
        refusing the lot."""
        text = ('head,GTR01,Mohinder,m@apisindia.com\n'
                'head,GTR99,Nobody,x@apisindia.com\n'
                'head,GTR04 B,Gulshan,not-an-email\n')
        d = self.client.post('/api/sales/recipients/import/', {'text': text},
                             content_type='application/json').json()
        self.assertEqual(d['added'], 1)
        self.assertEqual(len(d['skipped']), 2)

    def test_a_blank_email_is_a_row_not_filled_in_yet_not_an_error(self):
        text = 'head,GTR01,Mohinder,\n'
        d = self.client.post('/api/sales/recipients/import/', {'text': text},
                             content_type='application/json').json()
        self.assertEqual(d['added'], 0)
        self.assertEqual(d['skipped'], [])

    def test_a_manager_covers_the_regions_named_against_them(self):
        text = 'manager,GTR01;GTR04 B,The Manager,mgr@apisindia.com\n'
        d = self.client.post('/api/sales/recipients/import/', {'text': text},
                             content_type='application/json').json()
        self.assertEqual(d['added'], 1)
        rec = ReportRecipient.objects.get(role='manager')
        self.assertEqual(rec.regions, ['GTR01', 'GTR04 B'])

    def test_a_manager_cannot_be_given_a_region_that_is_not_on_the_sheet(self):
        """Guessing a reporting line from a spreadsheet is how somebody
        receives a territory that is not theirs."""
        d = self.client.post('/api/sales/recipients/import/',
                             {'text': 'manager,GTR77,Nobody,n@apisindia.com\n'},
                             content_type='application/json').json()
        self.assertEqual(d['added'], 0)
        self.assertEqual(len(d['skipped']), 1)

    def test_covering_everything_has_to_be_said_deliberately(self):
        """An empty region list must not quietly mean the whole company."""
        self.client.post('/api/sales/recipients/import/',
                         {'text': 'manager,ALL,National Head,nh@apisindia.com\n'},
                         content_type='application/json')
        rec = ReportRecipient.objects.get(role='manager')
        self.assertTrue(rec.covers_all)
        self.assertEqual(rec.covered_regions(['GTR01', 'GTR04 B']),
                         ['GTR01', 'GTR04 B'])
        rec.covers_all = False; rec.regions = []; rec.save()
        self.assertEqual(rec.covered_regions(['GTR01', 'GTR04 B']), [])

    def test_the_same_person_is_not_added_twice(self):
        """A duplicated row means two identical files in one inbox each
        morning, which reads as the system being broken."""
        for _ in range(2):
            self.client.post('/api/sales/recipients/edit/',
                             {'role': 'head', 'head_key': 'GTR01',
                              'email': 'm@apisindia.com'},
                             content_type='application/json')
        self.assertEqual(ReportRecipient.objects.count(), 1)


class TheManagersReport(TestCase):

    def setUp(self):
        upload(review_workbook([GTR01, GTR04B]))

    def test_it_adds_the_territories_rather_than_taking_the_sheets_total(self):
        """A group covering part of a channel has no subtotal on the sheet,
        and taking the one that is there reports the whole channel as
        theirs."""
        total = ['GT Total', '', '', 999, 9999.0, 9999.0, 999.0, 9999.0, 9999.0,
                 0, 0, 0, 9999.0, 9999.0, 0, 0, 9999.0, 9999.0, 0, 0]
        ReviewSnapshot.objects.all().delete()
        upload(review_workbook([GTR01, GTR04B, total]))
        d = self.client.get('/api/sales/review/team/?regions=GTR01,GTR04 B').json()
        self.assertAlmostEqual(d['totals']['mtd_primary'] / 100_000,
                               83.91 + 110.33, places=2)

    def test_it_covers_only_the_regions_asked_for(self):
        d = self.client.get('/api/sales/review/team/?regions=GTR01').json()
        self.assertEqual(d['regions'], ['GTR01'])
        self.assertAlmostEqual(d['totals']['mtd_primary'] / 100_000, 83.91, places=2)

    def test_the_file_is_a_whole_html_document(self):
        r = self.client.get('/api/sales/review/team/file/?regions=GTR01,GTR04 B'
                            '&name=North%20Group')
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertTrue(body.startswith('<!doctype html>'))
        self.assertIn('North Group', body)
        self.assertIn('Mohinder Sharma', body)
        self.assertIn('Gulshan Kumar', body)

    def test_it_is_self_contained_like_the_head_report(self):
        body = self.client.get(
            '/api/sales/review/team/file/?regions=GTR01').content.decode()
        self.assertNotIn('<script', body.lower())
        self.assertNotIn('http://', body)

    def test_asking_for_regions_that_are_not_there_says_so(self):
        r = self.client.get('/api/sales/review/team/?regions=GTR77')
        self.assertEqual(r.status_code, 404)


class TheCoveringEmail(TestCase):

    def setUp(self):
        upload(review_workbook([GTR01, GTR04B]))

    def _d(self, head):
        return self.client.get(f'/api/sales/review/report/?head={head}').json()

    def test_the_subject_carries_the_figure_that_decides_whether_to_open_it(self):
        subject, _ = REPORT.email_for(self._d('GTR01'))
        self.assertIn('GTR01', subject)
        self.assertIn('74%', subject)

    def test_the_body_is_addressed_to_a_person_not_a_region(self):
        _, body = REPORT.email_for(self._d('GTR01'))
        self.assertIn('Hello Mohinder', body)

    def test_a_head_past_plan_is_not_told_what_the_year_still_needs(self):
        """It reads as the system refusing to acknowledge a good month."""
        _, body = REPORT.email_for(self._d('GTR04 B'))
        self.assertIn('past plan', body)
        self.assertNotIn('a month across', body)

    def test_a_head_behind_plan_is_told_what_the_year_needs(self):
        _, body = REPORT.email_for(self._d('GTR01'))
        self.assertIn('187.16', body)

    def test_the_manager_email_names_who_needs_attention(self):
        d = self.client.get('/api/sales/review/team/?regions=GTR01,GTR04 B'
                            '&name=North').json()
        subject, body = REPORT.team_email_for(d)
        self.assertIn('North', subject)
        self.assertIn('1 of 2 territories', body)

    def test_nothing_here_sends_anything(self):
        """Composing the list and sending to sixteen real people are separate
        decisions, and the second is made deliberately."""
        from django.core import mail
        self.client.get('/api/sales/review/report/file/?head=GTR01')
        self.client.get('/api/sales/review/bundle/')
        self.assertEqual(len(mail.outbox), 0)
