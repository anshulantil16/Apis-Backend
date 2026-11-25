"""Wishes on a colleague's birthday or work anniversary.

The tests worth protecting here are the ones about TIME, because the feature's
whole expiry rule is "the feed asks for today's date". There is no cleanup job
to test; what has to hold is that a wish sent early stays hidden until the day,
that it is there all of that day, and that it is gone the morning after without
anything having run. Those three are `ItArrivesOnTheDay`.

The rest guard the ways a free-text box that reaches a colleague could be
misused: wishing somebody who is not celebrating, wishing a leaver, wishing the
same person fifty times, or wishing anonymously.
"""
from datetime import date
from unittest import mock

from django.test import TestCase

from .models import PortalSession, PortalUser, Wish
from . import wishes

WISHES = '/api/accounts/portal/wishes/'
SEEN = '/api/accounts/portal/wishes/seen/'
CELEBRATIONS = '/api/accounts/portal/celebrations/'

# A fixed "today" so a test that passes in June still passes in December. Not
# a leap year, and far enough from a month end that +7 days does not wrap --
# the wrapping cases get their own tests.
TODAY = date(2027, 3, 10)


def person(code, name, dob=None, doj=None, **kw):
    return PortalUser.objects.create(
        employee_code=code, name=name,
        email=kw.pop('email', f'{code.lower()}@apisindia.com'),
        date_of_birth=dob, date_of_joining=doj, **kw)


def auth(user):
    return {'HTTP_AUTHORIZATION': f'Bearer {PortalSession.start(user)}'}


def frozen(d=TODAY):
    """Pins the server's idea of today. Patched at accounts.wishes.today_ist,
    which is the single place both the endpoints and the views read it."""
    return mock.patch.object(wishes, 'today_ist', return_value=d)


class Sending(TestCase):
    def setUp(self):
        self.me = person('HO0001', 'Arun Mishra', dob=date(1990, 6, 1))
        # Her birthday is TODAY. It has to be: a wish can only be sent on the
        # day, so every ordinary send test needs a recipient celebrating now.
        self.her = person('HO0002', 'Priya Shah', dob=date(1992, 3, 10))
        self.auth = auth(self.me)

    def post(self, **body):
        with frozen():
            return self.client.post(WISHES, body, content_type='application/json',
                                    **self.auth)

    def test_a_wish_is_recorded_against_the_day_it_is_for(self):
        r = self.post(to=self.her.id, occasion='birthday', message='Have a great one')
        self.assertEqual(r.status_code, 201)
        w = Wish.objects.get()
        # The day it is FOR, computed on the server from her own record and
        # never taken from the request.
        self.assertEqual(w.occasion_date, TODAY)
        self.assertEqual(w.message, 'Have a great one')
        self.assertEqual(w.recipient_id, self.her.id)

    def test_the_sender_is_told_it_lands_today(self):
        r = self.post(to=self.her.id, occasion='birthday')
        self.assertEqual(r.json()['arrives_on'], '10 Mar')
        self.assertTrue(r.json()['arrives_today'])

    def test_a_wish_with_no_message_still_sends(self):
        self.assertEqual(self.post(to=self.her.id, occasion='birthday').status_code, 201)
        self.assertEqual(Wish.objects.get().message, '')

    def test_pressing_the_button_twice_edits_rather_than_duplicates(self):
        self.post(to=self.her.id, occasion='birthday', message='first')
        r = self.post(to=self.her.id, occasion='birthday', message='second')
        self.assertEqual(r.status_code, 201)
        self.assertFalse(r.json()['created'])
        self.assertEqual(Wish.objects.count(), 1)
        self.assertEqual(Wish.objects.get().message, 'second')

    def test_the_senders_name_is_always_stored(self):
        self.post(to=self.her.id, occasion='birthday')
        self.assertEqual(Wish.objects.get().sender_name, 'Arun Mishra')

    def test_a_message_is_tidied_and_capped(self):
        self.post(to=self.her.id, occasion='birthday', message='  lots\n\n   of   space  ')
        self.assertEqual(Wish.objects.get().message, 'lots of space')
        Wish.objects.all().delete()
        self.post(to=self.her.id, occasion='birthday', message='x' * 500)
        self.assertEqual(len(Wish.objects.get().message), 280)

    def test_you_cannot_wish_yourself(self):
        r = self.post(to=self.me.id, occasion='birthday')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Wish.objects.count(), 0)

    def test_a_signed_out_caller_cannot_send(self):
        with frozen():
            r = self.client.post(WISHES, {'to': self.her.id, 'occasion': 'birthday'},
                                 content_type='application/json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(Wish.objects.count(), 0)

    def test_an_unknown_occasion_is_refused(self):
        self.assertEqual(self.post(to=self.her.id, occasion='promotion').status_code, 400)
        self.assertEqual(Wish.objects.count(), 0)

    def test_a_missing_recipient_is_refused(self):
        self.assertEqual(self.post(to=999999, occasion='birthday').status_code, 404)

    def test_a_non_numeric_recipient_is_refused_not_crashed(self):
        self.assertEqual(self.post(to='nobody', occasion='birthday').status_code, 400)


class OnlyPeopleActuallyCelebrating(TestCase):
    """Not a message channel in a party hat. See accounts/wishes.py."""

    def setUp(self):
        self.me = person('HO0001', 'Arun Mishra')
        self.auth = auth(self.me)

    def post(self, target, occasion='birthday'):
        with frozen():
            return self.client.post(WISHES, {'to': target.id, 'occasion': occasion},
                                    content_type='application/json', **self.auth)

    def test_somebody_whose_birthday_is_months_away_cannot_be_wished(self):
        far = person('HO0003', 'Far Away', dob=date(1990, 9, 20))   # ~6 months off
        self.assertEqual(self.post(far).status_code, 400)
        self.assertEqual(Wish.objects.count(), 0)

    def test_somebody_with_no_date_on_record_cannot_be_wished(self):
        blank = person('HO0004', 'No Dates')
        self.assertEqual(self.post(blank).status_code, 400)

    def test_a_leaver_cannot_be_wished(self):
        gone = person('HO0005', 'Someone Who Left', dob=date(1990, 3, 10), is_active=False)
        self.assertEqual(self.post(gone).status_code, 404)
        self.assertEqual(Wish.objects.count(), 0)

    def test_only_today_is_wishable(self):
        """The rule the whole feature turns on.

        The cards show the week ahead and "View all" the rest of the month, so
        most rows on screen are not wishable. The button is hidden on those;
        this is the check that makes hiding it more than a decoration.
        """
        today = person('HO0006', 'Today', dob=date(1990, 3, 10))
        self.assertEqual(self.post(today).status_code, 201)

        tomorrow = person('HO0007', 'Tomorrow', dob=date(1990, 3, 11))
        self.assertEqual(self.post(tomorrow).status_code, 400)

        # Yesterday is not "a day late", it is eleven months early.
        yesterday = person('HO0008', 'Yesterday', dob=date(1990, 3, 9))
        self.assertEqual(self.post(yesterday).status_code, 400)

        self.assertEqual(Wish.objects.count(), 1)

    def test_a_first_anniversary_has_not_happened_yet(self):
        # Joined today. The date matches and it is still not an anniversary --
        # "1 year" is the point of one, and this is day zero.
        new = person('HO0009', 'Joined Today', doj=date(2027, 3, 10))
        self.assertEqual(self.post(new, 'anniversary').status_code, 400)

    def test_a_real_anniversary_can_be_wished(self):
        old = person('HO0010', 'Five Years', doj=date(2022, 3, 10))
        self.assertEqual(self.post(old, 'anniversary').status_code, 201)
        self.assertEqual(Wish.objects.get().occasion_date, TODAY)

    def test_a_birthday_wish_cannot_be_sent_as_an_anniversary(self):
        # Date of birth filled in, date of joining empty: there is no
        # anniversary to address, whatever the caller asks for.
        her = person('HO0011', 'Priya Shah', dob=date(1992, 3, 10))
        self.assertEqual(self.post(her, 'anniversary').status_code, 400)


class ItArrivesOnTheDay(TestCase):
    """The expiry rule, which is the whole point: the feed asks for today."""

    def setUp(self):
        self.her = person('HO0002', 'Priya Shah', dob=date(1992, 3, 10))
        self.me = person('HO0001', 'Arun Mishra')
        with frozen():
            self.client.post(WISHES, {'to': self.her.id, 'occasion': 'birthday',
                                      'message': 'Have a great one'},
                             content_type='application/json', **auth(self.me))

    def feed(self, on):
        with frozen(on):
            return self.client.get(WISHES, **auth(self.her)).json()

    def test_it_is_there_on_the_day(self):
        got = self.feed(TODAY)
        self.assertEqual(len(got['wishes']), 1)
        self.assertEqual(got['wishes'][0]['from'], 'Arun Mishra')
        self.assertEqual(got['wishes'][0]['message'], 'Have a great one')
        self.assertEqual(got['unseen'], 1)

    def test_it_is_gone_the_next_day_with_nothing_having_run(self):
        self.assertEqual(len(self.feed(TODAY)['wishes']), 1)
        self.assertEqual(self.feed(date(2027, 3, 11))['wishes'], [])
        # Nothing was deleted. No job ran. The row simply stopped matching,
        # which is the one kind of expiry that cannot break by not running.
        self.assertEqual(Wish.objects.count(), 1)

    def test_a_wish_with_no_message_reads_as_the_plain_wish(self):
        Wish.objects.update(message='')
        got = self.feed(TODAY)['wishes'][0]
        self.assertEqual(got['message'], 'Happy birthday!')
        self.assertFalse(got['has_message'])

    def test_only_my_own_wishes_reach_me(self):
        someone = person('HO0003', 'Somebody Else', dob=date(1992, 3, 10))
        with frozen():
            got = self.client.get(WISHES, **auth(someone)).json()
        self.assertEqual(got['wishes'], [])

    def test_a_signed_out_caller_gets_nothing(self):
        self.assertEqual(self.client.get(WISHES).status_code, 401)


class TheDayItself(TestCase):
    """What the banner needs: whether today is mine, and what it is."""

    def ask(self, user, on):
        with frozen(on):
            return self.client.get(WISHES, **auth(user)).json()['my_occasions']

    def test_a_birthday_today(self):
        u = person('HO0001', 'Arun Mishra', dob=date(1990, 3, 10))
        self.assertEqual(self.ask(u, TODAY), [{'occasion': 'birthday', 'years': None}])

    def test_an_anniversary_today_carries_the_years(self):
        u = person('HO0002', 'Priya Shah', doj=date(2022, 3, 10))
        self.assertEqual(self.ask(u, TODAY), [{'occasion': 'anniversary', 'years': 5}])

    def test_both_on_one_day(self):
        u = person('HO0003', 'Both', dob=date(1990, 3, 10), doj=date(2022, 3, 10))
        self.assertEqual([o['occasion'] for o in self.ask(u, TODAY)],
                         ['birthday', 'anniversary'])

    def test_a_joining_date_today_is_not_an_anniversary(self):
        u = person('HO0004', 'Joined Today', doj=date(2027, 3, 10))
        self.assertEqual(self.ask(u, TODAY), [])

    def test_an_ordinary_day_is_empty(self):
        u = person('HO0005', 'Nothing Today', dob=date(1990, 7, 4))
        self.assertEqual(self.ask(u, TODAY), [])


class TheUnreadDot(TestCase):
    def setUp(self):
        self.her = person('HO0002', 'Priya Shah', dob=date(1992, 3, 10))
        self.me = person('HO0001', 'Arun Mishra')
        Wish.objects.create(sender=self.me, sender_name='Arun Mishra',
                            recipient=self.her, occasion='birthday',
                            occasion_date=TODAY, message='Happy birthday')

    def test_marking_seen_clears_the_count_but_keeps_the_wish(self):
        with frozen():
            self.assertEqual(self.client.post(SEEN, {}, content_type='application/json',
                                              **auth(self.her)).json()['marked'], 1)
            got = self.client.get(WISHES, **auth(self.her)).json()
        self.assertEqual(got['unseen'], 0)
        # A greeting that vanishes when you glance at it is worse than none.
        self.assertEqual(len(got['wishes']), 1)
        self.assertTrue(got['wishes'][0]['seen'])

    def test_marking_seen_twice_is_harmless(self):
        with frozen():
            self.client.post(SEEN, {}, content_type='application/json', **auth(self.her))
            r = self.client.post(SEEN, {}, content_type='application/json', **auth(self.her))
        self.assertEqual(r.json()['marked'], 0)

    def test_a_signed_out_caller_cannot_mark_anything_seen(self):
        self.assertEqual(self.client.post(SEEN, {}, content_type='application/json').status_code, 401)
        self.assertIsNone(Wish.objects.get().seen_at)


class TheCelebrationsCards(TestCase):
    """The cards have to carry who to wish, and who has been wished already."""

    def setUp(self):
        self.me = person('HO0001', 'Arun Mishra', dob=date(1990, 3, 12))
        self.her = person('HO0002', 'Priya Shah', dob=date(1992, 3, 10))
        self.old = person('HO0003', 'Five Years', doj=date(2022, 3, 10))

    def get(self):
        with frozen():
            return self.client.get(CELEBRATIONS, **auth(self.me)).json()

    def test_each_row_carries_the_id_a_wish_is_addressed_to(self):
        row = next(b for b in self.get()['birthdays'] if b['name'] == 'Priya Shah')
        self.assertEqual(row['id'], self.her.id)
        self.assertFalse(row['wished'])

    def test_my_own_row_is_flagged_so_the_button_can_be_hidden(self):
        mine = next(b for b in self.get()['birthdays'] if b['name'] == 'Arun Mishra')
        self.assertTrue(mine['is_me'])

    def test_a_row_i_have_wished_says_so(self):
        with frozen():
            self.client.post(WISHES, {'to': self.her.id, 'occasion': 'birthday'},
                             content_type='application/json', **auth(self.me))
        row = next(b for b in self.get()['birthdays'] if b['name'] == 'Priya Shah')
        self.assertTrue(row['wished'])

    def test_wishing_a_birthday_does_not_tick_the_anniversary(self):
        # Same person could be on both cards; the two are wished separately.
        both = person('HO0004', 'Both Days', dob=date(1990, 3, 10), doj=date(2022, 3, 10))
        with frozen():
            self.client.post(WISHES, {'to': both.id, 'occasion': 'birthday'},
                             content_type='application/json', **auth(self.me))
        got = self.get()
        self.assertTrue(next(b for b in got['birthdays'] if b['id'] == both.id)['wished'])
        self.assertFalse(next(a for a in got['anniversaries'] if a['id'] == both.id)['wished'])

    def test_the_card_never_shows_a_birth_year(self):
        # The feature added fields to this payload; the old rule still holds.
        for row in self.get()['birthdays']:
            self.assertNotIn('1992', str(row))
            self.assertNotIn('date_of_birth', row)


class TheDateMaths(TestCase):
    """next_occurrence is shared with the celebrations card on purpose."""

    def test_today_counts_as_the_next_occurrence(self):
        self.assertEqual(wishes.next_occurrence(date(1990, 3, 10), TODAY), TODAY)

    def test_it_wraps_across_the_new_year(self):
        dec = date(2027, 12, 20)
        self.assertEqual(wishes.next_occurrence(date(1990, 1, 5), dec), date(2028, 1, 5))

    def test_a_leap_day_birthday_still_happens_every_year(self):
        self.assertEqual(wishes.next_occurrence(date(1992, 2, 29), date(2027, 1, 1)),
                         date(2027, 2, 28))
        # And on the real date in a leap year.
        self.assertEqual(wishes.next_occurrence(date(1992, 2, 29), date(2028, 1, 1)),
                         date(2028, 2, 28))

    def test_a_missing_date_has_no_occurrence(self):
        self.assertIsNone(wishes.next_occurrence(None, TODAY))

    def test_the_card_and_the_wish_agree_on_the_day(self):
        # The bug this guards: the card says 10 Mar, the wish is filed for the
        # 11th, and nobody finds out until somebody's birthday.
        her = person('HO0002', 'Priya Shah', dob=date(1992, 3, 10))
        me = person('HO0001', 'Arun Mishra')
        with frozen():
            card = self.client.get(CELEBRATIONS, **auth(me)).json()
            self.client.post(WISHES, {'to': her.id, 'occasion': 'birthday'},
                             content_type='application/json', **auth(me))
        row = next(b for b in card['birthdays'] if b['id'] == her.id)
        self.assertEqual(row['date'], '10 Mar')
        self.assertEqual(Wish.objects.get().occasion_date.strftime('%d %b').lstrip('0'),
                         row['date'])


class TooManyWishes(TestCase):
    def test_a_daily_ceiling_exists(self):
        me = person('HO0001', 'Arun Mishra')
        her = person('HO0002', 'Priya Shah', dob=date(1992, 3, 10))
        with mock.patch.object(wishes, 'MAX_PER_DAY', 1), frozen():
            first = self.client.post(WISHES, {'to': her.id, 'occasion': 'birthday'},
                                     content_type='application/json', **auth(me))
            other = person('HO0003', 'Someone', dob=date(1992, 3, 10))
            second = self.client.post(WISHES, {'to': other.id, 'occasion': 'birthday'},
                                      content_type='application/json', **auth(me))
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(Wish.objects.count(), 1)
