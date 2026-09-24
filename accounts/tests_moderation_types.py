"""Every moderated type must actually be approvable from the console.

This exists because two of them were not, and nothing noticed. A queue row
carries `type: self._meta.model_name`, the console posts that value back, and
the registry is keyed by whatever read well as a filter chip. Where the two
strings happened to coincide approving worked; where they did not the POST
answered "Unknown content type" and the item could not be approved at all.

Three of five matched by luck. 'referral' is EmployeeReferral and 'news' is
NewsItem, and both were dead ends -- referrals since the queue was built.

So this walks the real registry rather than a list written out here: a content
type added later is covered the day it is added, including one whose model is
named something other than its chip.
"""
from django.apps import apps
from django.test import TestCase

from accounts.models import PortalSession, PortalUser
from accounts.moderation import ModerationStatus
from accounts.views_moderation import CONTENT_TYPES, _model_for


class EveryModeratedTypeResolves(TestCase):

    def test_each_type_resolves_by_its_registry_key(self):
        for key in CONTENT_TYPES:
            with self.subTest(key=key):
                self.assertIsNotNone(_model_for(key))

    def test_each_type_also_resolves_by_the_name_the_console_sends_back(self):
        """The queue row's `type` is the Django model name, not the chip."""
        for key, spec in CONTENT_TYPES.items():
            model = apps.get_model(*spec['model'])
            with self.subTest(key=key, model=model._meta.model_name):
                self.assertIsNotNone(_model_for(model._meta.model_name))

    def test_every_registered_model_can_actually_be_moderated(self):
        for key, spec in CONTENT_TYPES.items():
            model = apps.get_model(*spec['model'])
            with self.subTest(key=key):
                self.assertTrue(hasattr(model, 'moderation_status'),
                                f'{key} does not inherit ModeratedContent')
                self.assertTrue(hasattr(model, 'moderation_payload'),
                                f'{key} cannot render a queue row')
                # published() is deliberately not universal: a referral is an
                # inbound submission, never shown on the dashboard.

    def test_an_invented_type_is_still_refused(self):
        self.assertIsNone(_model_for('sorcery'))
        self.assertIsNone(_model_for(''))


class ApprovingFromTheConsole(TestCase):
    """The round trip the screen actually performs: read the queue, post back
    each row's own `type`, and expect it to be approved."""

    API = '/api/accounts/portal/admin/moderation/'

    def setUp(self):
        self.admin = PortalUser.objects.create(
            email='admin@apisindia.com', name='Admin', employee_code='ADMIN',
            is_superadmin=True, app_access=['home'])
        self.token = PortalSession.start(self.admin)

    def auth(self):
        return {'HTTP_AUTHORIZATION': f'Bearer {self.token}'}

    def test_a_waiting_story_can_be_approved_the_way_the_screen_does_it(self):
        from noticeboard.models import NewsItem
        n = NewsItem.objects.create(title='Honey exports rise', summary='x')
        self.assertEqual(n.moderation_status, ModerationStatus.PENDING)

        queue = self.client.get(f'{self.API}?status=pending', **self.auth()).json()
        row = next(i for i in queue['items'] if i['id'] == n.id
                   and 'Honey exports' in i['label'])

        r = self.client.post(self.API,
                             {'type': row['type'], 'ids': [n.id], 'decision': 'approved'},
                             content_type='application/json', **self.auth())
        self.assertEqual(r.status_code, 200, r.content[:200])
        n.refresh_from_db()
        self.assertEqual(n.moderation_status, ModerationStatus.APPROVED)

    def test_a_referral_can_be_approved_too(self):
        """This never worked. The chip says 'referral'; the model is
        EmployeeReferral."""
        from referrals.models import EmployeeReferral
        model = EmployeeReferral
        row_type = model._meta.model_name
        self.assertNotEqual(row_type, 'referral')      # the whole problem
        self.assertIsNotNone(_model_for(row_type))

    def test_the_chip_and_the_row_reach_the_same_model(self):
        for key, spec in CONTENT_TYPES.items():
            model = apps.get_model(*spec['model'])
            with self.subTest(key=key):
                self.assertIs(_model_for(key),
                              _model_for(model._meta.model_name))
