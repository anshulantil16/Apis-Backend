"""The morning mail: who it is addressed to, and what stops it going.

The failure this file exists for is not a crash. It is a mail that arrives,
correctly formatted, signed off and plausible, carrying somebody else's
numbers under the reader's own name.
"""
import io

from django.core import mail as DJMAIL
from django.test import override_settings

from .models import ReportRecipient, ReviewSnapshot
from .tests import TestCase, Client, GTR01, GTR04B, review_workbook, upload
from .views.recipients import candidates, match_head, unique_key


# The handover line, as it reads on the real sheet: ARUN MISHRA stands in for
# ARNAB GHOSH, and his REGION cell is merged upward -- so GTR04 A is the
# region on BOTH rows and names neither of them.
GTR04A = ['GT', 'GTR04 A', 'ARNAB GHOSH', 18, 40.00, 60.00, 3.00, 37.12,
          37.12, 0.62, -0.07, 22.88, 400.00, 250.00, 0.63, 150.00,
          900.00, 250.00, 0.28, 650.00]
HANDOVER = ['GT', 'GTR04 A', 'ARUN MISHRA ( ARNAB GHOSH)', 9, 10.00, 20.00,
            1.00, 12.00, 12.00, 0.60, 0.20, 8.00, 100.00, 60.00, 0.60, 40.00,
            240.00, 60.00, 0.25, 180.00]

MAIL_ON = dict(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
               DEFAULT_FROM_EMAIL='reports@apisindia.com',
               EMAIL_HOST_PASSWORD='not-a-real-password')


def rows():
    return [r for r in ReviewSnapshot.objects.first().rows.all()
            if not r.is_total]


class AKeyHasToNameOnePerson(TestCase):
    """A region is not unique on this sheet."""

    def setUp(self):
        upload(review_workbook([GTR01, GTR04A, HANDOVER]))

    def test_the_shared_region_is_on_two_rows(self):
        self.assertEqual(len(candidates('GTR04 A', rows())), 2)

    def test_a_key_naming_two_people_is_not_a_match(self):
        """It used to resolve to whichever came first, so the handover line's
        recipient would have been sent Arnab Ghosh's numbers."""
        self.assertIsNone(match_head('GTR04 A', rows()))

    def test_the_template_hands_out_a_key_that_names_one_row(self):
        """Not the region, where the region is shared -- the name, which is
        the only thing that tells the two apart."""
        rs = rows()
        keys = {r.head_name.upper(): unique_key(r, rs) for r in rs}
        self.assertEqual(keys['MOHINDER SHARMA'], 'GTR01')
        # The region is shared, so the key falls back to the name -- the only
        # thing that tells the two GTR04 A rows apart.
        self.assertEqual(keys['ARNAB GHOSH'].upper(), 'ARNAB GHOSH')
        self.assertEqual(keys['ARUN MISHRA ( ARNAB GHOSH)'].upper(),
                         'ARUN MISHRA ( ARNAB GHOSH)')
        for name, key in keys.items():
            self.assertEqual([r.head_name.upper() for r in candidates(key, rs)],
                             [name])

    def test_every_key_in_the_workbook_resolves_to_exactly_one_row(self):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(
            self.client.get('/api/sales/recipients/template/').content))
        rs = rows()
        seen = 0
        for row in wb.worksheets[0].iter_rows(min_row=2, values_only=True):
            if row[0] == 'head' and row[1]:
                self.assertEqual(len(candidates(row[1], rs)), 1, row)
                seen += 1
        self.assertEqual(seen, 3)

    def test_the_report_refuses_an_ambiguous_key_rather_than_guessing(self):
        r = self.client.get('/api/sales/review/report/?head=GTR04%20A')
        self.assertEqual(r.status_code, 400)
        self.assertIn('2 rows', r.json()['error'])

    def test_the_list_says_which_two_people_a_key_could_mean(self):
        ReportRecipient.objects.create(role='head', head_key='GTR04 A',
                                       email='x@apisindia.com', name='X')
        rec = self.client.get('/api/sales/recipients/').json()['recipients'][0]
        self.assertFalse(rec['matched'])
        self.assertEqual(sorted(n.upper() for n in rec['ambiguous']),
                         ['ARNAB GHOSH', 'ARUN MISHRA ( ARNAB GHOSH)'])


@override_settings(**MAIL_ON)
class TheMorningSend(TestCase):

    def setUp(self):
        upload(review_workbook([GTR01, GTR04B]))
        ReportRecipient.objects.create(role='head', head_key='GTR01',
                                       name='Mohinder Sharma',
                                       email='mohinder@apisindia.com')
        ReportRecipient.objects.create(role='head', head_key='GTR04 B',
                                       name='Gulshan Kumar',
                                       email='gulshan@apisindia.com')

    def send(self, **body):
        return self.client.post('/api/sales/mail/send/', body,
                                content_type='application/json')

    def test_the_outbox_shows_what_would_go_without_sending_it(self):
        d = self.client.get('/api/sales/mail/outbox/').json()
        self.assertEqual(d['ready'], 2)
        self.assertEqual(len(DJMAIL.outbox), 0)
        self.assertTrue(all(r['attachments'] for r in d['recipients']))

    def test_each_head_is_sent_their_own_report_and_nobody_elses(self):
        self.send()
        self.assertEqual(len(DJMAIL.outbox), 2)
        by_to = {m.to[0]: m for m in DJMAIL.outbox}
        mine = by_to['mohinder@apisindia.com']
        html = mine.alternatives[0][0]
        self.assertIn('GTR01', html)
        self.assertNotIn('GULSHAN', html.upper())
        names = [n for n, _, _ in mine.attachments]
        self.assertEqual(len(names), 1)
        self.assertIn('GTR01', names[0])
        self.assertNotIn('GULSHAN', names[0].upper())

    def test_a_test_run_goes_to_one_address_and_says_so(self):
        self.send(test_to='anshul@apisindia.com')
        self.assertEqual(len(DJMAIL.outbox), 2)
        self.assertTrue(all(m.to == ['anshul@apisindia.com']
                            for m in DJMAIL.outbox))
        self.assertTrue(all(m.subject.startswith('[TEST')
                            for m in DJMAIL.outbox))

    def test_a_test_run_does_not_mark_the_morning_mail_as_sent(self):
        """Otherwise the screen says sixteen people have today's numbers when
        the only inbox touched was your own."""
        self.send(test_to='anshul@apisindia.com')
        self.assertFalse(ReportRecipient.objects.exclude(
            last_sent_at=None).exists())
        self.send()
        self.assertEqual(ReportRecipient.objects.exclude(
            last_sent_at=None).count(), 2)

    def test_one_broken_recipient_stops_the_whole_run(self):
        """Half a run going out is the worst outcome available: nobody can
        say afterwards who holds the morning's numbers."""
        ReportRecipient.objects.create(role='head', head_key='GTR99',
                                       name='Nobody', email='n@apisindia.com')
        r = self.send()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(DJMAIL.outbox), 0)
        self.assertEqual(r.json()['failed'][0]['email'], 'n@apisindia.com')

    def test_the_rest_can_be_sent_once_that_is_a_decision(self):
        ReportRecipient.objects.create(role='head', head_key='GTR99',
                                       name='Nobody', email='n@apisindia.com')
        d = self.send(skip_broken=True).json()
        self.assertEqual(d['sent'], 2)
        self.assertEqual(len(DJMAIL.outbox), 2)

    def test_a_manager_gets_the_roll_up_and_each_head_s_own_file(self):
        ReportRecipient.objects.create(role='manager', name='North',
                                       email='mgr@apisindia.com',
                                       regions=['GTR01', 'GTR04 B'])
        self.send(recipients=[ReportRecipient.objects.get(
            email='mgr@apisindia.com').id])
        m = DJMAIL.outbox[0]
        self.assertEqual(len(m.attachments), 3)        # the group, plus two
        self.assertIn('GTR01', m.alternatives[0][0])
        self.assertIn('GTR04 B', m.alternatives[0][0])

    def test_a_manager_covering_nothing_is_refused_not_sent_an_empty_report(self):
        ReportRecipient.objects.create(role='manager', name='Empty',
                                       email='e@apisindia.com', regions=[])
        r = self.send(recipients=[ReportRecipient.objects.get(
            email='e@apisindia.com').id])
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(DJMAIL.outbox), 0)

    def test_an_inactive_recipient_is_not_sent_to(self):
        ReportRecipient.objects.filter(head_key='GTR01').update(is_active=False)
        self.send()
        self.assertEqual([m.to[0] for m in DJMAIL.outbox],
                         ['gulshan@apisindia.com'])

    def test_a_reader_cannot_fire_the_send(self):
        """Granting somebody the dashboard is not granting them the ability to
        mail the sales leadership."""
        from .views.auth import issue_session
        c = Client()
        c.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session('reader@apisindia.com')
        r = c.post('/api/sales/mail/send/', {},
                   content_type='application/json')
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(len(DJMAIL.outbox), 0)


class WithNoSendingAccountConfigured(TestCase):

    def setUp(self):
        upload(review_workbook([GTR01]))
        ReportRecipient.objects.create(role='head', head_key='GTR01',
                                       name='Mohinder Sharma',
                                       email='mohinder@apisindia.com')

    @override_settings(DEFAULT_FROM_EMAIL='', EMAIL_HOST_PASSWORD='')
    def test_it_says_so_rather_than_failing_against_smtp(self):
        r = self.client.post('/api/sales/mail/send/', {},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('.env', r.json()['error'])
        self.assertFalse(self.client.get(
            '/api/sales/mail/outbox/').json()['configured'])


class AnIndividualReportIsOnlyTheirOwn(TestCase):
    """The report goes to one person.

    A board listing eleven colleagues by name and figure hands every reader
    their peers' numbers. That is somebody else's information, and circulating
    it to sixteen inboxes every morning is not a thing to do by accident.
    """

    def setUp(self):
        upload(review_workbook([GTR01, GTR04A, GTR04B, HANDOVER]))

    def report_for(self, region):
        rs = rows()
        me = next(r for r in rs if r.region == region)
        r = self.client.get('/api/sales/review/report/file/?row=%d' % me.id)
        self.assertEqual(r.status_code, 200)
        return me, rs, r.content.decode('utf-8')

    def test_no_other_head_is_named_anywhere_in_it(self):
        me, rs, html = self.report_for('GTR01')
        for other in rs:
            if other.id == me.id:
                continue
            self.assertNotIn(other.head_name, html,
                             '%s is named in %s report'
                             % (other.head_name, me.head_name))
            # The surname on its own too: "Kumar" in a caption is still a
            # colleague's figure attached to a colleague.
            last = other.head_name.split()[-1].strip('()')
            if len(last) > 3 and last.lower() not in me.head_name.lower():
                self.assertNotIn(last, html)

    def test_it_still_says_where_they_stand(self):
        """Taking the names out must not take the standing out -- that is
        the reader's own position and the point of the section."""
        _, _, html = self.report_for('GTR01')
        flat = ' '.join(html.split())           # the markup wraps its lines
        self.assertIn('Where you stand', flat)
        self.assertIn('of 4 on the month', flat)
        self.assertIn('middle of GT', flat)

    def test_the_spread_is_still_drawn(self):
        """One mark per territory, so the shape of the channel is visible
        without anybody being named."""
        _, rs, html = self.report_for('GTR01')
        self.assertEqual(html.count('fill="var(--peer)"'), len(rs) - 1)

    def test_the_manager_report_does_name_them(self):
        """There the team IS the subject, and a roll-up that will not say
        which territory is behind is useless to the person who has to act."""
        regions = ','.join(sorted({r.region for r in rows()}))
        r = self.client.get('/api/sales/review/team/file/?regions=' + regions)
        self.assertEqual(r.status_code, 200)
        html = r.content.decode('utf-8')
        for row in rows():
            self.assertIn(row.head_name, html)


class TheDateTheSheetDescribes(TestCase):
    """It is not written inside the file -- only the month is, in the column
    headers. A sheet closing on the 30th of September uploaded on the 9th of
    October was stamped the 9th of October, and that date went out in the
    subject line of every mail built from it."""

    def test_it_is_read_off_the_name_the_business_writes_on_it(self):
        upload(review_workbook([GTR01]), name="Primary Master till 30th Sept'2026.xlsx")
        self.assertEqual(ReviewSnapshot.objects.first().as_of_date,
                         __import__('datetime').date(2026, 9, 30))

    def test_the_forms_the_business_actually_uses(self):
        from .review import date_from_name as f
        from datetime import date
        self.assertEqual(f("Primary Master till 30th Sept'2026.xlsx"), date(2026, 9, 30))
        self.assertEqual(f('Primary Master till 30.09.2026.xlsx'), date(2026, 9, 30))
        self.assertEqual(f('Sales 30-Sep-26.xlsx'), date(2026, 9, 30))
        self.assertEqual(f('Primary Master till 5 April 2026.xlsx'), date(2026, 4, 5))
        # ISO, which the day-first pattern would read as the 26th of 2030.
        self.assertEqual(f('report_2026-09-30.xlsx'), date(2026, 9, 30))

    def test_something_that_is_not_a_date_is_not_guessed_at(self):
        """Falling back to the upload day is better than inventing a day."""
        from .review import date_from_name as f
        self.assertIsNone(f('Primary Master till 31st Feb 2026.xlsx'))
        self.assertIsNone(f('Region Summary v2.xlsx'))
        self.assertIsNone(f(''))

    def test_an_explicit_as_of_still_wins(self):
        upload(review_workbook([GTR01]),
               name="Primary Master till 30th Sept'2026.xlsx", as_of='2026-10-01')
        self.assertEqual(ReviewSnapshot.objects.first().as_of_date,
                         __import__('datetime').date(2026, 10, 1))

    def test_the_mail_carries_that_date_and_not_the_upload_day(self):
        upload(review_workbook([GTR01]), name="Primary Master till 30th Sept'2026.xlsx")
        ReportRecipient.objects.create(role='head', head_key='GTR01',
                                       name='Mohinder Sharma',
                                       email='m@apisindia.com')
        d = self.client.get('/api/sales/mail/outbox/').json()
        self.assertIn('30.09.26', d['recipients'][0]['subject'])


@override_settings(**MAIL_ON)
class TheMailBody(TestCase):

    def setUp(self):
        upload(review_workbook([GTR01]), name="Primary Master till 30th Sept'2026.xlsx")
        ReportRecipient.objects.create(role='head', head_key='GTR01',
                                       name='Mohinder Sharma',
                                       email='m@apisindia.com')
        self.client.post('/api/sales/mail/send/', {},
                         content_type='application/json')
        self.html = DJMAIL.outbox[0].alternatives[0][0]

    def test_it_reads_like_the_mail_the_business_already_sends(self):
        self.assertIn('Subzone-wise and ASM/TSM-wise B2C Primary Sales Report',
                      self.html)
        self.assertIn("SEPT&#8217;26", self.html)
        self.assertIn('(as of 30.09.26)', self.html)

    def test_the_ot_caveat_is_highlighted_the_way_it_is_circulated(self):
        """Yellow is how somebody scanning on a phone finds it. Reformatting
        it into our own house style makes the mail harder to read for
        everybody who already reads it."""
        self.assertIn('background:#FFFF00', self.html)
        self.assertIn('In OT channel Primary = Secondary.', self.html)

    def test_the_cn_note_is_red(self):
        self.assertIn('color:#FF0000', self.html)
        self.assertIn('Sales Return-Damage Expiry &amp; Good SR', self.html)

    def test_the_attachment_is_not_sent_as_its_own_source_code(self):
        """An .html attachment shows in Gmail as a wall of CSS, and only a
        download gets the reader the report."""
        from . import pdf as PDF
        name, _, mime = (DJMAIL.outbox[0].attachments[0][0],
                         None, DJMAIL.outbox[0].attachments[0][2])
        if PDF.available():
            self.assertTrue(name.endswith('.pdf'), name)
            self.assertEqual(mime, 'application/pdf')
        else:
            # Honest about the fallback rather than silently shipping the
            # thing this test exists to prevent.
            self.assertTrue(name.endswith('.html'), name)
            self.assertEqual(
                self.client.get('/api/sales/mail/outbox/').json()
                ['attachment_format'], 'html')
