"""SalesIQ ingestion — what counts as a sale, and what the dump columns mean.

The Pre-Sales Dump is an ERP export, not a sales report: it carries cancelled
invoices and credit memos in the same sheet as live billing, splits the money
across a dozen columns, and repeats several figures in two forms. These tests
hold the decisions that turn it into numbers a person can act on.
"""
import io

import openpyxl
from datetime import date

from django.test import Client, TestCase

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

    def test_a_credit_memo_is_flagged_as_a_return(self):
        upload(a_workbook([a_row(), a_row(**{'Type': 'Credit Memo'})]))
        self.assertEqual(SalesRecord.objects.filter(is_return=True).count(), 1)
        self.assertTrue(is_return_type('Credit Memo'))
        self.assertTrue(is_return_type('Sales Credit Memo'))
        self.assertFalse(is_return_type('Invoice'))

    def test_the_operator_is_told_what_was_found(self):
        """Silently dropping cancelled rows is right; not saying so is not."""
        r = upload(a_workbook([a_row(**{'Cancelled': 'Yes'}),
                               a_row(**{'Type': 'Credit Memo'})]))
        blob = ' '.join(r.json().get('warnings', []))
        self.assertIn('cancelled', blob.lower())
        self.assertIn('credit memo', blob.lower())


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
        for junk in ('', None, 'XX', '00'):
            self.assertEqual(state_from_code(junk), '', junk)

    def test_a_dump_row_lands_in_a_state(self):
        upload(a_workbook([a_row(**{'Cust.State Code': '07'})]))
        self.assertEqual(SalesRecord.objects.get().state, 'Delhi')

    def test_a_sheet_that_names_its_own_state_keeps_that_spelling(self):
        """Filling in a blank is help; overwriting what someone typed is not."""
        buf = a_workbook([{'Date': '2026-04-05', 'State': 'NCR Delhi', 'Amount': 500}],
                         headers=['Date', 'State', 'Amount'])
        upload(buf)
        self.assertEqual(SalesRecord.objects.get().state, 'NCR Delhi')


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
        d = upload(a_workbook([a_row(),
                               a_row(**{'Cancelled': 'Yes'}),
                               a_row(**{'Type': 'Credit Memo'})])).json()
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
        self.assertEqual(float(cy.plan_achievement), 90000)

    def test_last_year_is_an_actual_with_no_plan_against_it(self):
        upload(aop_workbook([aop_row()]))
        ly = SalesRecord.objects.get(period=date(2025, 4, 1))
        self.assertEqual(float(ly.target_amount), 0)
        self.assertEqual(float(ly.plan_achievement), 80000)

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
        total = sum(float(r.plan_achievement) for r in SalesRecord.objects.all())
        self.assertEqual(total, 170000)         # 90,000 + 80,000, nothing else
        self.assertFalse(SalesRecord.objects.filter(net_amount=888888).exists())

    def test_the_operator_is_told_the_totals_were_skipped(self):
        d = upload(aop_workbook([aop_row()])).json()
        self.assertEqual(d['file_kind'], 'aop')
        blob = ' '.join(d['warnings']).lower()
        self.assertIn('skipped on purpose', blob)
        self.assertIn('ytd ach', blob)


class WhichFileTheSalesFigureComesFrom(TestCase):
    """Both files describe the same money. Summing both would double it."""

    def test_the_aop_sheet_alone_still_shows_sales(self):
        upload(aop_workbook([aop_row()]))
        cy = SalesRecord.objects.get(period=date(2026, 4, 1))
        self.assertEqual(float(cy.net_amount), 90000)

    def test_invoice_detail_takes_over_when_it_arrives(self):
        upload(aop_workbook([aop_row()]))
        upload(a_workbook([a_row()]))           # the Pre-Sales Dump
        plan = SalesRecord.objects.filter(source='plan')
        self.assertTrue(all(float(r.net_amount) == 0 for r in plan),
                        'the AOP sheet is still adding its own actuals on top')
        # The plan itself survives — that is the whole reason to load it.
        self.assertEqual(float(plan.get(period=date(2026, 4, 1)).target_amount), 100000)
        # ...and its achievement is kept for reconciliation.
        self.assertEqual(float(plan.get(period=date(2026, 4, 1)).plan_achievement), 90000)

    def test_the_order_the_files_arrive_in_does_not_matter(self):
        upload(a_workbook([a_row()]))           # dump first this time
        upload(aop_workbook([aop_row()]))
        plan = SalesRecord.objects.filter(source='plan')
        self.assertTrue(all(float(r.net_amount) == 0 for r in plan))

    def test_removing_the_invoice_data_hands_the_figures_back(self):
        upload(aop_workbook([aop_row()]))
        r = upload(a_workbook([a_row()])).json()
        Client().delete(f"/api/sales/uploads/?id={r['upload_id']}")
        cy = SalesRecord.objects.get(source='plan', period=date(2026, 4, 1))
        self.assertEqual(float(cy.net_amount), 90000,
                         'the dashboard would show zero with a good plan file loaded')

    def test_the_total_is_not_doubled_when_both_files_are_loaded(self):
        upload(aop_workbook([aop_row(cy=20000)]))
        upload(a_workbook([a_row()]))           # 20,000 taxable
        d = Client().get('/api/sales/overview/').json()
        rev = float(d.get('total_revenue') or d.get('revenue') or 0)
        self.assertEqual(rev, 20000, f'counted twice: {rev}')
