"""The workbook renames itself every month. The import must not care.

Both failures here were real, found when the 8th-October file was uploaded and
nothing happened: the dashboard did not move and the morning reports kept
serving the 30th of September.

Neither failed loudly. The dump tab was ignored in silence, and the review
sheet was refused with a message about a missing column that was sitting right
there under a different name. The reports went on working, which is what made
it look like an upload problem rather than a parsing one -- the previous
snapshot was still the newest one that existed, so every mail built from it
was correct, just a week stale.

So these tests use the business's own strings, verbatim, from both months.
"""
import io
from datetime import date

from django.test import Client as RawClient, SimpleTestCase, TestCase

from . import review as REVIEW
from .ingest import pick_sheets, undated
from .models import ReviewSnapshot
from .views.auth import SALESIQ_SUPER_ADMIN, issue_session


# The tabs of "Primary Master till 08th Oct'2026", in order.
OCTOBER_TABS = ['Region Summary', 'Sub-Region Summary', 'Region Cat Matrix',
                'REGION & BRAND WISE', 'Sub-Region Cat Matrix',
                "YTD,AOP vs.ACH", "Oct'26 Pri Sales Dump"]

# Its "Region Summary" header row, verbatim.
OCTOBER_HEADER = [
    'CHANEL TYPE', 'REGION', 'GTR HEAD', 'NO OF SFO', 'LMTD', 'Oct-26 AOP',
    'MTD Pri Oct-26', 'YESTERDAY BILLING', 'MTD Sec sales', 'AOP ACH %',
    'Growth Over LM', 'Backlog TGT FTM', 'YTD AOP', 'YTD ACH', 'YTD ACH %',
    'BACKLOG TGT YTD', "FY'26-27 AOP", "FY'26-27 ACH", 'FY ACH %', 'BACKLOG FY']

# September's, which must keep working.
SEPTEMBER_HEADER = [
    'CHANEL TYPE', 'REGION', 'GTR HEAD', 'NO OF SFO', 'LMTD', 'Sep-26 AOP',
    'MTD Sep-26 PRI SALES', 'YESTERDAY BILLING', 'MTD Sec sales', 'AOP ACH %',
    'Growth Over LM', 'Backlog TGT FTM', 'YTD AOP', 'YTD ACH', 'YTD ACH %',
    'BACKLOG TGT YTD', "FY'26-27 AOP", "FY'26-27 ACH", 'FY ACH %', 'BACKLOG FY']


class TheDumpTabCarriesTheMonthInItsName(SimpleTestCase):
    """"Oct'26 Pri Sales Dump" is the same tab as "Pri Sales Dump"."""

    def test_the_dated_dump_tab_is_picked_up(self):
        chosen, _ = pick_sheets(OCTOBER_TABS)
        self.assertEqual(chosen.get('dump'), "Oct'26 Pri Sales Dump")

    def test_all_three_roles_are_found_in_the_real_workbook(self):
        chosen, _ = pick_sheets(OCTOBER_TABS)
        self.assertEqual(sorted(chosen), ['aop', 'dump', 'review'])

    def test_the_month_is_stripped_from_either_end(self):
        for name in ("oct 26 pri sales dump", "pri sales dump oct 26",
                     "sept 2026 pri sales dump", "pri sales dump"):
            self.assertEqual(undated(name), 'pri sales dump', name)

    def test_a_tab_that_merely_starts_with_a_month_word_is_left_alone(self):
        """"May" is a month and also an English word; stripping needs digits
        beside it, or a tab could lose a real part of its name."""
        self.assertEqual(undated('may figures summary'), 'may figures summary')

    def test_the_summary_tabs_are_still_ignored(self):
        """Stripping months must not make unrelated tabs start matching."""
        _, ignored = pick_sheets(OCTOBER_TABS)
        self.assertIn('Sub-Region Summary', ignored)
        self.assertIn('Region Cat Matrix', ignored)


class TheMonthColumnMovesAround(SimpleTestCase):
    """September wrote "MTD Sep-26 PRI SALES"; October wrote "MTD Pri Oct-26"."""

    def test_october_header_is_read_completely(self):
        cols, _, as_of, unknown = REVIEW.map_columns(OCTOBER_HEADER)
        self.assertEqual(unknown, [])
        self.assertEqual(as_of, date(2026, 10, 1))
        self.assertIn('mtd_primary', cols)

    def test_october_sheet_is_not_refused_for_a_missing_column(self):
        """The 400 this used to return named "mtd primary" as missing while
        the column sat in the sheet under another word order."""
        cols, _, _, _ = REVIEW.map_columns(OCTOBER_HEADER)
        self.assertEqual(
            [f for f in ('head_name', 'mtd_primary', 'month_target')
             if f not in cols], [])

    def test_september_header_still_reads_the_same_way(self):
        cols, _, as_of, unknown = REVIEW.map_columns(SEPTEMBER_HEADER)
        self.assertEqual(unknown, [])
        self.assertEqual(as_of, date(2026, 9, 1))
        self.assertIn('mtd_primary', cols)

    def test_primary_and_secondary_are_not_confused(self):
        """"MTD Sec sales" sits beside it and must never win the primary
        slot -- that would report secondary numbers as primary in every
        head's report, which no figure on the page would contradict."""
        for header in (OCTOBER_HEADER, SEPTEMBER_HEADER):
            cols, _, _, _ = REVIEW.map_columns(header)
            self.assertNotEqual(cols['mtd_primary'], cols['mtd_secondary'])
            self.assertEqual(header[cols['mtd_secondary']], 'MTD Sec sales')

    def test_both_months_are_still_recognised_as_the_review_sheet(self):
        for header in (OCTOBER_HEADER, SEPTEMBER_HEADER):
            self.assertTrue(REVIEW.looks_like_review_sheet(header))

    def test_the_wordings_seen_so_far_all_resolve_to_the_same_column(self):
        for text, month in (
                ('MTD Pri Oct-26',        date(2026, 10, 1)),
                ('MTD Oct-26 PRI SALES',  date(2026, 10, 1)),
                ('MTD PRIMARY SALES',     None),
                ('MTD Primary Sep-26',    date(2026, 9, 1)),
                ('MTD Sept-26 PRI SALES', date(2026, 9, 1))):
            cols, _, as_of, _ = REVIEW.map_columns(['GTR HEAD', text])
            self.assertIn('mtd_primary', cols, text)
            self.assertEqual(as_of, month, text)


class TheFileIsNamedForTheDayItCloses(SimpleTestCase):

    def test_the_october_filename_dates_the_snapshot(self):
        self.assertEqual(
            REVIEW.date_from_name("Primary Master till 08th Oct'2026.xlsx"),
            date(2026, 10, 8))

    def test_septembers_filename_still_reads(self):
        self.assertEqual(
            REVIEW.date_from_name("Primary Master till 30th Sept'2026.xlsx"),
            date(2026, 9, 30))


class TheOctoberWorkbookUploadsEndToEnd(TestCase):
    """The unit tests above prove the headers parse. This proves the file
    actually lands: a snapshot dated the day the business closed it, and the
    dump rows on the dashboard behind it.

    Both halves failed on the real 8th-October file, in different ways and
    both quietly -- so neither is covered by asserting a 200.
    """

    # October moved MTD Pri ahead of YESTERDAY BILLING. Mapping is by name,
    # so the order is part of what is being tested.
    ROWS = [
        ['GT', 'GTR01', 'MOHINDER SHARMA', 22, 57.61, 119.78, 38.31, 4.27,
         22.41, .32, -.32, 81.47, 739.46, 262.44, .35, 477.02,
         1423.73, 262.44, .18, 1161.29],
        ['GT', 'GTR02', 'RAMESH SINGH', 43, 85.89, 194.47, 6.98, 4.73,
         29.63, .04, .49, 187.49, 1200.58, 377.19, .31, 823.39,
         2311.54, 377.19, .16, 1934.35],
    ]

    def setUp(self):
        self.c = RawClient()
        self.c.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session(
            SALESIQ_SUPER_ADMIN)

    def workbook(self):
        import openpyxl
        from .tests import a_row, header_names
        wb = openpyxl.Workbook()
        wb.remove(wb.active)

        ws = wb.create_sheet(title='Region Summary')
        # The merged banner the real sheet carries above its header row.
        ws.append([None, None, None, None, "MTD FOR OCT'26"])
        ws.append(list(OCTOBER_HEADER))
        for r in self.ROWS:
            ws.append(list(r))

        dump = wb.create_sheet(title="Oct'26 Pri Sales Dump")
        names = header_names()
        dump.append(names)
        for r in (a_row(), a_row(**{'Customer No.': 'C-2',
                                    'Taxable Amount': 5000})):
            dump.append([r.get(h) for h in names])

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        buf.name = "Primary Master till 08th Oct'2026.xlsx"
        return buf

    def upload(self):
        r = self.c.post('/api/sales/upload/', {'file': self.workbook()})
        self.assertEqual(r.status_code, 200, r.content[:600])
        return r.json()

    def test_the_file_is_accepted(self):
        self.upload()

    def test_a_snapshot_is_created_for_the_day_on_the_filename(self):
        """The whole complaint: the reports kept serving 30 September because
        no newer snapshot ever came into existence."""
        self.upload()
        snap = ReviewSnapshot.objects.first()
        self.assertIsNotNone(snap, 'no snapshot was created at all')
        self.assertEqual(snap.as_of_date, date(2026, 10, 8))
        self.assertEqual(snap.as_of_month, date(2026, 10, 1))

    def test_the_snapshot_holds_the_heads_from_the_sheet(self):
        self.upload()
        snap = ReviewSnapshot.objects.first()
        # Stored title-cased, not as the sheet shouts them.
        names = {r.head_name.upper() for r in snap.rows.all()}
        self.assertIn('MOHINDER SHARMA', names)
        self.assertIn('RAMESH SINGH', names)

    def test_the_month_to_date_figure_is_the_primary_one(self):
        """Not the secondary column sitting beside it."""
        self.upload()
        row = ReviewSnapshot.objects.first().rows.get(region='GTR01')
        self.assertAlmostEqual(float(row.mtd_primary), 38.31 * 100000, places=2)
        self.assertAlmostEqual(float(row.mtd_secondary), 22.41 * 100000, places=2)

    def test_the_dated_dump_tab_reaches_the_dashboard(self):
        """The tab named "Oct'26 Pri Sales Dump" was ignored in silence,
        which looks identical to a workbook that had no dump in it."""
        from .models import SalesRecord
        before = SalesRecord.objects.count()
        self.upload()
        self.assertGreater(SalesRecord.objects.count(), before,
                           'the dump tab was skipped')

    def test_the_newest_snapshot_is_the_one_a_report_uses(self):
        """Ordering is -as_of_date, so an older September snapshot must not
        win once October exists."""
        ReviewSnapshot.objects.create(filename='September.xlsx',
                                      as_of_date=date(2026, 9, 30),
                                      as_of_month=date(2026, 9, 1))
        self.upload()
        self.assertEqual(ReviewSnapshot.objects.first().as_of_date,
                         date(2026, 10, 8))
