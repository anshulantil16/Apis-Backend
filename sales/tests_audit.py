"""A standing audit of every number that leaves this app in a report or a mail.

Not a unit test of one function: a check that the figures printed on the page
are the figures on the sheet, and that the awkward rows the live file actually
contains do not break the drawing. Run as part of the suite.

The rows below are copied from the circulated mail of 30.09.26 rather than
invented, so a change that quietly moves a figure fails here against the
business's own published numbers.
"""
import io
import re
import zipfile
from datetime import date

from django.test import TestCase as _TC

from . import report as REPORT
from . import mail as MAIL
from .models import ReviewSnapshot


# -- the sheet, as circulated ---------------------------------------------
HEADERS = [
    'CHANEL TYPE', 'REGION', 'GTR HEAD', 'NO OF SFO', 'LMTD', 'Sep-26 AOP',
    'YESTERDAY BILLING', 'MTD Sep-26 PRI SALES', 'MTD Sec sales', 'AOP ACH %',
    'Growth Over LM', 'Backlog TGT FTM', 'YTD AOP', 'YTD ACH', 'YTD ACH %',
    'BACKLOG TGT YTD', "FY'26-27 AOP", "FY'26-27 ACH", 'FY ACH %', 'BACKLOG FY',
]

# channel, region, head, sfo, lmtd, aop, yest, pri, sec, ach, growth, bl,
# ytd aop, ytd ach, ytd%, bl ytd, fy aop, fy ach, fy%, bl fy
SHEET = [
    ['GT', 'GTR01', 'MOHINDER SHARMA', 20, 57.61, 114.08, 21.09, 83.91, 77.50,
     .74, .46, 30.17, 619.69, 300.75, .49, 318.94, 1423.73, 300.75, .21, 1122.98],
    ['GT', 'GTR02', 'RAMESH SINGH', 41, 85.89, 185.22, 32.58, 106.17, 123.33,
     .57, .24, 79.05, 1006.11, 370.21, .37, 635.90, 2311.54, 370.21, .16, 1941.33],
    ['GT', 'GTR03 A', 'ASIF ALI ANSARI', 53, 210.70, 253.38, 63.11, 254.91, 244.63,
     1.01, .21, -1.53, 1373.61, 1228.28, .89, 145.33, 3165.75, 1228.28, .39, 1937.47],
    ['GT', 'GTR03 B', 'ARUN MISHRA (NSH)', 28, 79.26, 112.93, 29.47, 85.07, 86.71,
     .75, .07, 27.86, 616.22, 395.54, .64, 220.68, 1405.90, 395.54, .28, 1010.36],
    ['GT', 'GTR04 A', 'ARNAB GHOSH', 27, 99.47, 130.29, 3.92, 93.17, 114.19,
     .72, -.06, 37.11, 707.74, 480.28, .68, 227.46, 1626.03, 480.28, .30, 1145.75],
    # The handover line. Blank region (merged upward), sales against no AOP.
    ['', '', 'ARUN MISHRA ( ARNAB GHOSH)', 0, 0.00, 0.00, 15.22, 30.30, 0.00,
     0, 0, -30.30, 0.00, 30.30, 0, -30.30, 0.00, 30.30, 0, -30.30],
    ['GT', 'GTR04 B', 'GULSHAN KUMAR', 23, 86.86, 91.44, 6.25, 110.33, 94.54,
     1.21, .27, -18.89, 496.69, 454.59, .92, 42.10, 1141.15, 454.59, .40, 686.56],
    ['GT', 'GTR05', 'SHAKIL AHMED', 42, 155.68, 181.07, 4.16, 181.69, 157.66,
     1.00, .17, -0.63, 983.56, 869.77, .88, 113.79, 2259.75, 869.77, .38, 1389.98],
    ['GT', 'GTR06 A', 'SANJAY SINGH', 25, 77.55, 98.62, 3.70, 100.15, 94.57,
     1.02, .29, -1.53, 535.71, 484.04, .90, 51.67, 1230.80, 484.04, .39, 746.75],
    ['GT', 'GTR06 B', 'VINOD SHAH', 19, 39.81, 35.27, 9.95, 48.92, 43.51,
     1.39, .23, -13.65, 191.60, 173.34, .90, 18.25, 440.20, 173.34, .39, 266.85],
    ['GT', 'GTR07', 'B BHASKAR', 37, 210.33, 178.72, 15.48, 144.27, 154.63,
     .81, -.31, 34.46, 970.84, 789.40, .81, 181.44, 2230.51, 789.40, .35, 1441.11],
    ['GT', 'GTR08', 'REVANASIDDAPPA PATIL', 34, 82.44, 171.43, 20.49, 69.93, 103.70,
     .41, -.15, 101.51, 928.34, 467.14, .50, 461.20, 2135.64, 467.14, .22, 1668.50],
    ['GT', 'GTR09', 'VASANTHKUMAR D', 29, 56.75, 148.80, 12.02, 69.69, 68.25,
     .47, .23, 79.11, 808.31, 285.08, .35, 523.23, 1857.10, 285.08, .15, 1572.02],
    ['GT Total', '', '', 378, 1242.35, 1701.24, 237.44, 1378.51, 1363.23,
     .81, .11, 322.73, 9238.41, 6328.72, .69, 2909.69, 21228.10, 6328.72, .30, 14899.38],
]

LAKH = 100_000


def _workbook(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Region Summary'
    ws.append([None, None, None, None, "MTD FOR SEPT'26"])
    ws.append(list(HEADERS))
    for r in rows:
        ws.append(list(r))
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    buf.name = 'Daily MIS.xlsx'
    return buf


class EveryFigureAgainstTheSheet(_TC):
    """Line by line, against the mail of 30.09.26."""

    @classmethod
    def setUpTestData(cls):
        from .tests import upload
        upload(_workbook(SHEET))

    def setUp(self):
        from .tests import Client
        self.client = Client()

    def _head(self, key):
        return self.client.get(f'/api/sales/review/report/?head={key}').json()

    # -- the figures the sheet itself prints ------------------------------
    def test_every_head_s_achievement_matches_the_sheet(self):
        """The sheet prints ACH % for all thirteen lines. Ours must agree
        with every one of them, not with most of them."""
        snap = ReviewSnapshot.objects.first()
        for row in SHEET[:-1]:
            name = row[2]
            r = snap.rows.get(head_name__iexact=name.strip())
            stated = row[9]
            if not row[5]:                      # no AOP: no percentage
                self.assertIsNone(r.month_pct, name)
                continue
            self.assertEqual(round(r.month_pct), round(stated * 100), name)

    def test_every_head_s_ytd_and_fy_match_the_sheet(self):
        snap = ReviewSnapshot.objects.first()
        for row in SHEET[:-1]:
            r = snap.rows.get(head_name__iexact=row[2].strip())
            for ours, stated, label in ((r.ytd_pct, row[14], 'YTD'),
                                        (r.fy_pct, row[18], 'FY')):
                if not stated:
                    continue
                self.assertAlmostEqual(ours, stated * 100, delta=0.6,
                                       msg=f'{row[2]} {label}')

    def test_every_backlog_matches_the_sheet(self):
        snap = ReviewSnapshot.objects.first()
        for row in SHEET[:-1]:
            r = snap.rows.get(head_name__iexact=row[2].strip())
            for ours, stated, label in ((r.month_backlog, row[11], 'FTM'),
                                        (r.ytd_backlog, row[15], 'YTD'),
                                        (r.fy_backlog, row[19], 'FY')):
                # A paisa, and no more. Excel derives these from the full
                # precision underneath and prints them rounded; we only
                # receive the rounded figures. Anything larger is a real
                # disagreement and the import warns about it.
                self.assertAlmostEqual(ours / LAKH, stated, delta=0.011,
                                       msg=f'{row[2]} backlog {label}')

    def test_our_heads_add_up_to_the_sheet_s_own_gt_total(self):
        """The strongest check available: the business's own addition."""
        snap = ReviewSnapshot.objects.first()
        heads = snap.rows.filter(is_total=False)
        total = SHEET[-1]
        for field, stated in (('mtd_primary', total[7]), ('mtd_secondary', total[8]),
                              ('lmtd', total[4]), ('month_target', total[5]),
                              ('ytd_target', total[12]), ('ytd_actual', total[13]),
                              ('fy_target', total[16]), ('fy_actual', total[17])):
            ours = sum(float(getattr(r, field)) for r in heads) / LAKH
            # Half a paisa per line. The sheet's own total for secondary
            # sales is 1,363.23 where its own rows add to 1,363.22, which is
            # Excel rounding each line before adding them -- their artefact,
            # not ours, and not something to "correct" by preferring our sum
            # silently.
            self.assertAlmostEqual(ours, stated, delta=0.02, msg=field)

    def test_the_sfo_count_adds_up_to_the_sheet_s_own(self):
        snap = ReviewSnapshot.objects.first()
        self.assertEqual(sum(r.sfo_count for r in snap.rows.filter(is_total=False)),
                         SHEET[-1][3])

    # -- the figures we derive --------------------------------------------
    def test_the_annual_backlog_splits_into_two_halves_that_add_back(self):
        for row in SHEET[:-1]:
            if not row[16]:
                continue
            d = self._head(row[2])
            y = d['year']
            self.assertAlmostEqual(
                (y['plan_ahead'] + y['catch_up']) / LAKH, row[19], delta=0.011,
                msg=row[2])

    def test_the_required_run_rate_is_the_backlog_over_the_months_left(self):
        d = self._head('GTR01')
        y = d['year']
        self.assertEqual(y['months_remaining'], 6)
        self.assertAlmostEqual(y['required_monthly'] / LAKH, 1122.98 / 6, places=2)
        self.assertAlmostEqual(y['required_base'] / LAKH,
                               (1423.73 - 619.69) / 6, places=2)

    def test_the_months_of_the_year_are_counted_from_april(self):
        from .views.review import fy_index
        for d, want in ((date(2026, 4, 1), 1), (date(2026, 9, 1), 6),
                        (date(2027, 3, 1), 12), (date(2026, 12, 1), 9)):
            self.assertEqual(fy_index(d), want, str(d))

    def test_per_officer_output_is_the_territory_over_its_own_officers(self):
        d = self._head('GTR01')
        self.assertAlmostEqual(d['productivity']['per_sfo_mtd'] / LAKH,
                               83.91 / 20, places=2)
        self.assertAlmostEqual(d['productivity']['channel_per_sfo_mtd'] / LAKH,
                               1378.51 / 378, places=2)

    def test_a_rank_is_out_of_the_heads_that_have_a_figure(self):
        """A territory with no plan has not come bottom; it has not been
        measured. Ranking it last states something untrue about it."""
        d = self._head('GTR01')
        self.assertEqual(d['rank']['month_pct']['of'], 12)   # 13 rows, one no-plan
        self.assertEqual(d['rank']['growth_pct']['position'], 1)

    def test_heads_on_the_same_percentage_get_the_same_place(self):
        """Otherwise two people doing identically well are told one of them
        did better, and the order is whatever the sort happened to do."""
        snap = ReviewSnapshot.objects.first()
        a = snap.rows.get(region='GTR05')        # 100%
        b = snap.rows.get(region='GTR06 A')      # 102%
        a.mtd_primary = b.mtd_primary = 100 * LAKH
        a.month_target = b.month_target = 100 * LAKH
        a.save(); b.save()
        pa = self._head('GTR05')['rank']['month_pct']['position']
        pb = self._head('GTR06 A')['rank']['month_pct']['position']
        self.assertEqual(pa, pb)

    # -- the awkward rows the live sheet contains -------------------------
    def test_the_handover_line_gets_its_own_report_not_arnab_s(self):
        """Its region cell is merged upward, so it carries GTR04 A -- the
        same key as the head above it. Keyed on region alone, both files in
        the bundle would hold the same person's report."""
        z = zipfile.ZipFile(io.BytesIO(
            self.client.get('/api/sales/review/bundle/').content))
        names = [n for n in z.namelist() if n.endswith('.html')]
        self.assertEqual(len(names), 13)
        who = []
        for n in names:
            m = re.search(r'<h1>([^<]+)</h1>', z.read(n).decode())
            who.append(m.group(1).upper() if m else '?')
        self.assertEqual(len(set(who)), 13, sorted(who))

    def test_no_drawing_is_given_a_negative_size(self):
        """A month with more returned than sold is a real thing and the sheet
        writes it as a minus. A negative width is not drawn by any browser,
        so the chart silently loses its bar."""
        snap = ReviewSnapshot.objects.first()
        r = snap.rows.get(region='GTR07')
        r.mtd_primary = -12.40 * LAKH
        r.save()
        for url in ('/api/sales/review/report/file/?head=GTR07',
                    '/api/sales/review/team/file/?regions=GTR07,GTR01'):
            body = self.client.get(url).content.decode()
            bad = re.findall(r'(?:width|height|r)="(-[\d.]+|nan|NaN|inf)"', body)
            self.assertEqual(bad, [], f'{url}: {bad}')

    def test_no_drawing_is_given_a_negative_size_anywhere_on_the_real_sheet(self):
        for row in SHEET[:-1]:
            snap = ReviewSnapshot.objects.first()
            r = snap.rows.get(head_name__iexact=row[2].strip())
            body = self.client.get(
                f'/api/sales/review/report/file/?head={r.head_name}').content.decode()
            bad = re.findall(r'(?:width|height|r)="(-[\d.]+|nan|NaN|inf)"', body)
            self.assertEqual(bad, [], f'{row[2]}: {bad}')

    def test_a_name_out_of_the_sheet_cannot_become_markup(self):
        snap = ReviewSnapshot.objects.first()
        r = snap.rows.get(region='GTR01')
        r.head_name = '<script>alert(1)</script>'
        r.save()
        body = self.client.get(
            '/api/sales/review/report/file/?head=GTR01').content.decode()
        self.assertNotIn('<script>alert(1)</script>', body)

    # -- the figures that reach the page ----------------------------------
    def test_the_rendered_file_prints_the_sheet_s_own_numbers(self):
        body = self.client.get(
            '/api/sales/review/report/file/?head=GTR01').content.decode()
        for fig in ('83.91', '114.08', '30.17', '619.69', '300.75', '318.94',
                    '1,423.73', '1,122.98', '804.04', '134.01', '187.16',
                    '57.61', '21.09', '77.50', '74%', '49%', '21%', '+46%'):
            self.assertIn(fig, body, fig)

    def test_lakhs_are_grouped_the_indian_way_at_every_size(self):
        for rupees, dp, want in ((83.91e5, 2, '83.91'), (1122.98e5, 2, '1,122.98'),
                                 (31927.46e5, 2, '31,927.46'),
                                 (123456789e5, 2, '12,34,56,789.00'),
                                 (0, 2, '0.00'), (-30.17e5, 2, '-30.17'),
                                 (1234e5, 0, '1,234'), (None, 2, '--')):
            self.assertEqual(REPORT.lakh(rupees, dp), want, str(rupees))

    def test_a_figure_that_rounds_to_minus_nothing_is_not_printed_as_minus(self):
        self.assertEqual(REPORT.lakh(-0.001 * LAKH), '0.00')


class TheMailThatGoesOut(_TC):

    @classmethod
    def setUpTestData(cls):
        from .tests import upload
        upload(_workbook(SHEET))

    def setUp(self):
        from .tests import Client
        self.client = Client()

    def _head(self, key):
        return self.client.get(f'/api/sales/review/report/?head={key}').json()

    def test_a_head_s_mail_carries_their_row_and_nobody_else_s(self):
        """The circulated mail shows every GTR line to everybody. Cutting the
        table to the reader is the whole reason it is built rather than
        pasted, so this is the test that matters most here."""
        _, html = MAIL.for_head(self._head('GTR01'))
        self.assertIn('Mohinder Sharma', html)
        for other in ('Ramesh Singh', 'Gulshan Kumar', 'B Bhaskar',
                      'Vasanthkumar D', 'Asif Ali Ansari'):
            self.assertNotIn(other, html, other)

    def test_a_head_s_mail_carries_their_own_figures(self):
        _, html = MAIL.for_head(self._head('GTR01'))
        for fig in ('83.91', '114.08', '77.50', '57.61', '74%', '+46%',
                    '619.69', '300.75', '1,423.73', '1,122.98'):
            self.assertIn(fig, html, fig)

    def test_the_subject_follows_the_one_already_in_use(self):
        subject, _ = MAIL.for_head(self._head('GTR01'))
        self.assertTrue(subject.startswith('FLASH PRIMARY SALES REPORT (B2C)'),
                        subject)
        self.assertIn('GTR01', subject)

    def test_the_business_s_own_notes_are_carried_verbatim(self):
        _, html = MAIL.for_head(self._head('GTR01'))
        self.assertIn('Sales Return-Damage Expiry', html)
        self.assertIn('In OT channel Primary = Secondary', html)

    def test_a_manager_s_mail_carries_their_territories_and_a_group_line(self):
        d = self.client.get('/api/sales/review/team/?regions=GTR01,GTR02'
                            '&name=North').json()
        _, html = MAIL.for_manager(d)
        self.assertIn('Mohinder Sharma', html)
        self.assertIn('Ramesh Singh', html)
        self.assertIn('Group', html)
        self.assertNotIn('B Bhaskar', html)

    def test_a_manager_s_group_line_adds_their_own_rows(self):
        d = self.client.get('/api/sales/review/team/?regions=GTR01,GTR02').json()
        self.assertAlmostEqual(d['totals']['mtd_primary'] / LAKH,
                               83.91 + 106.17, places=2)
        _, html = MAIL.for_manager(d)
        self.assertIn('190.08', html)
        # and never the sheet's own GT Total, which is the whole channel
        self.assertNotIn('1,378.51', html)

    def test_the_mail_has_no_stylesheet_or_script_to_be_stripped(self):
        """Outlook drops <style>, scripts and anything external. What
        survives it is inline styles on the cells themselves."""
        _, html = MAIL.for_head(self._head('GTR01'))
        self.assertNotIn('<style', html.lower())
        self.assertNotIn('<script', html.lower())
        self.assertNotIn('http://', html)
        self.assertNotIn('class=', html)

    def test_a_head_past_plan_is_not_told_what_the_year_still_needs(self):
        _, html = MAIL.for_head(self._head('GTR04 B'))
        self.assertIn('past plan', html)
        self.assertNotIn('is needed across', html)

    def test_a_head_with_no_plan_is_not_told_they_achieved_nothing(self):
        d = self._head('ARUN MISHRA ( ARNAB GHOSH)')
        self.assertIsNone(d['head']['month_pct'])
        _, html = MAIL.for_head(d)
        self.assertIn('no AOP against this territory', html)
        self.assertNotIn('0%', html)

    def test_the_date_is_written_the_way_the_sheet_writes_it(self):
        snap = ReviewSnapshot.objects.first()
        self.assertEqual(MAIL.as_of({'as_of_date': '2026-09-30'}), '30.09.26')
        self.assertEqual(MAIL.as_of({'as_of_date': None,
                                     'as_of_month_label': 'Sep 2026'}), 'Sep 2026')
        self.assertTrue(snap.as_of_date)

    def test_nothing_in_here_sends_anything(self):
        from django.core import mail as djmail
        MAIL.for_head(self._head('GTR01'))
        self.client.get('/api/sales/review/bundle/')
        self.assertEqual(len(djmail.outbox), 0)
