"""The approval gate and the audit trail.

These are written against the behaviour that was wrong before: that anyone at
all could publish to the company dashboard, and that nothing recorded who did.
Each test names the guarantee rather than the function it calls.
"""
import io
import shutil
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse  # noqa: F401  (paths are literal here)

from accounts.models import ActivityLog, PortalSession, PortalUser
from accounts.moderation import ModerationStatus
from vacancies.models import Vacancy
from wall.models import WallPhoto


# Uploads are real files. Without this the suite writes into the project's
# own media/ folder and leaves the photos behind after it passes.
MEDIA = tempfile.mkdtemp(prefix='apis-test-media-')


class UploadsToATempFolder(TestCase):
    """Base for anything that stores a file."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)


def a_user(email, name='Someone', superadmin=False):
    return PortalUser.objects.create(
        email=email, name=name, employee_code=email.split('@')[0].upper(),
        is_superadmin=superadmin, app_access=['home', 'apis-wall'])


def a_png():
    """Smallest thing Pillow will accept as a real image."""
    from PIL import Image
    from django.core.files.uploadedfile import SimpleUploadedFile
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), (200, 150, 40)).save(buf, format='PNG')
    return SimpleUploadedFile('team.png', buf.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA)
class VacancyApproval(TestCase):

    def setUp(self):
        Vacancy.objects.all().delete()          # drop the seeded hiring plan
        self.staff = a_user('staff@apisindia.com', 'Staff Person')
        self.admin = a_user('admin@apisindia.com', 'Admin Person', superadmin=True)
        self.staff_token = PortalSession.start(self.staff)
        self.admin_token = PortalSession.start(self.admin)

    def auth(self, token):
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def payload(self, title='Area Manager'):
        return {'title': title, 'function': 'Sales', 'department': 'Sales',
                'location': 'Delhi', 'state': 'Delhi'}

    def test_a_stranger_cannot_put_anything_on_the_dashboard(self):
        r = self.client.post('/api/vacancies/', self.payload(),
                             content_type='application/json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(Vacancy.objects.count(), 0)

    def test_a_submission_waits_for_approval_before_anyone_sees_it(self):
        r = self.client.post('/api/vacancies/', self.payload(),
                             content_type='application/json', **self.auth(self.staff_token))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['moderationStatus'], ModerationStatus.PENDING)

        # The public list — what a signed-out visitor would get — is empty.
        self.assertEqual(self.client.get('/api/vacancies/').json(), [])

    def test_the_person_who_submitted_it_can_still_see_their_own(self):
        """Otherwise they assume it failed and submit it again."""
        self.client.post('/api/vacancies/', self.payload(),
                         content_type='application/json', **self.auth(self.staff_token))
        mine = self.client.get('/api/vacancies/', **self.auth(self.staff_token)).json()
        self.assertEqual(len(mine), 1)
        self.assertTrue(mine[0]['isMine'])

        # ...but a different employee does not.
        other = a_user('other@apisindia.com')
        other_token = PortalSession.start(other)
        self.assertEqual(self.client.get('/api/vacancies/', **self.auth(other_token)).json(), [])

    def test_approving_it_puts_it_on_the_dashboard(self):
        self.client.post('/api/vacancies/', self.payload(),
                         content_type='application/json', **self.auth(self.staff_token))
        v = Vacancy.objects.get()

        r = self.client.post('/api/accounts/portal/admin/moderation/',
                             {'type': 'vacancy', 'ids': [v.id], 'decision': 'approved'},
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.client.get('/api/vacancies/').json()), 1)

    def test_rejecting_it_keeps_it_off_and_keeps_the_reason(self):
        self.client.post('/api/vacancies/', self.payload(),
                         content_type='application/json', **self.auth(self.staff_token))
        v = Vacancy.objects.get()
        self.client.post('/api/accounts/portal/admin/moderation/',
                         {'type': 'vacancy', 'id': v.id, 'decision': 'rejected',
                          'note': 'Position not signed off yet.'},
                         content_type='application/json', **self.auth(self.admin_token))

        v.refresh_from_db()
        self.assertEqual(v.moderation_status, ModerationStatus.REJECTED)
        self.assertEqual(v.review_note, 'Position not signed off yet.')
        self.assertEqual(self.client.get('/api/vacancies/').json(), [])

    def test_an_administrators_own_addition_goes_up_immediately(self):
        r = self.client.post('/api/vacancies/', self.payload('Head of Sales'),
                             content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.json()['moderationStatus'], ModerationStatus.APPROVED)
        self.assertEqual(len(self.client.get('/api/vacancies/').json()), 1)

    def test_only_an_administrator_can_close_a_vacancy(self):
        """Closing one removes it from the dashboard for the whole company."""
        self.client.post('/api/vacancies/', self.payload(),
                         content_type='application/json', **self.auth(self.admin_token))
        v = Vacancy.objects.get()

        for headers, expected in ((None, 401), (self.auth(self.staff_token), 403)):
            r = self.client.patch(f'/api/vacancies/{v.id}/status/', {'status': 'Closed'},
                                  content_type='application/json', **(headers or {}))
            self.assertEqual(r.status_code, expected)

        v.refresh_from_db()
        self.assertEqual(v.status, 'Active')

    def test_an_administrator_can_edit_and_delete_anything(self):
        self.client.post('/api/vacancies/', self.payload(),
                         content_type='application/json', **self.auth(self.staff_token))
        v = Vacancy.objects.get()

        r = self.client.patch(f'/api/vacancies/{v.id}/', {'title': 'Regional Manager'},
                              content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        v.refresh_from_db()
        self.assertEqual(v.title, 'Regional Manager')

        r = self.client.delete(f'/api/vacancies/{v.id}/', **self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Vacancy.objects.count(), 0)

    def test_an_ordinary_employee_cannot_edit_or_delete(self):
        self.client.post('/api/vacancies/', self.payload(),
                         content_type='application/json', **self.auth(self.staff_token))
        v = Vacancy.objects.get()
        self.assertEqual(self.client.patch(f'/api/vacancies/{v.id}/', {'title': 'x'},
                                           content_type='application/json',
                                           **self.auth(self.staff_token)).status_code, 403)
        self.assertEqual(self.client.delete(f'/api/vacancies/{v.id}/',
                                            **self.auth(self.staff_token)).status_code, 403)
        self.assertEqual(Vacancy.objects.count(), 1)


@override_settings(MEDIA_ROOT=MEDIA)
class WallApproval(UploadsToATempFolder):

    def setUp(self):
        self.staff = a_user('shooter@apisindia.com', 'Photo Person')
        self.admin = a_user('boss@apisindia.com', 'Admin Person', superadmin=True)
        self.staff_token = PortalSession.start(self.staff)
        self.admin_token = PortalSession.start(self.admin)

    def auth(self, token):
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def test_a_photo_does_not_reach_the_wall_on_its_own(self):
        r = self.client.post('/api/wall/photos/',
                             {'title': 'Diwali lunch', 'category': 'Celebrations',
                              'image': a_png()}, **self.auth(self.staff_token))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['moderationStatus'], ModerationStatus.PENDING)
        self.assertEqual(self.client.get('/api/wall/photos/').json(), [])

    def test_the_wall_records_who_uploaded_each_photo(self):
        self.client.post('/api/wall/photos/', {'title': 'Team day', 'image': a_png()},
                         **self.auth(self.staff_token))
        p = WallPhoto.objects.get()
        self.assertEqual(p.submitted_by, self.staff)
        self.assertEqual(p.submitted_by_name, 'Photo Person')
        self.assertEqual(p.submitted_by_email, 'shooter@apisindia.com')

    def test_an_unsigned_visitor_cannot_upload(self):
        r = self.client.post('/api/wall/photos/', {'title': 'x', 'image': a_png()})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(WallPhoto.objects.count(), 0)

    def test_a_file_that_is_not_an_image_is_refused(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        bad = SimpleUploadedFile('notes.png', b'this is not a png at all',
                                 content_type='image/png')
        r = self.client.post('/api/wall/photos/', {'title': 'x', 'image': bad},
                             **self.auth(self.staff_token))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(WallPhoto.objects.count(), 0)

    def test_approving_a_photo_puts_it_on_the_wall(self):
        self.client.post('/api/wall/photos/', {'title': 'Cake', 'image': a_png()},
                         **self.auth(self.staff_token))
        p = WallPhoto.objects.get()
        self.client.post('/api/accounts/portal/admin/moderation/',
                         {'type': 'wallphoto', 'ids': [p.id], 'decision': 'approved'},
                         content_type='application/json', **self.auth(self.admin_token))
        self.assertEqual(len(self.client.get('/api/wall/photos/').json()), 1)

    def test_removing_a_photo_takes_the_file_with_it(self):
        """A rejected photo left on disk is still served to anyone with the URL."""
        self.client.post('/api/wall/photos/', {'title': 'Oops', 'image': a_png()},
                         **self.auth(self.staff_token))
        p = WallPhoto.objects.get()
        storage, name = p.image.storage, p.image.name
        self.assertTrue(storage.exists(name))

        self.client.delete(f'/api/wall/photos/{p.id}/', **self.auth(self.admin_token))
        self.assertFalse(storage.exists(name))


@override_settings(MEDIA_ROOT=MEDIA)
class TheQueueAndTheLog(UploadsToATempFolder):

    def setUp(self):
        Vacancy.objects.all().delete()
        self.staff = a_user('one@apisindia.com', 'Staff One')
        self.admin = a_user('two@apisindia.com', 'Admin Two', superadmin=True)
        self.staff_token = PortalSession.start(self.staff)
        self.admin_token = PortalSession.start(self.admin)

    def auth(self, token):
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def test_the_queue_is_for_administrators_only(self):
        self.assertEqual(self.client.get(
            '/api/accounts/portal/admin/moderation/').status_code, 401)
        self.assertEqual(self.client.get(
            '/api/accounts/portal/admin/moderation/',
            **self.auth(self.staff_token)).status_code, 403)

    def test_the_queue_gathers_every_kind_of_content_in_one_place(self):
        self.client.post('/api/vacancies/',
                         {'title': 'Chemist', 'function': 'QA', 'department': 'QA',
                          'location': 'Roorkee', 'state': 'Uttarakhand'},
                         content_type='application/json', **self.auth(self.staff_token))
        self.client.post('/api/wall/photos/', {'title': 'Plant visit', 'image': a_png()},
                         **self.auth(self.staff_token))

        d = self.client.get('/api/accounts/portal/admin/moderation/',
                            **self.auth(self.admin_token)).json()
        self.assertEqual(d['pending_total'], 2)
        self.assertEqual({i['type'] for i in d['items']}, {'vacancy', 'wallphoto'})

    def test_every_item_in_the_queue_names_who_submitted_it(self):
        self.client.post('/api/wall/photos/', {'title': 'Holi', 'image': a_png()},
                         **self.auth(self.staff_token))
        d = self.client.get('/api/accounts/portal/admin/moderation/',
                            **self.auth(self.admin_token)).json()
        self.assertEqual(d['items'][0]['submitted_by'], 'Staff One')
        self.assertEqual(d['items'][0]['submitted_by_email'], 'one@apisindia.com')

    def test_the_log_records_who_created_and_who_approved(self):
        self.client.post('/api/vacancies/',
                         {'title': 'Chemist', 'function': 'QA', 'department': 'QA',
                          'location': 'Roorkee', 'state': 'Uttarakhand'},
                         content_type='application/json', **self.auth(self.staff_token))
        v = Vacancy.objects.get()
        self.client.post('/api/accounts/portal/admin/moderation/',
                         {'type': 'vacancy', 'ids': [v.id], 'decision': 'approved'},
                         content_type='application/json', **self.auth(self.admin_token))

        d = self.client.get('/api/accounts/portal/admin/activity/',
                            **self.auth(self.admin_token)).json()
        pairs = {(r['action'], r['actor']) for r in d['items']}
        self.assertIn(('created', 'Staff One'), pairs)
        self.assertIn(('approved', 'Admin Two'), pairs)

    def test_the_log_keeps_the_name_of_someone_later_removed(self):
        """A leaver's account can be deleted; what they did must still read."""
        self.client.post('/api/wall/photos/', {'title': 'Farewell', 'image': a_png()},
                         **self.auth(self.staff_token))
        self.staff.delete()

        entry = ActivityLog.objects.filter(action='created').first()
        self.assertIsNone(entry.actor)
        self.assertEqual(entry.actor_name, 'Staff One')

    def test_a_rejection_reason_reaches_the_log(self):
        self.client.post('/api/vacancies/',
                         {'title': 'Driver', 'function': 'Admin', 'department': 'Admin',
                          'location': 'Delhi', 'state': 'Delhi'},
                         content_type='application/json', **self.auth(self.staff_token))
        v = Vacancy.objects.get()
        self.client.post('/api/accounts/portal/admin/moderation/',
                         {'type': 'vacancy', 'id': v.id, 'decision': 'rejected',
                          'note': 'Duplicate of an existing opening.'},
                         content_type='application/json', **self.auth(self.admin_token))

        entry = ActivityLog.objects.filter(action='rejected').first()
        self.assertEqual(entry.detail['note'], 'Duplicate of an existing opening.')
        self.assertEqual(entry.detail['submitted_by'], 'Staff One')

    def test_the_log_is_for_administrators_only(self):
        self.assertEqual(self.client.get(
            '/api/accounts/portal/admin/activity/',
            **self.auth(self.staff_token)).status_code, 403)

    def test_a_referral_is_attributed_but_never_queued(self):
        """It goes to HR, not the dashboard — so there is nothing to approve."""
        r = self.client.post('/api/referrals/submit/',
                             {'candidate_name': 'A Candidate',
                              'position_applied_for': 'Chemist — Roorkee',
                              'referrer_name': 'Someone Else',
                              'recommendation': 'strong'},
                             content_type='application/json', **self.auth(self.staff_token))
        self.assertEqual(r.status_code, 201)

        d = self.client.get('/api/accounts/portal/admin/moderation/',
                            **self.auth(self.admin_token)).json()
        self.assertEqual(d['pending_total'], 0)

        # The typed referrer name is kept, but so is who actually sent it.
        from referrals.models import EmployeeReferral
        ref = EmployeeReferral.objects.get()
        self.assertEqual(ref.referrer_name, 'Someone Else')
        self.assertEqual(ref.submitted_by, self.staff)
