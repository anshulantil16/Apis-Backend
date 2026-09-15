"""The noticeboard: what the company is told, and what it plans leave around.

Both used to be hard-coded in the frontend. These tests hold the two things
that moving them into the database must not break — the circular has to
survive verbatim, and nothing unapproved may reach the card.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import ActivityLog, PortalSession, PortalUser
from accounts.moderation import ModerationStatus
from noticeboard.models import Announcement, Holiday, HolidayZone


def a_user(email, name='Someone', superadmin=False):
    return PortalUser.objects.create(
        email=email, name=name, employee_code=email.split('@')[0].upper(),
        is_superadmin=superadmin, app_access=['home'])


class Base(TestCase):

    def setUp(self):
        self.staff = a_user('staff@apisindia.com', 'Staff Person')
        self.admin = a_user('admin@apisindia.com', 'Admin Person', superadmin=True)
        self.staff_token = PortalSession.start(self.staff)
        self.admin_token = PortalSession.start(self.admin)

    def auth(self, token):
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def notice(self, **over):
        d = {'title': 'Plant shutdown', 'body': 'The Roorkee plant is closed on Friday.'}
        d.update(over)
        return d


class TheHolidayCircular(Base):
    """The signed circular is the source of truth; the table is a copy of it."""

    def test_the_whole_circular_survived_the_move(self):
        self.assertEqual(HolidayZone.objects.count(), 10)
        self.assertEqual(Holiday.objects.count(), 130)
        for zone in HolidayZone.objects.all():
            self.assertEqual(zone.holidays.count(), 13, f'{zone.key} lost days')

    def test_the_zones_keep_the_order_the_circular_lists_them_in(self):
        keys = list(HolidayZone.objects.values_list('key', flat=True))
        self.assertEqual(keys[0], 'north')
        self.assertEqual(keys[1], 'uttarakhand')

    def test_the_dashboard_gets_the_shape_its_zone_picker_expects(self):
        d = self.client.get('/api/noticeboard/holidays/', **self.auth(self.staff_token)).json()
        self.assertEqual(len(d), 10)
        self.assertEqual(set(d[0]), {'id', 'label', 'holidays'})
        self.assertEqual(set(d[0]['holidays'][0]), {'id', 'date', 'name', 'type'})

    def test_only_an_administrator_changes_the_holiday_list(self):
        zone = HolidayZone.objects.first()
        # A day the circular does not already list — Christmas is in there.
        payload = {'zone': zone.key, 'date': '2026-04-14', 'name': 'Ambedkar Jayanti',
                   'type': 'National'}

        self.assertEqual(self.client.post('/api/noticeboard/holidays/', payload,
                                          content_type='application/json').status_code, 401)
        self.assertEqual(self.client.post('/api/noticeboard/holidays/', payload,
                                          content_type='application/json',
                                          **self.auth(self.staff_token)).status_code, 403)
        self.assertEqual(Holiday.objects.count(), 130)

        r = self.client.post('/api/noticeboard/holidays/', payload,
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(Holiday.objects.count(), 131)

    def test_the_same_day_cannot_be_listed_twice_in_a_zone(self):
        """Pasting a list in twice is the normal way duplicates arrive."""
        zone = HolidayZone.objects.first()
        existing = zone.holidays.first()
        r = self.client.post('/api/noticeboard/holidays/',
                             {'zone': zone.key, 'date': existing.date.isoformat(),
                              'name': existing.name},
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(zone.holidays.count(), 13)

    def test_changing_a_holiday_is_recorded(self):
        h = Holiday.objects.first()
        self.client.patch(f'/api/noticeboard/holidays/{h.id}/', {'name': 'Republic Day (observed)'},
                          content_type='application/json', **self.auth(self.admin_token))
        h.refresh_from_db()
        self.assertEqual(h.name, 'Republic Day (observed)')
        self.assertTrue(ActivityLog.objects.filter(action='edited', object_type='holiday').exists())


class TheAnnouncementsCard(Base):

    def test_the_card_starts_empty_rather_than_showing_the_old_placeholders(self):
        """The two hard-coded rows were labelled sample data. Seeding them
        would have put fiction on the dashboard in the company's name."""
        self.assertEqual(Announcement.objects.count(), 0)
        self.assertEqual(self.client.get('/api/noticeboard/announcements/').json(), [])

    def test_a_notice_from_staff_waits_for_approval(self):
        r = self.client.post('/api/noticeboard/announcements/', self.notice(),
                             content_type='application/json', **self.auth(self.staff_token))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['moderationStatus'], ModerationStatus.PENDING)
        self.assertEqual(self.client.get('/api/noticeboard/announcements/').json(), [])

    def test_an_administrators_notice_goes_up_at_once(self):
        r = self.client.post('/api/noticeboard/announcements/', self.notice(),
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.json()['moderationStatus'], ModerationStatus.APPROVED)
        self.assertEqual(len(self.client.get('/api/noticeboard/announcements/').json()), 1)

    def test_an_announcement_appears_in_the_one_approval_queue(self):
        self.client.post('/api/noticeboard/announcements/', self.notice(),
                         content_type='application/json', **self.auth(self.staff_token))
        d = self.client.get('/api/accounts/portal/admin/moderation/',
                            **self.auth(self.admin_token)).json()
        self.assertEqual(d['pending_counts']['announcement'], 1)
        self.assertEqual(d['items'][0]['type'], 'announcement')
        self.assertEqual(d['items'][0]['submitted_by'], 'Staff Person')

    def test_a_notice_stops_showing_once_it_is_out_of_date(self):
        """A maintenance window announced for last month is noise, but the
        record of having announced it is not — so it hides, not deletes."""
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.client.post('/api/noticeboard/announcements/',
                         self.notice(expiresOn=yesterday),
                         content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(self.client.get('/api/noticeboard/announcements/').json(), [])
        self.assertEqual(Announcement.objects.count(), 1)

    def test_a_pinned_notice_sits_above_a_newer_one(self):
        self.client.post('/api/noticeboard/announcements/',
                         self.notice(title='Old but important', pinned=True, date='2026-01-01'),
                         content_type='application/json', **self.auth(self.admin_token))
        self.client.post('/api/noticeboard/announcements/', self.notice(title='Newer'),
                         content_type='application/json', **self.auth(self.admin_token))
        rows = self.client.get('/api/noticeboard/announcements/').json()
        self.assertEqual(rows[0]['title'], 'Old but important')

    def test_staff_cannot_pin_their_own_notice_to_the_top(self):
        self.client.post('/api/noticeboard/announcements/', self.notice(pinned=True),
                         content_type='application/json', **self.auth(self.staff_token))
        self.assertFalse(Announcement.objects.get().pinned)

    def test_only_an_administrator_edits_or_removes_a_notice(self):
        self.client.post('/api/noticeboard/announcements/', self.notice(),
                         content_type='application/json', **self.auth(self.admin_token))
        a = Announcement.objects.get()

        self.assertEqual(self.client.patch(f'/api/noticeboard/announcements/{a.id}/',
                                           {'title': 'Hijacked'}, content_type='application/json',
                                           **self.auth(self.staff_token)).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/noticeboard/announcements/{a.id}/',
                                            **self.auth(self.staff_token)).status_code, 403)

        self.client.patch(f'/api/noticeboard/announcements/{a.id}/', {'title': 'Corrected'},
                          content_type='application/json', **self.auth(self.admin_token))
        a.refresh_from_db()
        self.assertEqual(a.title, 'Corrected')
        self.assertTrue(ActivityLog.objects.filter(action='edited',
                                                   object_type='announcement').exists())

    def test_an_empty_notice_is_refused(self):
        r = self.client.post('/api/noticeboard/announcements/', {'title': 'Just a heading'},
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Announcement.objects.count(), 0)
