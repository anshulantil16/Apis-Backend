"""AdminPulse — the helpdesk, and the sign-in that guards it.

Written after an audit found that the OTP login bought nothing: every
privileged endpoint took the caller's word for who they were, read from an
`email` field in the request itself. The tests that matter most here are the
ones that try to act as somebody else.
"""
import io
from calendar import monthrange
from datetime import date, datetime, time, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from django.test.utils import override_settings as _os  # noqa: F401

from accounts.models import PortalUser

from .models import (AdminUser, BookingRequest, ResourceRequest, SupportTicket,
                     TicketEvent, Employee, Room)
from .views.auth import issue_session, resolve_role, SUPER_ADMIN_EMAIL
from .worktime import after_hours_minutes, duration_minutes, resolve_time

API = '/api/roompulse'

EMPLOYEE = 'priya.sharma@apisindia.com'
IT_STAFF = 'it.desk@apisindia.com'
OUTSIDER = 'attacker@gmail.com'
OTHER_IT = 'sana.it@apisindia.com'


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


# ── work nobody raised a ticket for ──────────────────────────────────────
class LoggingWorkNobodyAskedFor(HelpdeskBase):
    """A large part of what IT does never becomes a ticket -- a server
    restarted, a laptop rebuilt for a joiner, a printer fixed because someone
    walked over and said so. Counted only what came through the queue, the
    monthly figure measured how often people used the ticket form rather than
    how much work was done.
    """

    def log_work(self, email=IT_STAFF, **over):
        body = {'origin': 'logged', 'subject': 'Restarted the mail server',
                'description': 'Queue was stuck; restarted it and cleared the backlog.',
                'category': 'server_storage', 'time_spent_minutes': 45,
                'logged_for': 'Whole office'}
        body.update(over)
        return self.client.post(f'{API}/tickets/', body,
                                content_type='application/json', **auth(email))

    def test_an_employee_cannot_log_work(self):
        self.assertEqual(self.log_work(EMPLOYEE).status_code, 403)

    def test_it_support_can(self):
        self.assertEqual(self.log_work().status_code, 201)

    def test_it_is_recorded_as_done_rather_than_queued(self):
        """There is nobody waiting on it and nothing to approve -- it has
        already happened. Entering it at 'pending' would mean walking it
        through a workflow after the fact."""
        self.log_work()
        t = SupportTicket.objects.get(origin='logged')
        self.assertEqual(t.status, 'closed')
        self.assertEqual(t.performed_by_email, IT_STAFF)
        self.assertEqual(t.performed_on, timezone.localdate())
        self.assertEqual(t.time_spent_minutes, 45)
        self.assertEqual([e.action for e in t.events.all()], ['logged'])

    def test_it_belongs_to_the_day_the_work_happened(self):
        """This is usually written up afterwards -- at the end of the day, or
        on Monday for something done on Friday."""
        friday = timezone.localdate() - timedelta(days=3)
        self.assertEqual(self.log_work(performed_on=friday.isoformat()).status_code, 201)
        self.assertEqual(SupportTicket.objects.get(origin='logged').performed_on, friday)

    def test_work_cannot_be_logged_in_the_future(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        self.assertEqual(self.log_work(performed_on=tomorrow.isoformat()).status_code, 400)

    def test_a_mistyped_year_is_caught(self):
        """Otherwise it is filed under a month nobody will look at again."""
        long_ago = timezone.localdate() - timedelta(days=400)
        self.assertEqual(self.log_work(performed_on=long_ago.isoformat()).status_code, 400)

    def test_an_unreadable_date_is_refused(self):
        self.assertEqual(self.log_work(performed_on='12/09/2026').status_code, 400)

    def test_closing_a_logged_job_does_not_move_its_date(self):
        """Logged as still running on Friday, closed on Monday. If closing
        stamped today, the job would move -- and across a month end, into the
        wrong month's report."""
        friday = timezone.localdate() - timedelta(days=3)
        r = self.log_work(performed_on=friday.isoformat(), status='in_progress')
        tid = r.json()['id']
        self.client.patch(f'{API}/tickets/{tid}/', {'action': 'close'},
                          content_type='application/json', **auth(IT_STAFF))
        t = SupportTicket.objects.get(id=tid)
        self.assertEqual(t.status, 'closed')
        self.assertEqual(t.performed_on, friday)

    def test_an_employee_never_sees_the_teams_work_log(self):
        """It is a record of what IT did, not correspondence with them."""
        self.log_work()
        self.raise_ticket()
        rows = self.client.get(f'{API}/tickets/', **auth(EMPLOYEE)).json()['results']
        self.assertTrue(all(r['origin'] != 'logged' for r in rows))

    def test_a_logged_job_cannot_smuggle_a_dangerous_attachment(self):
        r = self.client.post(f'{API}/tickets/', {
            'origin': 'logged', 'subject': 's', 'description': 'd',
            'attachments': SimpleUploadedFile('payload.html', b'<script>')}, **auth(IT_STAFF))
        self.assertEqual(r.status_code, 400)


class WhatTheMonthAddsUpTo(HelpdeskBase):
    """The report has to count both kinds of work, and keep them apart."""

    def setUp(self):
        super().setUp()
        AdminUser.objects.create(email=OTHER_IT, name='Sana', scope='it_support')

    def close_a_raised_ticket(self, closer=IT_STAFF):
        tid = self.raise_ticket().json()['id']
        for action in ('approve', 'start'):
            self.client.patch(f'{API}/tickets/{tid}/', {'action': action},
                              content_type='application/json', **auth(IT_STAFF))
        self.client.patch(f'{API}/tickets/{tid}/', {'action': 'close'},
                          content_type='application/json', **auth(closer))
        return tid

    def log_one(self, **over):
        body = {'origin': 'logged', 'subject': 'Rebuilt a laptop', 'description': 'Joiner setup',
                'category': 'it_asset_request', 'time_spent_minutes': 30}
        body.update(over)
        return self.client.post(f'{API}/tickets/', body,
                                content_type='application/json', **auth(IT_STAFF))

    def report(self, email=IT_STAFF, month=''):
        q = f'?month={month}' if month else ''
        return self.client.get(f'{API}/work-report/{q}', **auth(email))

    def test_an_employee_cannot_read_it(self):
        self.assertEqual(self.report(EMPLOYEE).status_code, 403)

    def test_both_kinds_are_counted_and_kept_apart(self):
        self.close_a_raised_ticket()
        self.log_one()
        d = self.report().json()
        self.assertEqual(d['total'], 2)
        self.assertEqual(d['from_tickets'], 1)
        self.assertEqual(d['logged_directly'], 1)

    def test_a_ticket_is_credited_to_whoever_finished_it(self):
        """Not to whoever picked it up -- the row has one slot and closing
        is the fact the report is about."""
        self.close_a_raised_ticket(closer=OTHER_IT)
        people = {p['email']: p for p in self.report().json()['people']}
        self.assertEqual(people[OTHER_IT]['closed'], 1)
        self.assertNotIn(IT_STAFF, people)

    def test_people_are_named_not_emailed(self):
        self.log_one()
        self.assertEqual(self.report().json()['people'][0]['name'], 'IT Desk')

    def test_time_is_totalled(self):
        self.log_one()
        self.log_one(time_spent_minutes=15)
        self.assertEqual(self.report().json()['minutes_recorded'], 45)

    def test_work_lands_in_the_month_it_was_done(self):
        """A ticket raised in March and closed in April is April's work."""
        last_month = timezone.localdate().replace(day=1) - timedelta(days=1)
        self.log_one(performed_on=last_month.isoformat())
        self.assertEqual(self.report(month=f'{last_month:%Y-%m}').json()['total'], 1)
        self.assertEqual(self.report().json()['total'], 0)

    def test_an_empty_month_reads_as_empty_not_as_broken(self):
        d = self.report(month='2019-03').json()
        self.assertEqual(d['total'], 0)
        self.assertEqual(d['label'], 'March 2019')

    def test_a_junk_month_falls_back_to_this_one(self):
        self.assertEqual(self.report(month='rubbish').json()['month'],
                         f'{timezone.localdate():%Y-%m}')

    def test_logged_work_is_not_counted_as_demand_on_the_dashboard(self):
        """Analytics measures what people asked for. Counting the team's own
        jobs there would say the helpdesk got busier every time IT wrote one
        up -- and their near-zero age would drag the resolution average down
        with it."""
        self.close_a_raised_ticket()
        self.log_one()
        d = self.client.get(f'{API}/analytics/?days=30', **auth(SUPER_ADMIN_EMAIL)).json()
        self.assertEqual(d['tickets']['total'], 1)
        self.assertEqual(d['tickets']['logged_directly'], 1)
        self.assertEqual(d['totals']['tickets'], 2)


class HowLongAndWhen(TestCase):
    """The after-hours arithmetic on its own.

    A duration cannot show WHEN: "ninety minutes" reads the same whether it
    was a Tuesday afternoon or 23:00 to 00:30 bringing a server back. Someone
    who works late should be able to point at it, so it is computed from the
    window rather than claimed.
    """

    WED = date(2026, 9, 23)
    SAT = date(2026, 9, 26)
    FRI = date(2026, 9, 25)

    def test_a_normal_span(self):
        self.assertEqual(duration_minutes(time(10, 0), time(11, 30)), 90)

    def test_past_midnight_is_not_a_negative_span(self):
        """The case this exists to measure, so it is read as the next day
        rather than as a mistake."""
        self.assertEqual(duration_minutes(time(23, 0), time(0, 30)), 90)

    def test_a_job_inside_the_working_day_is_not_after_hours(self):
        self.assertEqual(after_hours_minutes(self.WED, time(11, 0), time(12, 0)), 0)

    def test_the_part_before_the_office_opens(self):
        self.assertEqual(after_hours_minutes(self.WED, time(8, 30), time(10, 30)), 60)

    def test_the_part_after_it_closes(self):
        self.assertEqual(after_hours_minutes(self.WED, time(18, 0), time(19, 30)), 60)

    def test_a_late_night(self):
        self.assertEqual(after_hours_minutes(self.WED, time(23, 0), time(0, 30)), 90)

    def test_an_all_nighter_into_the_next_working_morning(self):
        """22:00 to 10:30 is twelve and a half hours, of which only the hour
        after 09:30 was inside office hours."""
        self.assertEqual(after_hours_minutes(self.WED, time(22, 0), time(10, 30)), 690)

    def test_a_saturday_is_entirely_after_hours(self):
        self.assertEqual(after_hours_minutes(self.SAT, time(11, 0), time(13, 0)), 120)

    def test_friday_night_rolling_into_saturday_stays_after_hours(self):
        self.assertEqual(after_hours_minutes(self.FRI, time(23, 0), time(2, 0)), 180)

    def test_no_window_means_no_figure_which_is_not_zero(self):
        self.assertIsNone(after_hours_minutes(self.WED, None, None))

    def test_minutes_are_derived_from_the_window(self):
        self.assertEqual(resolve_time(self.WED, '22:00', '23:30', None)[0], 90)

    def test_a_typed_duration_wins_but_the_window_still_dates_it(self):
        """People round, and a job with a break in the middle is honestly two
        hours of work inside a four-hour window."""
        minutes, _, _, after = resolve_time(self.WED, '17:00', '21:00', 120)
        self.assertEqual(minutes, 120)
        self.assertEqual(after, 150)

    def test_half_a_window_is_refused(self):
        self.assertEqual(resolve_time(self.WED, '22:00', '', None),
                         'Give both a start and an end time, or neither.')

    def test_an_unreadable_time_is_refused(self):
        self.assertIn('HH:MM', resolve_time(self.WED, '10 pm', '23:00', None))

    def test_giving_nothing_is_fine(self):
        self.assertIsNone(resolve_time(self.WED, '', '', None)[0])


class TimeOnBothKindsOfWork(HelpdeskBase):
    """Time is recorded when a ticket is closed as well as on a logged job.
    Otherwise half the month's work carries no time and the two kinds cannot
    be compared."""

    def in_progress_ticket(self):
        tid = self.raise_ticket().json()['id']
        for action in ('approve', 'start'):
            self.client.patch(f'{API}/tickets/{tid}/', {'action': action},
                              content_type='application/json', **auth(IT_STAFF))
        return tid

    def test_closing_a_ticket_records_the_hours_worked(self):
        tid = self.in_progress_ticket()
        r = self.client.patch(f'{API}/tickets/{tid}/',
                              {'action': 'close', 'worked_from': '18:00', 'worked_to': '19:30'},
                              content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 200)
        t = SupportTicket.objects.get(id=tid)
        self.assertEqual(t.time_spent_minutes, 90)
        self.assertEqual(t.worked_from, time(18, 0))

    def test_a_bad_time_refuses_the_close_and_changes_nothing(self):
        """A request that is refused must leave the ticket where it was."""
        tid = self.in_progress_ticket()
        r = self.client.patch(f'{API}/tickets/{tid}/',
                              {'action': 'close', 'worked_from': '9 am', 'worked_to': '10:00'},
                              content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(SupportTicket.objects.get(id=tid).status, 'in_progress')

    def test_closing_without_any_time_is_still_allowed(self):
        """A time nobody has is worse than none — it would be a guess sitting
        in a report."""
        tid = self.in_progress_ticket()
        r = self.client.patch(f'{API}/tickets/{tid}/', {'action': 'close'},
                              content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(SupportTicket.objects.get(id=tid).time_spent_minutes)

    def test_a_logged_job_takes_a_window_too(self):
        r = self.client.post(f'{API}/tickets/', {
            'origin': 'logged', 'subject': 'Brought the server back up',
            'description': 'Disk filled', 'category': 'server_storage',
            'worked_from': '23:00', 'worked_to': '00:30',
        }, content_type='application/json', **auth(IT_STAFF))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['ticket']['time_spent_minutes'], 90)
        self.assertEqual(r.json()['ticket']['after_hours_minutes'], 90)

    def test_the_month_separates_the_late_hours(self):
        wednesday = date(2026, 9, 23)
        for frm, to in (('23:00', '00:30'), ('14:00', '14:20')):
            self.client.post(f'{API}/tickets/', {
                'origin': 'logged', 'subject': f'Job {frm}', 'description': 'x',
                'category': 'other', 'performed_on': wednesday.isoformat(),
                'worked_from': frm, 'worked_to': to,
            }, content_type='application/json', **auth(IT_STAFF))

        d = self.client.get(f'{API}/work-report/?month=2026-09', **auth(IT_STAFF)).json()
        self.assertEqual(d['minutes_recorded'], 110)
        self.assertEqual(d['after_hours_minutes'], 90)
        self.assertEqual(d['people'][0]['after_hours_minutes'], 90)

    def test_a_job_with_no_window_contributes_no_late_hours(self):
        """Not guessed at — a job nobody timed is silent, not zero-and-inside."""
        self.client.post(f'{API}/tickets/', {
            'origin': 'logged', 'subject': 'Untimed', 'description': 'x',
            'category': 'other', 'time_spent_minutes': 30,
        }, content_type='application/json', **auth(IT_STAFF))
        d = self.client.get(f'{API}/work-report/', **auth(IT_STAFF)).json()
        self.assertEqual(d['minutes_recorded'], 30)
        self.assertEqual(d['after_hours_minutes'], 0)


class TheEmployeeMaster(HelpdeskBase):
    """The list comes from the company directory, not a spreadsheet uploaded
    every time somebody joins or leaves."""

    def setUp(self):
        super().setUp()
        PortalUser.objects.create(employee_code='E001', name='Priya Sharma',
                                  email='priya.sharma@gmail.com', department='Accounts',
                                  is_active=True)
        PortalUser.objects.create(employee_code='E002', name='Ravi Kumar',
                                  email='ravi@apisindia.com', department='IT', is_active=True)
        PortalUser.objects.create(employee_code='E003', name='Left Already',
                                  email='gone@gmail.com', is_active=False)
        PortalUser.objects.create(employee_code='E004', name='No Address', email='')

    def sync(self):
        return self.client.post(f'{API}/employees/sync/', **auth(SUPER_ADMIN_EMAIL))

    def test_only_the_super_admin_may_sync(self):
        self.assertEqual(self.client.post(f'{API}/employees/sync/',
                                          **auth(EMPLOYEE)).status_code, 403)

    def test_it_brings_people_in(self):
        d = self.sync().json()
        self.assertEqual(d['created'], 3)
        self.assertEqual(d['skipped_no_email'], 1)

    def test_somebody_with_no_email_is_skipped_not_invented(self):
        """Identity here IS the email address — a row without one could never
        be matched to anybody signing in."""
        self.sync()
        self.assertFalse(Employee.objects.filter(name='No Address').exists())

    def test_a_leaver_is_marked_not_deleted(self):
        """Their name is on tickets they raised; a directory that forgets them
        makes that history unreadable."""
        self.sync()
        self.assertFalse(Employee.objects.get(email='gone@gmail.com').is_active)

    def test_running_it_twice_duplicates_nobody(self):
        self.sync()
        self.assertEqual(self.sync().json()['created'], 0)
        self.assertEqual(Employee.objects.count(), 3)

    def test_somebody_added_by_hand_survives_a_sync(self):
        """They are here BECAUSE they are not in HRMS, so a sync can never
        bring them back — taking them out would be unrecoverable."""
        self.client.post(f'{API}/employees/add/',
                         {'email': 'contractor@vendor.com', 'name': 'Contractor'},
                         content_type='application/json', **auth(SUPER_ADMIN_EMAIL))
        self.sync()
        row = Employee.objects.get(email='contractor@vendor.com')
        self.assertTrue(row.is_active)
        self.assertEqual(row.source, 'manual')

    def test_clearing_keeps_them_too(self):
        self.sync()
        self.client.post(f'{API}/employees/add/',
                         {'email': 'contractor@vendor.com', 'name': 'Contractor'},
                         content_type='application/json', **auth(SUPER_ADMIN_EMAIL))
        self.client.delete(f'{API}/employees/', **auth(SUPER_ADMIN_EMAIL))
        self.assertEqual(Employee.objects.count(), 1)
        self.assertEqual(Employee.objects.first().source, 'manual')

    def test_being_on_the_directory_is_what_makes_you_an_employee(self):
        """The rule was "anything @apisindia.com", and only about a quarter of
        the company has such an address."""
        self.sync()
        self.assertEqual(resolve_role('priya.sharma@gmail.com'), 'employee')

    def test_somebody_who_has_left_cannot_sign_in(self):
        self.sync()
        self.assertIsNone(resolve_role('gone@gmail.com'))

    def test_a_stranger_still_cannot(self):
        self.sync()
        self.assertIsNone(resolve_role('attacker@gmail.com'))

    def test_a_company_address_still_works_as_a_fallback(self):
        """So nobody who can sign in today is locked out by this."""
        self.assertEqual(resolve_role('someone.new@apisindia.com'), 'employee')

    def test_the_whole_staff_list_is_not_public(self):
        """Every name, email and department — readable by anyone signed in,
        which is most of the company."""
        self.sync()
        self.assertEqual(self.client.get(f'{API}/employees/', **auth(EMPLOYEE)).status_code, 403)


class SayingWhoHandlesWhat(HelpdeskBase):
    """One control per person, because from the screen it is one decision."""

    def setUp(self):
        super().setUp()
        Employee.objects.create(email='ravi@apisindia.com', name='Ravi Kumar',
                                source='directory', is_active=True)

    def set_role(self, scope, email='ravi@apisindia.com', who=SUPER_ADMIN_EMAIL):
        return self.client.post(f'{API}/admins/role/', {'email': email, 'scope': scope},
                                content_type='application/json', **auth(who))

    def test_making_somebody_it_support(self):
        self.assertEqual(self.set_role('it_support').status_code, 201)
        self.assertEqual(resolve_role('ravi@apisindia.com'), 'it_support')

    def test_changing_their_role_in_one_call(self):
        self.set_role('it_support')
        self.assertEqual(self.set_role('admin').status_code, 200)
        self.assertEqual(resolve_role('ravi@apisindia.com'), 'admin')
        self.assertEqual(AdminUser.objects.filter(email='ravi@apisindia.com').count(), 1)

    def test_taking_it_away_again(self):
        self.set_role('admin')
        self.set_role('employee')
        self.assertEqual(resolve_role('ravi@apisindia.com'), 'employee')
        self.assertFalse(AdminUser.objects.filter(email='ravi@apisindia.com').exists())

    def test_the_directory_shows_the_change(self):
        self.set_role('it_support')
        self.assertEqual(Employee.objects.get(email='ravi@apisindia.com').role, 'it_support')

    def test_an_employee_cannot_promote_themselves(self):
        self.assertEqual(self.set_role('admin', email=EMPLOYEE, who=EMPLOYEE).status_code, 403)

    def test_the_super_admin_cannot_be_demoted_here(self):
        self.assertEqual(self.set_role('employee', email=SUPER_ADMIN_EMAIL).status_code, 400)

    def test_an_invented_role_is_refused(self):
        self.assertEqual(self.set_role('wizard').status_code, 400)

    def test_a_directory_person_cannot_be_removed_by_hand(self):
        """A removal here would last until the next sync — a confusing way to
        lose work."""
        row = Employee.objects.get(email='ravi@apisindia.com')
        self.assertEqual(self.client.delete(f'{API}/employees/{row.id}/',
                                            **auth(SUPER_ADMIN_EMAIL)).status_code, 400)


class TheOverview(HelpdeskBase):
    """The Super Admin's one screen. Everything on it was visible somewhere
    already; the point is noticing, which does not happen across five tabs."""

    def overview(self, who=SUPER_ADMIN_EMAIL):
        return self.client.get(f'{API}/overview/', **auth(who))

    def test_it_is_the_super_admins_alone(self):
        self.assertEqual(self.overview(EMPLOYEE).status_code, 403)
        self.assertEqual(self.overview(IT_STAFF).status_code, 403)
        self.assertEqual(self.client.get(f'{API}/overview/').status_code, 401)

    def test_it_says_when_nobody_is_assigned_to_a_queue(self):
        """A helpdesk with no IT Support accepts tickets and never answers
        them, and nothing on screen said so."""
        AdminUser.objects.all().delete()
        said = ' '.join(self.overview().json()['problems']).lower()
        self.assertIn('it support', said)
        self.assertIn('admin', said)

    def test_a_forgotten_request_is_counted_as_forgotten(self):
        t = SupportTicket.objects.create(requested_by_name='P', requested_by_email=EMPLOYEE,
                                         subject='VPN down', status='pending')
        SupportTicket.objects.filter(id=t.id).update(
            created_at=timezone.now() - timedelta(days=9))
        d = self.overview().json()
        row = next(w for w in d['waiting'] if 'IT tickets' in w['what'])
        self.assertEqual(row['oldest_days'], 9)
        self.assertEqual(row['stale'], 1)
        self.assertEqual(d['stale_total'], 1)

    def test_logged_work_is_not_counted_as_a_waiting_request(self):
        self.client.post(f'{API}/tickets/', {'origin': 'logged', 'subject': 'Restarted a server',
                                             'description': 'x', 'category': 'server_storage'},
                         content_type='application/json', **auth(IT_STAFF))
        d = self.overview().json()
        row = next(w for w in d['waiting'] if 'IT tickets' in w['what'])
        self.assertEqual(row['count'], 0)
        self.assertEqual(d['today']['jobs_done'], 1)

    def test_an_out_of_date_directory_is_noticed(self):
        Employee.objects.create(email='p@apisindia.com', name='P', source='directory',
                                is_active=True, synced_at=timezone.now() - timedelta(days=45))
        self.assertTrue(any('last synced' in p for p in self.overview().json()['problems']))


class AnExpiredSessionSaysSo(HelpdeskBase):
    """A session that has aged out must answer 401, not "you are not an admin".

    actor_role gives (None, '') for an expired token -- the same shape as a
    signed-in employee without the role. Handlers that checked the role first
    told an admin looking at the approvals screen that only an admin could
    approve, which is both wrong and unactionable.

    It also left the app stuck: rpFetch signs out on a 401 and ignores a 403,
    so the dead session survived until somebody reloaded by hand. That is why
    the report said a hard refresh "fixed" it.
    """

    DEAD = {'HTTP_X_ADMINPULSE_SESSION': 'expired-or-forged-token'}

    def test_approving_a_booking(self):
        r = self.client.patch(f'{API}/bookings/1/', {'action': 'approve'},
                              content_type='application/json', **self.DEAD)
        self.assertEqual(r.status_code, 401, r.content[:120])

    def test_approving_an_item_request(self):
        r = self.client.patch(f'{API}/resource-requests/1/', {'action': 'approve'},
                              content_type='application/json', **self.DEAD)
        self.assertEqual(r.status_code, 401, r.content[:120])

    def test_acting_on_a_ticket(self):
        r = self.client.patch(f'{API}/tickets/1/', {'action': 'approve'},
                              content_type='application/json', **self.DEAD)
        self.assertEqual(r.status_code, 401, r.content[:120])

    def test_the_message_does_not_blame_the_role(self):
        """The old answer sent somebody hunting for a permissions problem that
        was not there."""
        r = self.client.patch(f'{API}/resource-requests/1/', {'action': 'approve'},
                              content_type='application/json', **self.DEAD)
        self.assertNotIn('admin', (r.json().get('error') or '').lower())

    def test_a_real_admin_is_still_allowed(self):
        """The guard must not have broken the case it sits in front of."""
        AdminUser.objects.create(email='meena@apisindia.com', name='Meena', scope='admin')
        r = self.client.patch(f'{API}/resource-requests/999999/', {'action': 'approve'},
                              content_type='application/json',
                              **auth('meena@apisindia.com'))
        # 404 for the missing row, not 401/403: they got past both gates.
        self.assertEqual(r.status_code, 404, r.content[:120])


class TheRoomGridIsNotPublic(HelpdeskBase):
    """It carries who is meeting where and until when."""

    def test_an_unsigned_caller_is_refused(self):
        self.assertEqual(self.client.get(f'{API}/rooms/').status_code, 401)

    def test_any_signed_in_employee_still_sees_it(self):
        self.assertEqual(self.client.get(f'{API}/rooms/', **auth(EMPLOYEE)).status_code, 200)


class PendingBookingsAreVisible(HelpdeskBase):
    """Booking a room and being told it went for approval, then seeing the room
    say "nothing booked today", left no way to tell the request existed -- and
    invited the next person to ask for the same slot."""

    def test_a_pending_request_shows_on_the_room(self):
        room = Room.objects.filter(is_active=True).first()
        today = timezone.localdate()
        BookingRequest.objects.create(
            room=room, requested_by_name='Ravi', requested_by_email=IT_STAFF,
            date=today, start_time=time(15, 0), end_time=time(16, 0),
            purpose='internal_meeting', status='pending')
        grid = self.client.get(f'{API}/rooms/', **auth(IT_STAFF)).json()['results']
        mine = next(r for r in grid if r['id'] == room.id)
        self.assertEqual(len(mine['pending']), 1)
        self.assertEqual(mine['pending'][0]['requested_by_name'], 'Ravi')

    def test_an_approved_one_is_not_listed_as_pending(self):
        room = Room.objects.filter(is_active=True).first()
        BookingRequest.objects.create(
            room=room, requested_by_name='Ravi', requested_by_email=IT_STAFF,
            date=timezone.localdate(), start_time=time(15, 0), end_time=time(16, 0),
            purpose='internal_meeting', status='approved')
        grid = self.client.get(f'{API}/rooms/', **auth(IT_STAFF)).json()['results']
        mine = next(r for r in grid if r['id'] == room.id)
        self.assertEqual(mine['pending'], [])


ADMIN_STAFF = 'meena.admin@apisindia.com'


class AnAdminsWorkIsAdminWork(HelpdeskBase):
    """Two halves of the same complaint: an Admin logging a job had only IT's
    categories to file it under, and the work their own queues produced was
    not counted at all. Between them, an Admin's month read as almost empty."""

    def setUp(self):
        super().setUp()
        AdminUser.objects.create(email=ADMIN_STAFF, name='Meena', scope='admin')

    def log(self, email, category):
        return self.client.post(f'{API}/tickets/', {
            'origin': 'logged', 'subject': 'Got the pantry tap fixed',
            'description': 'Plumber came at 11.', 'category': category,
            'performed_by_name': 'Meena', 'time_spent_minutes': 45,
        }, content_type='application/json', **auth(email))

    def test_an_admin_can_file_a_job_under_an_admin_category(self):
        r = self.log(ADMIN_STAFF, 'plumbing')
        self.assertEqual(r.status_code, 201, r.content[:200])
        self.assertEqual(SupportTicket.objects.get(pk=r.json()['id']).category, 'plumbing')

    def test_a_raised_ticket_still_cannot_be_admin_work(self):
        """The IT queue is for IT. An admin category on a request falls back
        to 'other' rather than filling IT's queue with plumbing."""
        t = self.raise_ticket(category='housekeeping').json()
        self.assertEqual(SupportTicket.objects.get(pk=t['id']).category, 'other')

    def test_the_report_names_the_admin_category(self):
        self.log(ADMIN_STAFF, 'plumbing')
        d = self.client.get(f'{API}/work-report/', **auth(ADMIN_STAFF)).json()
        self.assertIn('Plumbing', [c['label'] for c in d['by_category']])

    def _a_fulfilled_request(self, when=None):
        req = ResourceRequest.objects.create(
            requested_by_name='Priya', requested_by_email=EMPLOYEE,
            category='stationery_office_supplies', item_name='A4 paper', quantity=5,
            status='fulfilled', reviewed_by=ADMIN_STAFF,
            fulfilled_by=ADMIN_STAFF, fulfilled_at=when or timezone.now())
        return req

    def test_fulfilling_an_item_request_counts_as_work_done(self):
        self._a_fulfilled_request()
        d = self.client.get(f'{API}/work-report/', **auth(ADMIN_STAFF)).json()
        self.assertEqual(d['total'], 1)
        self.assertEqual(d['from_queue']['item_requests'], 1)
        mine = next(p for p in d['people'] if p['email'] == ADMIN_STAFF)
        self.assertEqual(mine['total'], 1)

    def test_approving_a_room_booking_counts_too(self):
        room = Room.objects.filter(is_active=True).first()
        BookingRequest.objects.create(
            room=room, requested_by_name='Priya', requested_by_email=EMPLOYEE,
            date=timezone.localdate(), start_time=time(15, 0), end_time=time(16, 0),
            purpose='internal_meeting', status='approved',
            reviewed_by=ADMIN_STAFF, reviewed_at=timezone.now())
        d = self.client.get(f'{API}/work-report/', **auth(ADMIN_STAFF)).json()
        self.assertEqual(d['from_queue']['room_bookings'], 1)

    def test_approving_your_own_request_is_not_work(self):
        """Staff requests are auto-approved, so this would let anyone raise
        their own month's figures by booking rooms."""
        room = Room.objects.filter(is_active=True).first()
        BookingRequest.objects.create(
            room=room, requested_by_name='Meena', requested_by_email=ADMIN_STAFF,
            date=timezone.localdate(), start_time=time(9, 0), end_time=time(10, 0),
            purpose='internal_meeting', status='approved',
            reviewed_by=ADMIN_STAFF, reviewed_at=timezone.now())
        d = self.client.get(f'{API}/work-report/', **auth(ADMIN_STAFF)).json()
        self.assertEqual(d['total'], 0)

    def test_approved_but_not_yet_handed_over_is_not_done(self):
        ResourceRequest.objects.create(
            requested_by_name='Priya', requested_by_email=EMPLOYEE,
            category='stationery_office_supplies', item_name='A4 paper',
            status='approved', reviewed_by=ADMIN_STAFF, reviewed_at=timezone.now())
        d = self.client.get(f'{API}/work-report/', **auth(ADMIN_STAFF)).json()
        self.assertEqual(d['total'], 0)
        self.assertEqual(d['still_open'], 1)

    def test_work_on_the_last_day_of_the_month_is_in_the_month(self):
        """A date range against a datetime column ends at midnight, which
        drops the whole of the last day."""
        today = timezone.localdate()
        last = date(today.year, today.month, monthrange(today.year, today.month)[1])
        at = timezone.make_aware(datetime.combine(last, time(18, 30)))
        self._a_fulfilled_request(when=at)
        d = self.client.get(f'{API}/work-report/', **auth(ADMIN_STAFF)).json()
        self.assertEqual(d['total'], 1, f"{last} fell out of {d['label']}")

    def test_the_spreadsheet_agrees_with_the_screen(self):
        self._a_fulfilled_request()
        self.log(ADMIN_STAFF, 'plumbing')
        r = self.client.get(f'{API}/work-report/export/', **auth(ADMIN_STAFF))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content[:2] == b'PK', 'not a workbook')
