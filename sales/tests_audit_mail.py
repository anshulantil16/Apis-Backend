"""The morning send, audited against the sheet the business actually
circulated on 30.09.26.

The failure this exists for is not a crash. It is sixteen mails that arrive,
correctly formatted and plausible, one of which carries somebody else's
numbers under the reader's own name. Nobody reports that as a bug; they act
on it.

So every assertion here is about isolation and arithmetic, not about whether
the code runs.
"""
from django.core import mail as DJMAIL
from django.test import Client as RawClient, TestCase as _TC, override_settings

from .models import ReportRecipient, ReviewSnapshot
from .tests_audit import SHEET, _workbook
from .views.auth import SALESIQ_SUPER_ADMIN, issue_session


MAIL_ON = dict(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
               DEFAULT_FROM_EMAIL='reports@apisindia.com',
               EMAIL_HOST_PASSWORD='not-a-real-password')

# Every head on the sheet, each with an address of their own, which is the
# arrangement the business is about to run.
HEADS = [r for r in SHEET if r[2] and r[0] != 'GT Total']


def owner():
    c = RawClient()
    c.defaults['HTTP_X_SALESIQ_SESSION'] = issue_session(SALESIQ_SUPER_ADMIN)
    return c


@override_settings(**MAIL_ON)
class TheWholeRunOfSixteen(_TC):

    def setUp(self):
        self.c = owner()
        self.c.post('/api/sales/upload/', {'file': _workbook(SHEET)})
        self.snap = ReviewSnapshot.objects.first()
        self.rows = [r for r in self.snap.rows.all() if not r.is_total]
        from .views.recipients import unique_key
        self.by_email = {}
        for r in self.rows:
            addr = 'h%d@apisindia.com' % r.id
            ReportRecipient.objects.create(
                role='head', head_key=unique_key(r, self.rows),
                name=r.head_name, email=addr)
            self.by_email[addr] = r

    def send(self, **body):
        return self.c.post('/api/sales/mail/send/', body,
                           content_type='application/json')

    # -- isolation ---------------------------------------------------------
    def test_everybody_on_the_sheet_is_sent_to_exactly_once(self):
        d = self.send().json()
        self.assertEqual(d['sent'], len(self.rows), d)
        self.assertEqual(len(DJMAIL.outbox), len(self.rows))
        self.assertEqual(sorted(m.to[0] for m in DJMAIL.outbox),
                         sorted(self.by_email))

    def test_no_mail_carries_another_head_s_name(self):
        """The one failure that is acted on rather than reported."""
        self.send()
        for m in DJMAIL.outbox:
            mine = self.by_email[m.to[0]]
            body = m.alternatives[0][0] + m.body + ''.join(
                a[1] if isinstance(a[1], str) else '' for a in m.attachments)
            for other in self.rows:
                # Skipped where one name contains the other: the handover
                # line is literally called "Arun Mishra ( Arnab Ghosh)", so
                # finding "Arnab Ghosh" inside it proves nothing.
                if other.id == mine.id or other.head_name in mine.head_name:
                    continue
                self.assertNotIn(other.head_name, body,
                                 f'{other.head_name} appears in the mail to '
                                 f'{mine.head_name}')

    def test_each_mail_carries_its_own_head_s_figures(self):
        from .report import lakh
        self.send()
        for m in DJMAIL.outbox:
            mine = self.by_email[m.to[0]]
            html = m.alternatives[0][0]
            # lakh() takes rupees and converts; dividing first converts twice.
            if mine.month_target:
                self.assertIn(lakh(mine.month_target), html)
            self.assertIn(lakh(mine.mtd_primary), html)

    def test_the_attachment_belongs_to_the_addressee(self):
        """Right subject with the wrong file attached is the same mistake
        wearing a different hat."""
        from .report import _safe
        self.send()
        for m in DJMAIL.outbox:
            mine = self.by_email[m.to[0]]
            self.assertEqual(len(m.attachments), 1)
            name = m.attachments[0][0]
            self.assertIn(_safe(mine.head_name), name)

    def test_the_subject_names_the_addressee_s_own_territory(self):
        self.send()
        for m in DJMAIL.outbox:
            mine = self.by_email[m.to[0]]
            self.assertIn(mine.region or mine.head_name, m.subject)

    # -- the handover line -------------------------------------------------
    def test_the_handover_line_gets_its_own_mail_not_arnabs(self):
        """Two rows share GTR04 A. The second of them used to resolve to the
        first, so one man was sent the other's month under his own name."""
        self.send()
        pair = [r for r in self.rows
                if 'ARNAB' in r.head_name.upper() or 'ARUN MISHRA (' in
                r.head_name.upper()]
        self.assertGreaterEqual(len(pair), 2)
        sent = {m.to[0]: m for m in DJMAIL.outbox}
        seen = set()
        for r in pair:
            addr = next(a for a, row in self.by_email.items() if row.id == r.id)
            body = sent[addr].alternatives[0][0]
            self.assertNotIn(body, seen, 'two people got the identical mail')
            seen.add(body)

    # -- the run as a whole ------------------------------------------------
    def test_a_test_run_touches_nobody_else_and_marks_nothing(self):
        self.send(test_to='anshul@apisindia.com')
        self.assertTrue(all(m.to == ['anshul@apisindia.com']
                            for m in DJMAIL.outbox))
        self.assertFalse(ReportRecipient.objects.exclude(
            last_sent_at=None).exists())

    def test_one_unresolvable_recipient_stops_the_entire_run(self):
        """Half a run delivered is the worst outcome available: afterwards
        nobody can say who is holding today's numbers."""
        ReportRecipient.objects.create(role='head', head_key='GTR99',
                                       name='Nobody', email='x@apisindia.com')
        r = self.send()
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(DJMAIL.outbox), 0)

    def test_sending_twice_does_not_double_anybody_s_mail(self):
        self.send()
        first = len(DJMAIL.outbox)
        self.send()
        self.assertEqual(len(DJMAIL.outbox), first * 2)
        # ...and each run is one mail per person, not two.
        second = DJMAIL.outbox[first:]
        self.assertEqual(len(set(m.to[0] for m in second)), len(second))

    def test_an_inactive_recipient_is_left_out(self):
        ReportRecipient.objects.filter(
            email=list(self.by_email)[0]).update(is_active=False)
        self.send()
        self.assertEqual(len(DJMAIL.outbox), len(self.rows) - 1)

    # -- the manager -------------------------------------------------------
    def test_a_manager_covering_everything_gets_one_file_and_every_row(self):
        ReportRecipient.objects.all().delete()
        regions = [r.region for r in self.rows if r.region]
        ReportRecipient.objects.create(role='manager', name='Group Head',
                                       email='mgr@apisindia.com',
                                       regions=list(dict.fromkeys(regions)))
        self.send()
        self.assertEqual(len(DJMAIL.outbox), 1)
        m = DJMAIL.outbox[0]
        self.assertEqual(len(m.attachments), 1)
        html = m.alternatives[0][0]
        for r in self.rows:
            if r.region:
                self.assertIn(r.region, html)

    def test_the_group_line_adds_the_rows_it_covers_not_the_sheet_s_total(self):
        """A manager covering part of a channel has no subtotal on the sheet;
        taking the one that is there hands them the whole channel."""
        from .report import lakh
        ReportRecipient.objects.all().delete()
        two = [r for r in self.rows if r.region in ('GTR01', 'GTR04 B')]
        ReportRecipient.objects.create(role='manager', name='Pair',
                                       email='p@apisindia.com',
                                       regions=['GTR01', 'GTR04 B'])
        self.send()
        html = DJMAIL.outbox[0].alternatives[0][0]
        want = sum(float(r.mtd_primary) for r in two)
        self.assertIn(lakh(want), html)
        # The sheet's own GT total must not appear.
        self.assertNotIn(lakh(1378.51 * 100000), html)


@override_settings(**MAIL_ON)
class AddressesAsPeopleActuallyTypeThem(_TC):

    def setUp(self):
        self.c = owner()
        self.c.post('/api/sales/upload/', {'file': _workbook(SHEET)})

    def add(self, email, key='GTR01'):
        return self.c.post('/api/sales/recipients/edit/',
                           {'role': 'head', 'head_key': key, 'name': 'X',
                            'email': email},
                           content_type='application/json')

    def test_the_same_address_in_two_cases_is_one_person(self):
        """Otherwise they are sent the same report twice every morning."""
        self.add('Mohinder@APISINDIA.com')
        self.add('mohinder@apisindia.com')
        self.assertEqual(ReportRecipient.objects.filter(
            head_key='GTR01').count(), 1)

    def test_an_address_with_spaces_round_it_is_accepted(self):
        r = self.add('  spaced@apisindia.com  ')
        self.assertEqual(r.status_code, 201)
        self.assertTrue(ReportRecipient.objects.filter(
            email='spaced@apisindia.com').exists())

    def test_something_that_is_not_an_address_is_refused(self):
        for bad in ('', 'not-an-address', 'a@', '@b.com', 'a b@c.com'):
            r = self.add(bad)
            self.assertEqual(r.status_code, 400, bad)

    def test_one_person_may_hold_two_territories(self):
        """Which happens, and must not be collapsed into one row."""
        self.add('both@apisindia.com', 'GTR01')
        self.add('both@apisindia.com', 'GTR02')
        self.assertEqual(ReportRecipient.objects.filter(
            email='both@apisindia.com').count(), 2)
