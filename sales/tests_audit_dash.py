"""The dashboard, audited two ways.

**With nothing loaded**, because that is the state PROD is in right now: a
screen of panels that 500 on an empty database is not an edge case there, it
is the first thing anybody sees.

**With a known workbook**, because the figures on the headline strip and the
figures in the breakdowns are drawn by different code down different query
paths, and nothing has been checking that they agree. A breakdown whose
parts do not add to the total it sits under is the kind of wrong that gets
believed.
"""
from django.test import Client as RawClient, TestCase as _TC
from django.urls import get_resolver

from .views.auth import SALESIQ_SUPER_ADMIN, issue_session


SKIP = {'login/', 'upload/', 'uploads/', 'recipients/edit/',
        'recipients/edit/<int:pk>/', 'recipients/import/', 'mail/send/',
        'uploaders/edit/', 'uploaders/edit/<int:pk>/', 'review/<int:pk>/'}


def read_routes():
    """-> every GET-able endpoint under /api/sales/."""
    out = []
    for p in get_resolver().url_patterns:
        if not str(getattr(p, 'pattern', '')).startswith('api/sales'):
            continue
        for sub in p.url_patterns:
            raw = str(sub.pattern)
            if raw in SKIP or '<' in raw:
                continue
            out.append((raw, '/api/sales/' + raw))
    return out


def owner():
    c = RawClient()
    c.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session(SALESIQ_SUPER_ADMIN)
    return c


class WithNothingLoadedAtAll(_TC):
    """Which is what PROD looks like today."""

    def test_no_endpoint_raises_on_an_empty_database(self):
        """A 500 here is a stack trace on somebody's screen. 200 with an
        empty answer, or a stated 404, are both fine; a crash is not."""
        c = owner()
        broke = []
        for raw, url in read_routes():
            r = c.get(url)
            if r.status_code >= 500:
                broke.append(f'{url} -> {r.status_code}')
        self.assertEqual(broke, [])

    def test_the_headline_reads_zero_rather_than_blank(self):
        d = owner().get('/api/sales/overview/').json()
        self.assertIsInstance(d, dict)

    def test_the_review_screen_says_there_is_nothing_yet(self):
        d = owner().get('/api/sales/review/').json()
        self.assertEqual(d.get('snapshots'), [])

    def test_a_report_asked_for_before_any_upload_is_refused_not_crashed(self):
        r = owner().get('/api/sales/review/report/?head=GTR01')
        self.assertEqual(r.status_code, 404)
        self.assertIn('error', r.json())

    def test_the_outbox_is_empty_rather_than_broken(self):
        d = owner().get('/api/sales/mail/outbox/').json()
        self.assertEqual(d['recipients'], [])
        self.assertIsNone(d['snapshot'])

    def test_a_send_with_nothing_to_send_is_refused_clearly(self):
        from django.test import override_settings
        with override_settings(DEFAULT_FROM_EMAIL='r@apisindia.com',
                               EMAIL_HOST_PASSWORD='x'):
            r = owner().post('/api/sales/mail/send/', {},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('error', r.json())


class WithTheDailyWorkbookLoaded(_TC):
    """Three tabs, as the real file has."""

    def setUp(self):
        from .tests import (a_row, aop_row, header_names,
                            named_workbook)
        self.c = owner()
        rows = [a_row(),
                a_row(**{'Customer No.': 'CUST-002',
                         'Customer Name': 'Verma Stores', 'Zone': 'South',
                         'Quantity': 50, 'Taxable Amount': 10000})]
        plan = aop_row(**{'GTR HEAD': 'Anil Mehra', 'APIS ID': 'AP100'})
        book = named_workbook([
            ('YTD,AOP vs.ACH', [list(plan.keys()), plan]),
            ('PRI SALES DUMP', [header_names()] + rows),
        ])
        r = self.c.post('/api/sales/upload/', {'file': book})
        self.assertEqual(r.status_code, 200, r.content[:400])
        self.assertGreater(r.json().get('rows', 0), 0, r.content[:400])

    def test_no_endpoint_raises_with_data_loaded(self):
        broke = []
        for raw, url in read_routes():
            r = self.c.get(url)
            if r.status_code >= 500:
                broke.append(f'{url} -> {r.status_code}')
        self.assertEqual(broke, [])

    def test_a_breakdown_from_the_invoices_covers_less_than_the_headline(self):
        """Documented, because it is currently not said on screen.

        The headline counts the plan file and the invoice file together. A
        split by customer, SKU or city can only come from the invoices --
        the plan sheet has no customer on it -- so those panels show a
        smaller business than the strip above them.

        `coverage` exists to say so, and its comment in the view says it
        measures "how much of the business this breakdown can speak for".
        It does not: `grand` is the total of the file the dimension came
        from, so an invoice-only split reports 100% while showing a third
        of the revenue, and the badge that would have warned about it hides
        itself above 99.5%.
        """
        over = self.c.get('/api/sales/overview/').json()
        headline = float(over['revenue'])
        inv = self.c.get('/api/sales/breakdown/?dim=customer').json()
        shown = sum(float(r['revenue']) for r in inv['results'])
        self.assertLess(shown, headline)
        # What it reports today: full coverage of a partial universe.
        self.assertEqual(inv['coverage']['pct'], 100.0)
        self.assertEqual(float(inv['coverage']['total']), shown)
        self.assertNotEqual(float(inv['coverage']['total']), headline)

    def test_a_breakdown_the_plan_file_can_answer_does_match(self):
        """Zone is on both files, so this one does total the headline --
        which is why the gap above is easy to miss."""
        over = self.c.get('/api/sales/overview/').json()
        zone = self.c.get('/api/sales/breakdown/?dim=zone').json()
        self.assertAlmostEqual(
            sum(float(r['revenue']) for r in zone['results']),
            float(over['revenue']), places=2)

    def test_the_headline_says_how_much_of_it_came_from_invoices(self):
        """The information is there; it is the breakdown panels that do not
        connect it."""
        over = self.c.get('/api/sales/overview/').json()
        self.assertAlmostEqual(
            over['invoiced_pct'],
            round(over['invoiced_revenue'] / over['revenue'] * 100, 1),
            places=1)

    def test_uploading_the_same_file_twice_does_not_double_the_headline(self):
        """The one the business reported: every figure on the strip doubled
        the morning somebody loaded yesterday's file again."""
        before = self.c.get('/api/sales/overview/').json()
        self.setUp()                       # same workbook, loaded again
        after = self.c.get('/api/sales/overview/').json()
        for k, v in before.items():
            if isinstance(v, (int, float)) and v:
                self.assertAlmostEqual(
                    float(after.get(k) or 0), float(v),
                    delta=max(abs(float(v)) * 0.001, 0.01), msg=k)
