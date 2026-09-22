"""AdminPulse — the helpdesk, and the sign-in that guards it.

Written after an audit found that the OTP login bought nothing: every
privileged endpoint took the caller's word for who they were, read from an
`email` field in the request itself. The tests that matter most here are the
ones that try to act as somebody else.
"""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.utils import override_settings as _os  # noqa: F401

from .models import AdminUser, SupportTicket, TicketEvent, Employee, Room
from .views.auth import issue_session, SUPER_ADMIN_EMAIL

API = '/api/roompulse'

EMPLOYEE = 'priya.sharma@apisindia.com'
IT_STAFF = 'it.desk@apisindia.com'
OUTSIDER = 'attacker@gmail.com'


def auth(email):
    """Headers for a signed-in caller."""
    return {'HTTP_X_ADMINPULSE_SESSION': issue_session(email)}


class HelpdeskBase(TestCase):
    def setUp(self):
        AdminUser.objects.create(email=IT_STAFF, name='IT Desk', scope='it_support')

    def raise_ticket(self, email=EMPLOYEE, **over):
        body = {'subject': 'Laptop will not boot', 'description': 'Blue screen on startup',
                'category': 'other', 'priority': 'high'}
        body.update(over)
        return self.client.post(f'{API}/tickets/', body, **auth(email))


# ── the hole this whole change exists to close ───────────────────────────
class YouCannotSimplyClaimToBeSomebodyElse(HelpdeskBase):

    def test_an_unsigned_request_cannot_read_the_ticket_queue(self):
        """The list carried every description and a working link to every
        attachment, to anyone who knew the URL."""
        self.raise_ticket()
        r = self.client.get(f'{API}/tickets/')
        self.assertEqual(r.status_code, 401, r.content[:200])

    def test_naming_the_super_admin_in_the_body_grants_nothing(self):
        """This is exactly what used to work: no code, no email, no account —
        just their address, which is a constant in auth.py."""
        t = self.raise_ticket().json()
        r = self.client.patch(f'{API}/tickets/{t["id"]}/',
                              {'action': 'approve', 'email': SUPER_ADMIN_EMAIL},
                              content_type='application/json')
        self.assertIn(r.status_code, (401, 403), r.content[:200])
        self.assertEqual(SupportTicket.objects.get(id=t['id']).status, 'pending')

    def test_an_employee_cannot_approve_by_naming_it_support(self):
        t = self.raise_ticket().json()
        r = self.client.patch(f'{API}/tickets/{t["id"]}/',
                              {'action': 'approve', 'email': IT_STAFF},
                              content_type='application/json', **auth(EMPLOYEE))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(SupportTicket.objects.get(id=t['id']).status, 'pending')

    def test_the_database_reset_cannot_be_fired_by_a_stranger(self):
        """It deletes every ticket, booking and employee. It used to need
        nothing but the super admin's address in the body."""
        self.raise_ticket()
        r = self.client.post(f'{API}/reset/',
                             {'email': SUPER_ADMIN_EMAIL, 'confirm': 'RESET'},
                             content_type='application/json')
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(SupportTicket.objects.count(), 1, 'the tickets were wiped')

    def test_a_made_up_token_is_not_a_session(self):
        self.raise_ticket()
        r = self.client.get(f'{API}/tickets/', HTTP_X_ADMINPULSE_SESSION='not-a-real-token')
        self.assertEqual(r.status_code, 401)

    def test_an_outsider_cannot_sign_in_at_all(self):
        r = self.client.post(f'{API}/login/', {'action': 'send_otp', 'email': OUTSIDER},
                             content_type='application/json')
        self.assertEqual(r.status_code, 403)


class SigningIn(HelpdeskBase):

    @override_settings(PORTAL_DEV_LOGIN=True)
    def test_the_code_exchanges_for_a_working_session(self):
        r = self.client.post(f'{API}/login/', {'action': 'send_otp', 'email': EMPLOYEE},
                             content_type='application/json')
        code = r.json()['dev_otp']
        d = self.client.post(f'{API}/login/',
                             {'action': 'verify_otp', 'email': EMPLOYEE, 'otp': code},
                             content_type='application/json').json()
        self.assertTrue(d['token'], 'verifying the code must hand back a session')
        got = self.client.get(f'{API}/tickets/', HTTP_X_ADMINPULSE_SESSION=d['token'])
        self.assertEqual(got.status_code, 200)

    @override_settings(PORTAL_DEV_LOGIN=True)
    def test_a_wrong_code_hands_back_nothing(self):
        self.client.post(f'{API}/login/', {'action': 'send_otp', 'email': EMPLOYEE},
                         content_type='application/json')
        r = self.client.post(f'{API}/login/',
                             {'action': 'verify_otp', 'email': EMPLOYEE, 'otp': '000000'},
                             content_type='application/json')
        self.assertEqual(r.status_code, 400)
        self.assertNotIn('token', r.json())

    def test_signing_out_kills_the_session(self):
        token = issue_session(EMPLOYEE)
        self.client.post(f'{API}/login/', {'action': 'logout'},
                         content_type='application/json',
                         HTTP_X_ADMINPULSE_SESSION=token)
        r = self.client.get(f'{API}/tickets/', HTTP_X_ADMINPULSE_SESSION=token)
        self.assertEqual(r.status_code, 401)


class WhoSeesWhichTickets(HelpdeskBase):

    def test_an_employee_sees_only_their_own(self):
        self.raise_ticket(email=EMPLOYEE, subject='Mine')
        self.raise_ticket(email='someone.else@apisindia.com', subject='Theirs')
        rows = self.client.get(f'{API}/tickets/', **auth(EMPLOYEE)).json()['results']
        self.assertEqual([t['subject'] for t in rows], ['Mine'])

    def test_it_support_sees_everything(self):
        self.raise_ticket(email=EMPLOYEE, subject='Mine')
        self.raise_ticket(email='someone.else@apisindia.com', subject='Theirs')
        rows = self.client.get(f'{API}/tickets/', **auth(IT_STAFF)).json()['results']
        self.assertEqual(len(rows), 2)

    def test_an_employee_cannot_read_a_colleague_by_asking_for_them(self):
        self.raise_ticket(email='someone.else@apisindia.com', subject='Theirs')
        rows = self.client.get(f'{API}/tickets/?mine=someone.else@apisindia.com',
                               **auth(EMPLOYEE)).json()['results']
        self.assertEqual(rows, [])


class RaisingATicket(HelpdeskBase):

    def test_it_is_filed_under_the_person_who_is_signed_in(self):
        """Not under whatever address the body claims."""
        r = self.raise_ticket(email=EMPLOYEE, requested_by_email=IT_STAFF)
        t = SupportTicket.objects.get(id=r.json()['id'])
        self.assertEqual(t.requested_by_email, EMPLOYEE)

    def test_an_employees_ticket_waits_for_triage(self):
        self.assertEqual(self.raise_ticket().json()['status'], 'pending')

    def test_it_support_raising_one_is_already_approved(self):
        self.assertEqual(self.raise_ticket(email=IT_STAFF).json()['status'], 'approved')

    def test_a_subject_and_a_description_are_required(self):
        self.assertEqual(self.raise_ticket(subject='').status_code, 400)
        self.assertEqual(self.raise_ticket(description='').status_code, 400)


class TheTrailThatMakesItProof(HelpdeskBase):

    def test_raising_a_ticket_records_that_it_was_raised(self):
        t = self.raise_ticket().json()
        ev = TicketEvent.objects.filter(ticket_id=t['id'])
        self.assertEqual([e.action for e in ev], ['created'])
        self.assertEqual(ev[0].actor_email, EMPLOYEE)

    def test_every_step_is_kept_not_overwritten(self):
        """The ticket row has one reviewed_by slot, so closing a ticket used
        to erase who had approved it."""
        t = self.raise_ticket().json()
        for action in ('approve', 'start', 'close'):
            r = self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': action},
                                  content_type='application/json', **auth(IT_STAFF))
            self.assertEqual(r.status_code, 200, r.content[:200])
        trail = [e.action for e in TicketEvent.objects.filter(ticket_id=t['id'])]
        self.assertEqual(trail, ['created', 'approved', 'started', 'closed'])

    def test_the_trail_says_who_and_when(self):
        t = self.raise_ticket().json()
        self.client.patch(f'{API}/tickets/{t["id"]}/',
                          {'action': 'approve', 'remarks': 'Spare laptop issued'},
                          content_type='application/json', **auth(IT_STAFF))
        e = TicketEvent.objects.filter(ticket_id=t['id'], action='approved').first()
        self.assertEqual(e.actor_email, IT_STAFF)
        self.assertEqual(e.actor_role, 'it_support')
        self.assertEqual(e.remarks, 'Spare laptop issued')
        self.assertEqual((e.from_status, e.to_status), ('pending', 'approved'))
        self.assertIsNotNone(e.created_at)

    def test_the_history_is_served_with_the_ticket(self):
        t = self.raise_ticket().json()
        self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': 'approve'},
                          content_type='application/json', **auth(IT_STAFF))
        row = self.client.get(f'{API}/tickets/', **auth(IT_STAFF)).json()['results'][0]
        self.assertEqual([h['action'] for h in row['history']], ['created', 'approved'])


class WithdrawnIsNotRejected(HelpdeskBase):

    def test_a_requester_cancelling_is_recorded_as_cancelled(self):
        """Both used to be written as 'rejected', so the data could not tell
        "IT turned this down" from "the user no longer needed it"."""
        t = self.raise_ticket().json()
        r = self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': 'cancel'},
                              content_type='application/json', **auth(EMPLOYEE))
        self.assertEqual(r.status_code, 200, r.content[:200])
        self.assertEqual(SupportTicket.objects.get(id=t['id']).status, 'cancelled')

    def test_it_support_rejecting_is_still_rejected(self):
        t = self.raise_ticket().json()
        self.client.patch(f'{API}/tickets/{t["id"]}/',
                          {'action': 'reject', 'remarks': 'Out of scope'},
                          content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(SupportTicket.objects.get(id=t['id']).status, 'rejected')

    def test_you_cannot_cancel_a_colleagues_ticket(self):
        t = self.raise_ticket(email='someone.else@apisindia.com').json()
        r = self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': 'cancel'},
                              content_type='application/json', **auth(EMPLOYEE))
        self.assertEqual(r.status_code, 403)


class TheWorkflowOnlyGoesOneWay(HelpdeskBase):

    def test_work_cannot_start_before_approval(self):
        t = self.raise_ticket().json()
        r = self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': 'start'},
                              content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 400)

    def test_a_ticket_cannot_be_closed_before_it_is_started(self):
        t = self.raise_ticket().json()
        self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': 'approve'},
                          content_type='application/json', **auth(IT_STAFF))
        r = self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': 'close'},
                              content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 400)

    def test_a_decided_ticket_cannot_be_decided_again(self):
        t = self.raise_ticket().json()
        for _ in range(2):
            r = self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': 'approve'},
                                  content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(TicketEvent.objects.filter(ticket_id=t['id'],
                                                    action='approved').count(), 1)

    def test_an_unknown_action_is_refused(self):
        t = self.raise_ticket().json()
        r = self.client.patch(f'{API}/tickets/{t["id"]}/', {'action': 'delete_everything'},
                              content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 400)

    def test_a_missing_ticket_is_a_404_not_a_crash(self):
        r = self.client.patch(f'{API}/tickets/999999/', {'action': 'approve'},
                              content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 404)


class WhatMayBeAttached(HelpdeskBase):

    def _file(self, name, content=b'x', size=None):
        return SimpleUploadedFile(name, content * (size or 1))

    def test_a_screenshot_is_kept_and_linked(self):
        r = self.client.post(f'{API}/tickets/', {
            'subject': 'Printer jam', 'description': 'See photo',
            'attachments': self._file('error.png', b'PNG'),
        }, **auth(EMPLOYEE))
        self.assertEqual(r.status_code, 201, r.content[:300])
        att = r.json()['ticket']['attachments']
        self.assertEqual(len(att), 1)
        self.assertEqual(att[0]['name'], 'error.png')
        self.assertTrue(att[0]['url'])

    def test_a_script_cannot_be_attached(self):
        """Attachments are served back from MEDIA_URL and opened in a browser
        by IT Support — an .html or .svg is a script on our own origin."""
        for bad in ('payload.html', 'logo.svg', 'setup.exe', 'run.sh'):
            r = self.client.post(f'{API}/tickets/', {
                'subject': 'x', 'description': 'y', 'attachments': self._file(bad),
            }, **auth(EMPLOYEE))
            self.assertEqual(r.status_code, 400, f'{bad} was accepted')

    def test_an_oversized_file_is_refused(self):
        big = SimpleUploadedFile('huge.png', b'x' * (5 * 1024 * 1024 + 10))
        r = self.client.post(f'{API}/tickets/', {
            'subject': 'x', 'description': 'y', 'attachments': big,
        }, **auth(EMPLOYEE))
        self.assertEqual(r.status_code, 400)
        self.assertIn('5MB', r.json()['error'])

    def test_nothing_is_stored_when_a_file_is_refused(self):
        self.client.post(f'{API}/tickets/', {
            'subject': 'x', 'description': 'y', 'attachments': self._file('bad.exe'),
        }, **auth(EMPLOYEE))
        self.assertEqual(SupportTicket.objects.count(), 0,
                         'a refused upload left a ticket behind')


class TheQueueStaysCheapToLoad(HelpdeskBase):

    def test_listing_does_not_query_once_per_ticket(self):
        """_brief() reads each ticket's attachments and history, so without
        prefetching, a queue of 12 ran 24 extra queries — and the page is
        allowed to hold 200."""
        for i in range(12):
            self.raise_ticket(subject=f'T{i}')
        headers = auth(IT_STAFF)          # minting the token is not the request

        def count_queries(n_tickets):
            with self.assertNumQueries(6):
                r = self.client.get(f'{API}/tickets/', **headers)
            return r

        # 6 = session lookup, roster lookup, tickets, attachments, events, count.
        count_queries(12)

        # The point of the test: twice the tickets, same number of queries.
        for i in range(12):
            self.raise_ticket(subject=f'U{i}')
        count_queries(24)
