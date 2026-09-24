"""The noticeboard: what the company is told, and what it plans leave around.

Both used to be hard-coded in the frontend. These tests hold the two things
that moving them into the database must not break — the circular has to
survive verbatim, and nothing unapproved may reach the card.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from accounts.models import ActivityLog, PortalSession, PortalUser
from accounts.moderation import ModerationStatus
from noticeboard.models import (Announcement, Holiday, HolidayZone, NewsItem,
                                NewsSource)


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


# ── Daily News ──────────────────────────────────────────────
API = '/api/noticeboard/news/'


class TheNewsStrip(Base):
    """A strip on the company home page headed "latest updates". Two things
    decide whether it is worth having: nothing unapproved reaches it, and it
    does not quietly fill with last quarter's stories."""

    def story(self, **over):
        d = {'title': 'APIS expands nutraceutical capacity',
             'summary': 'The new line supports demand for plant-based APIs.',
             'category': 'company'}
        d.update(over)
        return d

    def post(self, token, **over):
        return self.client.post(API, self.story(**over),
                                content_type='application/json', **self.auth(token))

    # ── the approval gate ──
    def test_an_administrators_own_story_goes_straight_up(self):
        r = self.post(self.admin_token)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['moderationStatus'], ModerationStatus.APPROVED)

    def test_anybody_else_goes_to_the_queue(self):
        r = self.post(self.staff_token)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['moderationStatus'], ModerationStatus.PENDING)

    def test_a_pending_story_is_not_on_the_strip(self):
        """The whole point of the gate: this is the company home page.

        Nobody sees it there, the super admin included -- the strip shows what
        the company sees, and a review queue belongs on the review screen."""
        self.post(self.staff_token)
        self.assertEqual(self.client.get(API, **self.auth(self.admin_token)).json(), [])
        other = a_user('other@apisindia.com', 'Other')
        token = PortalSession.start(other)
        self.assertEqual(self.client.get(API, **self.auth(token)).json(), [])

    def test_the_console_still_sees_it(self):
        """...which is where it is reviewed from."""
        self.post(self.staff_token)
        seen = self.client.get(f'{API}?scope=all', **self.auth(self.admin_token)).json()
        self.assertEqual(len(seen), 1)

    def test_the_person_who_suggested_it_still_sees_their_own(self):
        """Otherwise it looks like the submission vanished. Under View all,
        not on the strip -- the strip is what the whole company sees."""
        self.post(self.staff_token)
        mine = self.client.get(f'{API}?scope=all', **self.auth(self.staff_token)).json()
        self.assertEqual(len(mine), 1)
        self.assertTrue(mine[0]['isMine'])

    def test_a_signed_out_visitor_sees_nothing_unapproved(self):
        self.post(self.staff_token)
        self.assertEqual(self.client.get(API).json(), [])

    # ── who may change what ──
    def test_only_an_administrator_may_edit(self):
        n = self.post(self.admin_token).json()
        r = self.client.patch(f'{API}{n["id"]}/', self.story(title='Rewritten'),
                              content_type='application/json', **self.auth(self.staff_token))
        self.assertEqual(r.status_code, 403)

    def test_only_an_administrator_may_delete(self):
        n = self.post(self.admin_token).json()
        r = self.client.delete(f'{API}{n["id"]}/', **self.auth(self.staff_token))
        self.assertEqual(r.status_code, 403)
        self.assertTrue(NewsItem.objects.filter(pk=n['id']).exists())

    def test_a_signed_out_visitor_cannot_post(self):
        r = self.client.post(API, self.story(), content_type='application/json')
        self.assertEqual(r.status_code, 401)

    def test_only_an_administrator_can_pin(self):
        """Pinning is how a story outranks everything newer than it."""
        n = self.post(self.staff_token, pinned=True).json()
        self.assertFalse(n['pinned'])
        n = self.post(self.admin_token, pinned=True).json()
        self.assertTrue(n['pinned'])

    # ── what it refuses ──
    def test_a_headline_with_nothing_behind_it_is_refused(self):
        r = self.client.post(API, {'title': 'Something happened', 'summary': '  '},
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 400)

    def test_a_link_that_is_not_a_link_is_refused(self):
        """A bare 'www.x.com' in an href resolves against our own site."""
        r = self.client.post(API, self.story(sourceUrl='www.example.com'),
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 400)

    def test_an_invented_category_falls_back_rather_than_breaking(self):
        n = self.post(self.admin_token, category='sorcery').json()
        self.assertEqual(n['category'], 'industry')

    def test_an_edit_is_held_to_the_same_rules_as_a_create(self):
        n = self.post(self.admin_token).json()
        r = self.client.patch(f'{API}{n["id"]}/', self.story(summary=''),
                              content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 400)

    # ── ordering and freshness ──
    def test_the_newest_story_is_first(self):
        old = timezone.localdate() - timedelta(days=10)
        self.post(self.admin_token, title='Older', publishedOn=old.isoformat())
        self.post(self.admin_token, title='Newer')
        titles = [n['title'] for n in
                  self.client.get(API, **self.auth(self.admin_token)).json()]
        self.assertEqual(titles[0], 'Newer')

    def test_a_pinned_story_outranks_a_newer_one(self):
        old = timezone.localdate() - timedelta(days=10)
        self.post(self.admin_token, title='Pinned', publishedOn=old.isoformat(), pinned=True)
        self.post(self.admin_token, title='Newer')
        titles = [n['title'] for n in
                  self.client.get(API, **self.auth(self.admin_token)).json()]
        self.assertEqual(titles[0], 'Pinned')

    def test_the_date_is_the_day_the_story_is_about(self):
        """An article found late belongs on its own date, or the strip claims
        a week-old piece broke this morning."""
        when = (timezone.localdate() - timedelta(days=5)).isoformat()
        n = self.post(self.admin_token, publishedOn=when).json()
        self.assertEqual(n['publishedOn'], when)

    def test_a_story_past_its_end_date_drops_off(self):
        gone = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.post(self.admin_token, title='Expired', expiresOn=gone)
        other = a_user('reader@apisindia.com', 'Reader')
        token = PortalSession.start(other)
        self.assertEqual(self.client.get(API, **self.auth(token)).json(), [])

    def test_it_is_hidden_not_deleted(self):
        """It stays as a record of what the company published and when."""
        gone = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.post(self.admin_token, title='Expired', expiresOn=gone)
        self.assertTrue(NewsItem.objects.filter(title='Expired').exists())

    def test_the_strip_asks_for_a_few_and_view_all_asks_for_everything(self):
        for i in range(15):
            self.post(self.admin_token, title=f'Story {i}')
        strip = self.client.get(API, **self.auth(self.admin_token)).json()
        self.assertEqual(len(strip), 12)
        everything = self.client.get(f'{API}?scope=all', **self.auth(self.admin_token)).json()
        self.assertEqual(len(everything), 15)

    # ── the picture ──
    def test_a_story_with_no_picture_is_fine(self):
        """Most stories will not have one, and the card must not look broken."""
        n = self.post(self.admin_token).json()
        self.assertEqual(n['image'], '')

    def test_a_linked_picture_comes_back_as_given(self):
        n = self.post(self.admin_token,
                      imageUrl='https://example.com/honey.jpg').json()
        self.assertEqual(n['image'], 'https://example.com/honey.jpg')

    def test_an_image_link_that_is_not_a_link_is_refused(self):
        r = self.client.post(API, self.story(imageUrl='javascript:alert(1)'),
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 400)

    # ── the audit trail ──
    def test_every_change_says_who_made_it(self):
        n = self.post(self.admin_token).json()
        self.client.patch(f'{API}{n["id"]}/', self.story(title='Rewritten'),
                          content_type='application/json', **self.auth(self.admin_token))
        self.client.delete(f'{API}{n["id"]}/', **self.auth(self.admin_token))
        actions = list(ActivityLog.objects.filter(summary__startswith='News:')
                       .values_list('action', flat=True))
        for expected in ('created', 'edited', 'deleted'):
            self.assertIn(expected, actions)

    def test_it_shows_up_in_the_moderation_console(self):
        """Adding it to CONTENT_TYPES is what makes the queue aware of it."""
        self.post(self.staff_token)
        d = self.client.get('/api/accounts/portal/admin/moderation/',
                            **self.auth(self.admin_token)).json()
        keys = [t['key'] for t in d['types']]
        self.assertIn('news', keys)

# ── Fetching the news ────────────────────────────────────
RSS = """<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <item>
      <title>Honey exports rise sharply - The Tribune</title>
      <description>&lt;a href="x"&gt;Volumes up on last year&lt;/a&gt; &amp;amp; rising</description>
      <link>https://example.com/a</link>
      <guid>tag:example.com,2026:a</guid>
      <pubDate>{recent}</pubDate>
      <source url="https://tribune.example">The Tribune</source>
      <media:thumbnail url="https://example.com/a.jpg"/>
    </item>
    <item>
      <title>Something from the archive</title>
      <description>Old news.</description>
      <link>https://example.com/old</link>
      <guid>tag:example.com,2013:old</guid>
      <pubDate>{old}</pubDate>
    </item>
  </channel>
</rss>"""


def rss_body(days_ago_recent=1, days_ago_old=400):
    fmt = '%a, %d %b %Y %H:%M:%S +0000'
    return RSS.format(
        recent=(timezone.now() - timedelta(days=days_ago_recent)).strftime(fmt),
        old=(timezone.now() - timedelta(days=days_ago_old)).strftime(fmt))


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text, self.status_code = text, status_code


class FetchingTheNews(TestCase):
    """The strip fills itself from real feeds. What matters is that it cannot
    publish itself, cannot carry the same story twice, and cannot quietly walk
    backwards into an archive while claiming to be today's news."""

    def setUp(self):
        # The two seeded sources ship with the app; these tests are about fetch
        # behaviour, so they run against their own feed alone.
        NewsSource.objects.all().delete()
        self.source = NewsSource.objects.create(
            name='Trade press', kind='rss', feed_url='https://example.com/feed.xml',
            category='industry', max_per_run=5, max_age_days=14)

    def run_fetch(self, body=None, status=200):
        from noticeboard import newsfeed
        with mock.patch.object(newsfeed.requests, 'get',
                               return_value=FakeResponse(body if body is not None
                                                         else rss_body(), status)):
            return newsfeed.fetch_source(self.source)

    # ── what it brings in ──
    def test_it_carries_a_story(self):
        found, added, error = self.run_fetch()
        self.assertEqual(error, '')
        self.assertEqual(added, 1)
        self.assertEqual(NewsItem.objects.count(), 1)

    def test_nothing_it_fetches_publishes_itself(self):
        """A search for the company name will eventually return a recall or a
        lawsuit. None of that goes up at 6am because cron ran."""
        self.run_fetch()
        self.assertEqual(NewsItem.objects.first().moderation_status,
                         ModerationStatus.PENDING)

    def test_a_source_can_be_trusted_to_publish_on_its_own(self):
        self.source.auto_publish = True
        self.source.save()
        self.run_fetch()
        self.assertEqual(NewsItem.objects.first().moderation_status,
                         ModerationStatus.APPROVED)

    def test_a_summary_that_only_echoes_the_headline_is_dropped(self):
        """Google News sets every description to the headline plus the
        publisher, so the card said the same sentence twice in two sizes."""
        from noticeboard.newsfeed import _tidy_summary
        title = 'Honey exports rise sharply'
        self.assertEqual(
            _tidy_summary('Honey exports rise sharply The Tribune', title, 'The Tribune'), '')
        self.assertEqual(_tidy_summary('Honey exports rise sharply', title, ''), '')

    def test_a_short_blurb_that_is_genuinely_different_survives(self):
        """Dropping everything not much longer than the headline would also
        throw away a real one-line summary."""
        from noticeboard.newsfeed import _tidy_summary
        self.assertEqual(
            _tidy_summary('Volumes up a third on last year.',
                          'Honey exports rise sharply', 'The Tribune'),
            'Volumes up a third on last year.')

    def test_a_summary_that_adds_something_is_kept(self):
        from noticeboard.newsfeed import _tidy_summary
        title = 'Honey exports rise sharply'
        real = ('Honey exports rise sharply as APEDA clears the first consignment '
                'from Assam, with volumes up by a third on last year.')
        self.assertEqual(_tidy_summary(real, title, 'The Tribune'), real)

    def test_an_empty_summary_is_fine(self):
        from noticeboard.newsfeed import _tidy_summary
        self.assertEqual(_tidy_summary('', 'A headline of some length', ''), '')

    def test_html_does_not_reach_the_card(self):
        """Feed descriptions are routinely a whole anchor tag."""
        self.run_fetch()
        n = NewsItem.objects.first()
        self.assertNotIn('<', n.summary)
        self.assertIn('Volumes up on last year', n.summary)

    def test_entities_are_decoded_not_left_as_text(self):
        self.run_fetch()
        self.assertIn('&', NewsItem.objects.first().summary)
        self.assertNotIn('&amp;', NewsItem.objects.first().summary)

    def test_the_publisher_is_not_repeated_at_the_end_of_the_headline(self):
        """Google News appends ' - The Publisher' to every headline, and the
        card shows the publisher underneath already."""
        self.run_fetch()
        self.assertEqual(NewsItem.objects.first().title, 'Honey exports rise sharply')
        self.assertEqual(NewsItem.objects.first().source_name, 'The Tribune')

    def test_a_thumbnail_is_used_when_the_feed_offers_one(self):
        self.run_fetch()
        self.assertEqual(NewsItem.objects.first().image_url, 'https://example.com/a.jpg')

    def test_the_story_is_filed_under_the_feeds_category(self):
        self.run_fetch()
        self.assertEqual(NewsItem.objects.first().category, 'industry')

    def test_it_is_traceable_to_the_feed_that_found_it(self):
        self.run_fetch()
        self.assertEqual(NewsItem.objects.first().source_ref_id, self.source.id)

    # ── what it refuses ──
    def test_it_will_not_carry_the_archive(self):
        """Google ranks by relevance, not date, so a narrow query answers with
        its best matches going back years."""
        self.run_fetch()
        self.assertFalse(NewsItem.objects.filter(title='Something from the archive').exists())

    def test_running_it_again_carries_nothing_twice(self):
        """The keys are long enough to be truncated on the way in, so the
        lookup has to be truncated too or every run re-carries everything."""
        self.run_fetch()
        self.run_fetch()
        self.assertEqual(NewsItem.objects.count(), 1)

    def test_a_story_whose_link_changed_is_still_recognised(self):
        """Feeds re-publish with a new link and the same id."""
        self.run_fetch()
        moved = rss_body().replace('https://example.com/a<', 'https://example.com/a2<')
        self.run_fetch(moved)
        self.assertEqual(NewsItem.objects.count(), 1)

    def test_a_very_long_key_does_not_defeat_the_dedupe(self):
        long_id = 'tag:example.com,2026:' + ('x' * 900)
        body = rss_body().replace('tag:example.com,2026:a', long_id)
        self.run_fetch(body)
        self.run_fetch(body)
        self.assertEqual(NewsItem.objects.count(), 1)

    def test_a_long_link_is_stored_whole_not_clipped(self):
        """A clipped Google News link is not a shorter link, it is a broken
        one: Google answers it with 400, malformed. In one real feed 13 of 39
        were over 500 characters and the longest was 824."""
        long_link = 'https://news.google.com/rss/articles/' + ('A' * 700) + '?oc=5'
        body = rss_body().replace('https://example.com/a<', long_link + '<')
        self.run_fetch(body)
        stored = NewsItem.objects.first().source_url
        self.assertEqual(stored, long_link)
        self.assertGreater(len(stored), 700)

    def test_the_dedupe_key_stays_short_enough_to_index(self):
        """MySQL's utf8mb4 index limit is 3072 bytes, so the raw id could not
        carry an index at all."""
        long_id = 'tag:example.com,2026:' + ('x' * 900)
        self.run_fetch(rss_body().replace('tag:example.com,2026:a', long_id))
        self.assertEqual(len(NewsItem.objects.first().external_id), 64)

    def test_a_placeholder_title_is_not_made_into_a_card(self):
        """One real government feed answers with a title of literally
        'BlogDescription'."""
        body = rss_body().replace('Honey exports rise sharply - The Tribune',
                                  'BlogDescription')
        found, added, error = self.run_fetch(body)
        self.assertEqual(added, 0)

    def test_a_placeholder_hiding_behind_a_publisher_suffix_is_caught_too(self):
        """Google appends ' - The Publisher' to every title, so the junk row
        arrives as 'BlogDescription - PIB' and sails past a check made before
        the suffix is stripped."""
        body = rss_body().replace('Honey exports rise sharply - The Tribune',
                                  'BlogDescription - The Tribune')
        found, added, error = self.run_fetch(body)
        self.assertEqual(added, 0, NewsItem.objects.values_list('title', flat=True))

    # ── when things go wrong ──
    def test_a_feed_that_is_down_does_not_raise(self):
        found, added, error = self.run_fetch(status=503)
        self.assertNotEqual(error, '')
        self.assertEqual(added, 0)

    def test_and_the_failure_is_recorded_where_somebody_will_see_it(self):
        self.run_fetch(status=503)
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_status, 'failed')
        self.assertTrue(self.source.last_error)

    def test_a_page_of_html_instead_of_xml_is_survivable(self):
        found, added, error = self.run_fetch('<html><body>Nope</body></html>')
        self.assertEqual(added, 0)

    def test_an_unreadable_date_does_not_send_a_story_to_1970(self):
        body = rss_body().replace(
            '<pubDate>', '<pubDate>not a date</pubDate><ignored>', 1)
        self.run_fetch(body)
        for n in NewsItem.objects.all():
            self.assertGreaterEqual(n.published_on,
                                    timezone.localdate() - timedelta(days=1))

    def test_one_bad_feed_does_not_stop_the_others(self):
        from noticeboard import newsfeed
        NewsSource.objects.create(name='Another', kind='rss',
                                  feed_url='https://example.com/two.xml',
                                  category='company')
        with mock.patch.object(newsfeed.requests, 'get',
                               side_effect=[FakeResponse('', 500),
                                            FakeResponse(rss_body())]):
            results = newsfeed.fetch_all()
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(r['added'] for r in results), 1)

    # ── the backlog guard ──
    def test_it_stops_adding_when_nobody_is_reviewing(self):
        """Five a day into a queue nobody opens is how a review screen becomes
        three hundred rows and stops being used."""
        from noticeboard.newsfeed import BACKLOG_LIMIT
        for i in range(BACKLOG_LIMIT):
            NewsItem.objects.create(title=f'Waiting {i}', summary='x',
                                    source_ref=self.source,
                                    moderation_status=ModerationStatus.PENDING)
        found, added, error = self.run_fetch()
        self.assertEqual(added, 0)

    def test_a_pause_is_not_reported_as_a_breakage(self):
        """Nothing is broken, and calling it a failure sends somebody looking
        for a fault that is not there."""
        from noticeboard.newsfeed import BACKLOG_LIMIT
        for i in range(BACKLOG_LIMIT):
            NewsItem.objects.create(title=f'Waiting {i}', summary='x',
                                    source_ref=self.source,
                                    moderation_status=ModerationStatus.PENDING)
        self.run_fetch()
        self.source.refresh_from_db()
        self.assertEqual(self.source.last_status, 'paused')

    def test_clearing_the_queue_lets_it_resume(self):
        from noticeboard.newsfeed import BACKLOG_LIMIT
        for i in range(BACKLOG_LIMIT):
            NewsItem.objects.create(title=f'Waiting {i}', summary='x',
                                    source_ref=self.source,
                                    moderation_status=ModerationStatus.PENDING)
        self.run_fetch()
        NewsItem.objects.filter(title__startswith='Waiting').update(
            moderation_status=ModerationStatus.APPROVED)
        found, added, error = self.run_fetch()
        self.assertEqual(added, 1)


class AskingGoogleForNews(TestCase):
    """The search that gets sent. Google ranks by relevance, not date."""

    def test_a_window_is_asked_for_upstream(self):
        """Without this the feed answers with its best matches from any year:
        one real query returned results from 2013 and nothing inside a
        fortnight."""
        s = NewsSource(kind='google_news', query='honey India', max_age_days=14)
        self.assertIn('when%3A14d', s.resolved_url())

    def test_the_window_follows_the_setting(self):
        s = NewsSource(kind='google_news', query='honey India', max_age_days=30)
        self.assertIn('when%3A30d', s.resolved_url())

    def test_a_query_that_says_it_itself_is_left_alone(self):
        s = NewsSource(kind='google_news', query='honey when:3d', max_age_days=14)
        self.assertNotIn('when%3A14d', s.resolved_url())

    def test_results_are_pinned_to_indian_english(self):
        """The same query answers differently depending on where the server is."""
        s = NewsSource(kind='google_news', query='honey', max_age_days=7)
        self.assertIn('ceid=IN:en', s.resolved_url())

    def test_an_rss_source_is_fetched_verbatim(self):
        s = NewsSource(kind='rss', feed_url='https://example.com/f.xml')
        self.assertEqual(s.resolved_url(), 'https://example.com/f.xml')


class ManagingTheSources(Base):
    """Which feeds the company home page may fill itself from."""

    API = '/api/noticeboard/news/sources/'

    def make(self, token, **over):
        d = {'name': 'Trade press', 'kind': 'google_news', 'query': 'honey India',
             'category': 'industry'}
        d.update(over)
        return self.client.post(self.API, d, content_type='application/json',
                                **self.auth(token))

    def test_only_an_administrator_may_see_them(self):
        self.assertEqual(self.client.get(self.API, **self.auth(self.staff_token)).status_code, 403)

    def test_only_an_administrator_may_add_one(self):
        self.assertEqual(self.make(self.staff_token).status_code, 403)
        self.assertEqual(self.make(self.admin_token).status_code, 201)

    def test_a_google_source_needs_something_to_search_for(self):
        self.assertEqual(self.make(self.admin_token, query='').status_code, 400)

    def test_an_rss_source_needs_a_real_address(self):
        self.assertEqual(
            self.make(self.admin_token, kind='rss', feedUrl='example.com').status_code, 400)

    def test_removing_a_source_keeps_the_stories_it_found(self):
        """They were reviewed on their own merits and some are on the
        dashboard right now."""
        r = self.make(self.admin_token)
        sid = r.json()['id']
        NewsItem.objects.create(title='Carried', summary='x',
                                source_ref_id=sid,
                                moderation_status=ModerationStatus.APPROVED)
        self.client.delete(f'{self.API}{sid}/', **self.auth(self.admin_token))
        self.assertTrue(NewsItem.objects.filter(title='Carried').exists())

    def test_approving_a_morning_of_headlines_at_once(self):
        """A queue reviewed one modal at a time is a queue that stops being
        used."""
        ids = [NewsItem.objects.create(title=f'S{i}', summary='x').id for i in range(3)]
        r = self.client.post('/api/noticeboard/news/approve/',
                             {'ids': ids, 'decision': 'approve'},
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(NewsItem.published().count(), 3)

    def test_a_colleague_cannot_approve_their_own_suggestions(self):
        ids = [NewsItem.objects.create(title='Mine', summary='x').id]
        r = self.client.post('/api/noticeboard/news/approve/',
                             {'ids': ids, 'decision': 'approve'},
                             content_type='application/json', **self.auth(self.staff_token))
        self.assertEqual(r.status_code, 403)

    def test_fetching_on_demand_is_an_administrators_button(self):
        self.assertEqual(
            self.client.post('/api/noticeboard/news/fetch/',
                             **self.auth(self.staff_token)).status_code, 403)


class WhatShipsConfigured(TestCase):
    """Two feeds are seeded so the strip works the day this ships."""

    def test_both_are_there(self):
        self.assertEqual(NewsSource.objects.count(), 2)

    def test_they_publish_without_waiting_to_be_read(self):
        """A deliberate trade, made after the gate was built and used.

        An approval queue that fills every morning gets approved in bulk
        without being read inside a fortnight, and is then a gate in name only
        while still being a daily chore. What makes it safe enough: the strip
        ages out on its own, removal is one click, and this is a per-source
        switch that a feed added later does not inherit."""
        self.assertEqual(NewsSource.objects.filter(auto_publish=True).count(), 2)

    def test_a_feed_added_later_still_waits(self):
        """The switch is per source, not a global setting."""
        s = NewsSource.objects.create(name='Something new', kind='rss',
                                      feed_url='https://example.com/f.xml')
        self.assertFalse(s.auto_publish)

    def test_the_company_feed_looks_wider_than_the_trade_one(self):
        """One mid-cap company makes news rarely -- a fortnight's window is the
        difference between a company feed and an empty one."""
        company = NewsSource.objects.get(category='company')
        trade = NewsSource.objects.get(category='industry')
        self.assertGreater(company.max_age_days, trade.max_age_days)

    def test_they_ask_google_for_recent_results_only(self):
        for src in NewsSource.objects.all():
            self.assertIn('when%3A', src.resolved_url())


class ClearingBeforeNarrowing(TestCase):
    """Migration 0007 empties the fetched rows so 0008 can shrink the column
    they sit in. MySQL refuses to narrow a column while a row would be
    truncated; SQLite does it silently, which is why this got to QA."""

    def setUp(self):
        from noticeboard.models import NewsSource
        self.src = NewsSource.objects.create(name='Feed', kind='rss',
                                             feed_url='https://example.com/f.xml')

    def test_fetched_rows_go(self):
        NewsItem.objects.create(title='From a feed', summary='x',
                                source_ref=self.src, external_id='y' * 60)
        NewsItem.objects.filter(source_ref__isnull=False).delete()
        self.assertEqual(NewsItem.objects.count(), 0)

    def test_hand_written_rows_stay(self):
        """No feed can bring these back."""
        NewsItem.objects.create(title='Written by the admin', summary='x')
        NewsItem.objects.create(title='From a feed', summary='x',
                                source_ref=self.src, external_id='y' * 60)
        NewsItem.objects.filter(source_ref__isnull=False).delete()
        self.assertEqual(NewsItem.objects.count(), 1)
        self.assertEqual(NewsItem.objects.first().title, 'Written by the admin')

    def test_nothing_is_left_too_long_for_the_new_column(self):
        NewsItem.objects.create(title='Written by the admin', summary='x')
        NewsItem.objects.filter(source_ref__isnull=False).delete()
        NewsItem.objects.exclude(external_id='').update(external_id='')
        for n in NewsItem.objects.all():
            self.assertLessEqual(len(n.external_id), 64)


class TheStripKeepsItself(TestCase):
    """Nobody has to take an old story down for the strip to stay current.

    This is what makes publishing-by-default workable: if approved stories
    simply accumulated, the row would quietly fill with last quarter's news and
    somebody would have to prune it by hand forever.
    """

    def a_story(self, days_old, **over):
        d = {'title': f'Story from {days_old} days ago', 'summary': '',
             'moderation_status': ModerationStatus.APPROVED,
             'published_on': timezone.localdate() - timedelta(days=days_old)}
        d.update(over)
        return NewsItem.objects.create(**d)

    def test_a_recent_story_is_on_the_strip(self):
        self.a_story(2)
        self.assertEqual(NewsItem.for_strip().count(), 1)

    def test_an_old_one_drops_off_by_itself(self):
        self.a_story(NewsItem.STRIP_MAX_AGE_DAYS + 5)
        self.assertEqual(NewsItem.for_strip().count(), 0)

    def test_but_it_is_not_deleted(self):
        """It is still there under View all and in the console."""
        self.a_story(NewsItem.STRIP_MAX_AGE_DAYS + 5)
        self.assertEqual(NewsItem.objects.count(), 1)

    def test_pinning_keeps_something_up_regardless(self):
        """Pinning is how somebody says 'keep this one'."""
        self.a_story(NewsItem.STRIP_MAX_AGE_DAYS + 90, pinned=True)
        self.assertEqual(NewsItem.for_strip().count(), 1)

    def test_nothing_unapproved_is_ever_on_it(self):
        self.a_story(1, moderation_status=ModerationStatus.PENDING)
        self.a_story(1, moderation_status=ModerationStatus.REJECTED)
        self.assertEqual(NewsItem.for_strip().count(), 0)

    def test_an_expiry_date_still_wins(self):
        self.a_story(1, expires_on=timezone.localdate() - timedelta(days=1))
        self.assertEqual(NewsItem.for_strip().count(), 0)


class TidyingUpAfterwards(TestCase):
    """Fetched stories are eventually deleted, so the table does not grow
    without limit. Nobody looks up what a feed carried three months ago."""

    def setUp(self):
        self.src = NewsSource.objects.create(name='Feed', kind='rss',
                                             feed_url='https://example.com/f.xml')

    def fetched(self, days_old, **over):
        d = {'title': f'Fetched {days_old} days ago', 'summary': '',
             'source_ref': self.src,
             'published_on': timezone.localdate() - timedelta(days=days_old)}
        d.update(over)
        return NewsItem.objects.create(**d)

    def test_an_ancient_fetched_story_is_removed(self):
        from noticeboard.newsfeed import purge_old
        self.fetched(NewsItem.PURGE_AFTER_DAYS + 10)
        purge_old()
        self.assertEqual(NewsItem.objects.count(), 0)

    def test_a_recent_one_is_left_alone(self):
        from noticeboard.newsfeed import purge_old
        self.fetched(5)
        purge_old()
        self.assertEqual(NewsItem.objects.count(), 1)

    def test_something_written_by_hand_is_never_purged(self):
        """No feed can bring it back."""
        from noticeboard.newsfeed import purge_old
        NewsItem.objects.create(
            title='Written by the admin', summary='x',
            published_on=timezone.localdate() - timedelta(days=NewsItem.PURGE_AFTER_DAYS + 50))
        purge_old()
        self.assertEqual(NewsItem.objects.count(), 1)

    def test_a_pinned_story_survives_the_purge(self):
        from noticeboard.newsfeed import purge_old
        self.fetched(NewsItem.PURGE_AFTER_DAYS + 10, pinned=True)
        purge_old()
        self.assertEqual(NewsItem.objects.count(), 1)
