"""The emails Help Desk sends.

Two ways these can be wrong, and both are worse than sending nothing:

**Mailing the wrong person.** A ticket carries what broke, who reported it
and often a screenshot of a real system. Every test below that checks a
recipient is checking a disclosure, not a nicety -- and the one rule holding
it together is that the mail goes to the side that did NOT take the step.

**Letting the mail break the work.** Approving a ticket and telling somebody
about it are separate jobs. If SMTP is down, the ticket must still approve.
"""
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings

from .models import AdminUser, BookingRequest, ResourceRequest, Room
from . import notify
from .tests import API, EMPLOYEE, IT_STAFF, ADMIN_STAFF, OTHER_IT, auth


def to(m):
    return m.to[0]


def addressees():
    return sorted(to(m) for m in mail.outbox)


def body_for(address):
    """The one mail sent to this person."""
    hits = [m for m in mail.outbox if to(m) == address]
    assert len(hits) == 1, f'{len(hits)} mails to {address}, expected 1'
    return hits[0]


class Base(TestCase):
    def setUp(self):
        AdminUser.objects.create(email=IT_STAFF, name='IT Desk', scope='it_support')
        AdminUser.objects.create(email=OTHER_IT, name='Sana', scope='it_support')
        AdminUser.objects.create(email=ADMIN_STAFF, name='Meena', scope='admin')
        mail.outbox = []

    def raise_ticket(self, email=EMPLOYEE, **over):
        body = {'subject': 'Laptop will not boot', 'description': 'Blue screen on startup',
                'category': 'other', 'priority': 'high', 'assigned_to_email': IT_STAFF}
        body.update(over)
        r = self.client.post(f'{API}/tickets/', body, **auth(email))
        assert r.status_code == 201, r.content[:300]
        return r.json()

    def act(self, ticket_id, email=IT_STAFF, **body):
        return self.client.patch(f'{API}/tickets/{ticket_id}/', body,
                                 content_type='application/json', **auth(email))


# ── raising one ──────────────────────────────────────────────────────────

class WhenATicketIsRaised(Base):

    def test_both_sides_are_told(self):
        """The desk gets work it does not know about yet; the person who
        raised it gets the only confirmation they will ever have that it
        arrived at all."""
        self.raise_ticket()
        self.assertEqual(addressees(), sorted([EMPLOYEE, IT_STAFF]))

    def test_the_desk_is_told_what_it_is_without_opening_anything(self):
        self.raise_ticket()
        m = body_for(IT_STAFF)
        self.assertIn('assigned to you', m.body)
        self.assertIn('Laptop will not boot', m.body)
        self.assertIn('High', m.body)
        self.assertIn('Blue screen on startup', m.body)

    def test_the_requester_is_told_whose_desk_it_is_on(self):
        """Without this the acknowledgement says nothing they did not
        already know, and they ring the desk anyway."""
        self.raise_ticket()
        self.assertIn('IT Desk', body_for(EMPLOYEE).body)

    def test_the_subject_names_the_ticket_and_the_problem(self):
        t = self.raise_ticket()
        self.assertIn(f'#{t["id"]}', body_for(IT_STAFF).subject)
        self.assertIn('Laptop will not boot', body_for(IT_STAFF).subject)

    def test_a_long_description_is_cut_rather_than_pasted_whole(self):
        self.raise_ticket(description='x' * 2000)
        self.assertLess(len(body_for(IT_STAFF).body), 1200)

    def test_it_raising_its_own_ticket_is_not_mailed_twice(self):
        """Assignee and requester are the same person. One mail, and it does
        not ask them to review something they just approved."""
        self.raise_ticket(email=IT_STAFF)
        self.assertEqual(addressees(), [IT_STAFF])
        self.assertIn('needs no review', body_for(IT_STAFF).body)


class WorkLoggedAfterTheFact(Base):

    def test_nobody_is_mailed(self):
        """A job written up afterwards has nobody waiting on it. Mailing the
        person who logged it about their own completed work is noise, and it
        is the only notification that would arrive every single day."""
        r = self.client.post(f'{API}/tickets/', {
            'origin': 'logged', 'subject': 'Reset a password',
            'description': 'Done at the desk', 'category': 'other',
            'logged_for': 'Priya', 'worked_from': '10:00', 'worked_to': '10:20',
        }, **auth(IT_STAFF))
        self.assertEqual(r.status_code, 201, r.content[:300])
        self.assertEqual(mail.outbox, [])


# ── every step after that ────────────────────────────────────────────────

class EachStepTellsTheOtherSide(Base):

    def setUp(self):
        super().setUp()
        self.t = self.raise_ticket()['id']
        mail.outbox = []

    def test_approving_tells_the_requester_and_not_the_approver(self):
        self.act(self.t, action='approve')
        self.assertEqual(addressees(), [EMPLOYEE])
        self.assertIn('approved', body_for(EMPLOYEE).body)

    def test_rejecting_carries_the_reason(self):
        """A refusal with no reason is the one that comes straight back as a
        second ticket."""
        self.act(self.t, action='reject', remarks='Use the spare laptop in store.')
        m = body_for(EMPLOYEE)
        self.assertIn('not been approved', m.body)
        self.assertIn('Use the spare laptop in store.', m.body)
        self.assertIn('Reason', m.body)

    def test_starting_work_is_itself_news(self):
        self.act(self.t, action='approve')
        mail.outbox = []
        self.act(self.t, action='start')
        self.assertIn('started work', body_for(EMPLOYEE).body)

    def test_resolving_says_so_and_says_how_long_it_took(self):
        self.act(self.t, action='approve')
        self.act(self.t, action='start')
        mail.outbox = []
        self.act(self.t, action='close', worked_from='10:00', worked_to='11:30',
                 remarks='Replaced the RAM.')
        m = body_for(EMPLOYEE)
        self.assertIn('resolved', m.body)
        self.assertIn('Replaced the RAM.', m.body)
        self.assertIn('1 h 30 min', m.body)

    def test_the_requester_withdrawing_it_tells_the_desk_not_themselves(self):
        """The direction flips here, and getting it wrong means the desk
        keeps working a ticket that was dropped."""
        self.act(self.t, email=EMPLOYEE, action='cancel')
        self.assertEqual(addressees(), [IT_STAFF])
        self.assertIn('withdrawn', body_for(IT_STAFF).body)

    def test_the_desk_cancelling_it_tells_the_requester(self):
        self.act(self.t, action='cancel', remarks='Raised twice.')
        self.assertEqual(addressees(), [EMPLOYEE])
        self.assertIn('cancelled', body_for(EMPLOYEE).body)

    def test_a_refused_action_sends_nothing(self):
        """Closing a ticket that was never started is answered 400. No mail
        may go out for a step that did not happen."""
        r = self.act(self.t, action='close')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(mail.outbox, [])

    def test_the_mail_says_who_did_it(self):
        self.act(self.t, action='approve')
        self.assertIn('Actioned by', body_for(EMPLOYEE).body)


class PassingItOn(Base):

    def test_the_new_holder_and_the_requester_are_both_told(self):
        """One of them has work they do not know about; the other has been
        waiting on a name that is no longer the right one."""
        t = self.raise_ticket()['id']
        mail.outbox = []
        self.act(t, action='transfer', to_email=OTHER_IT,
                 remarks='Hers to look after.')
        self.assertEqual(addressees(), sorted([EMPLOYEE, OTHER_IT]))
        self.assertIn('passed to you', body_for(OTHER_IT).body)
        self.assertIn('Hers to look after.', body_for(OTHER_IT).body)
        self.assertIn('Sana', body_for(EMPLOYEE).body)
        self.assertIn('Nothing is needed from you', body_for(EMPLOYEE).body)


# ── the Admin desk ───────────────────────────────────────────────────────

class ItemRequests(Base):

    def ask(self, email=EMPLOYEE, **over):
        body = {'requested_by_name': 'Priya', 'item_name': 'Whiteboard marker',
                'quantity': 5, 'reason': 'Training room is out',
                'assigned_to_email': ADMIN_STAFF}
        body.update(over)
        r = self.client.post(f'{API}/resource-requests/', body, **auth(email))
        assert r.status_code == 201, r.content[:300]
        return r.json()['id']

    def test_raising_one_tells_both_sides(self):
        self.ask()
        self.assertEqual(addressees(), sorted([EMPLOYEE, ADMIN_STAFF]))
        self.assertIn('Whiteboard marker x 5', body_for(ADMIN_STAFF).body)

    def test_fulfilling_one_tells_the_person_who_asked(self):
        rid = self.ask()
        mail.outbox = []
        self.client.patch(f'{API}/resource-requests/{rid}/', {'action': 'approve'},
                          content_type='application/json', **auth(ADMIN_STAFF))
        mail.outbox = []
        self.client.patch(f'{API}/resource-requests/{rid}/', {'action': 'fulfil'},
                          content_type='application/json', **auth(ADMIN_STAFF))
        self.assertEqual(addressees(), [EMPLOYEE])
        self.assertIn('fulfilled', body_for(EMPLOYEE).body)


class RoomBookings(Base):

    def setUp(self):
        super().setUp()
        self.room = Room.objects.create(name='Board Room', capacity=10)
        mail.outbox = []

    def book(self, email=EMPLOYEE, **over):
        body = {'room_id': self.room.id, 'name': 'Priya', 'date': '2099-01-05',
                'start_time': '10:00', 'end_time': '11:00',
                'purpose': 'internal_meeting', 'attendees': 4,
                'assigned_to_email': ADMIN_STAFF}
        body.update(over)
        r = self.client.post(f'{API}/bookings/', body, **auth(email))
        assert r.status_code == 201, r.content[:300]
        return r.json()['id']

    def test_raising_one_tells_both_sides_with_the_room_and_the_slot(self):
        self.book()
        self.assertEqual(addressees(), sorted([EMPLOYEE, ADMIN_STAFF]))
        m = body_for(ADMIN_STAFF)
        self.assertIn('Board Room', m.body)
        self.assertIn('10:00-11:00', m.body)

    def test_approving_one_confirms_it_to_whoever_booked(self):
        bid = self.book()
        mail.outbox = []
        self.client.patch(f'{API}/bookings/{bid}/', {'action': 'approve'},
                          content_type='application/json', **auth(ADMIN_STAFF))
        self.assertEqual(addressees(), [EMPLOYEE])
        self.assertIn('confirmed', body_for(EMPLOYEE).body)


# ── it must never break the thing it is reporting ────────────────────────

class WhenTheMailServerIsDown(Base):

    def test_a_ticket_still_approves(self):
        """SMTP is allowed to be unavailable; Help Desk is not. If this ever
        fails, an outage at Gmail becomes an outage here."""
        t = self.raise_ticket()['id']
        # assertLogs doubles as the proof it was recorded rather than
        # swallowed, and keeps the traceback out of the test output.
        with self.assertLogs('roompulse.notify', 'ERROR'),                 patch.object(notify, 'EmailMessage', side_effect=OSError('no route')):
            r = self.act(t, action='approve')
        self.assertEqual(r.status_code, 200, r.content[:300])

    def test_a_ticket_is_still_raised(self):
        with self.assertLogs('roompulse.notify', 'ERROR'),                 patch.object(notify, 'EmailMessage', side_effect=OSError('no route')):
            r = self.client.post(f'{API}/tickets/', {
                'subject': 'No network', 'description': 'Port is dead',
                'category': 'other', 'assigned_to_email': IT_STAFF}, **auth(EMPLOYEE))
        self.assertEqual(r.status_code, 201, r.content[:300])

    def test_a_broken_message_does_not_reach_the_caller(self):
        """_safe covers the message-building too, not only the send: a
        missing field in a body template must not 500 a ticket close."""
        t = self.raise_ticket()['id']
        mail.outbox = []
        # _fields and not _ticket_facts: SHAPE captured that one by
        # reference at import, so patching the name changes nothing and the
        # test would quietly pass against a bug it never provoked.
        with self.assertLogs('roompulse.notify', 'ERROR'),                 patch.object(notify, '_fields', side_effect=AttributeError('boom')):
            r = self.act(t, action='approve')
        self.assertEqual(r.status_code, 200, r.content[:300])
        self.assertEqual(mail.outbox, [])


# ── the shape of a mail ──────────────────────────────────────────────────

class HowTheMailReads(Base):

    def test_it_comes_from_the_same_address_as_the_sign_in_code(self):
        """Two senders for one tool is two things to whitelist, and the
        second one is the one that lands in Spam."""
        with override_settings(OFFER_LETTER_EMAIL_HOST_USER='helpdesk@apisindia.com'):
            self.raise_ticket()
        self.assertEqual(body_for(IT_STAFF).from_email, 'helpdesk@apisindia.com')

    def test_no_link_is_offered_when_nobody_has_said_where_the_tool_lives(self):
        """Better no link than one pointing at the wrong server."""
        with override_settings(PORTAL_BASE_URL=''):
            self.raise_ticket()
        self.assertNotIn('Open Help Desk', body_for(IT_STAFF).body)

    def test_the_link_is_included_once_it_is_configured(self):
        with override_settings(PORTAL_BASE_URL='https://intranet.example.com/'):
            self.raise_ticket()
        self.assertIn('https://intranet.example.com/help-desk',
                      body_for(IT_STAFF).body)

    def test_each_person_gets_their_own_copy(self):
        """Not one mail addressed to both: the desk roster is not public and
        a ticket names who reported what."""
        self.raise_ticket()
        for m in mail.outbox:
            self.assertEqual(len(m.to), 1)

    def test_empty_fields_are_left_out_rather_than_printed_blank(self):
        self.raise_ticket()
        self.assertNotIn(': \n', body_for(IT_STAFF).body)


class HowLongItTookReadsAsEnglish(TestCase):

    def test_minutes_hours_and_both(self):
        self.assertEqual(notify.mins_label(45), '45 min')
        self.assertEqual(notify.mins_label(60), '1 h')
        self.assertEqual(notify.mins_label(90), '1 h 30 min')

    def test_nothing_recorded_prints_nothing(self):
        """Not '0 min' -- a job with no time recorded is silent, not
        instantaneous. Same rule as worktime.after_hours_minutes."""
        self.assertEqual(notify.mins_label(0), '')
        self.assertEqual(notify.mins_label(None), '')
