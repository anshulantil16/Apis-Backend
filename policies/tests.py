import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from accounts.models import PortalSession, PortalUser
from accounts.moderation import ModerationStatus

from .models import BuiltInRemoval, PolicyDocument

URL = '/api/policies/documents/'
TMP_MEDIA = tempfile.mkdtemp()


def _pdf(pages=2, name='leave.pdf'):
    from pypdf import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return SimpleUploadedFile(name, buf.getvalue(), content_type='application/pdf')


@override_settings(MEDIA_ROOT=TMP_MEDIA)
class PolicyDocuments(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP_MEDIA, ignore_errors=True)

    def setUp(self):
        self.admin = PortalUser.objects.create(email='a@apisindia.com', employee_code='A1',
                                               name='Admin', is_superadmin=True)
        self.alice = PortalUser.objects.create(email='alice@apisindia.com', employee_code='E1',
                                               name='Alice')
        self.bob = PortalUser.objects.create(email='bob@apisindia.com', employee_code='E2',
                                             name='Bob')

    def _auth(self, user):
        return {'HTTP_AUTHORIZATION': f'Bearer {PortalSession.start(user)}'}

    def _upload(self, user, **fields):
        data = {'title': 'Leave SOP', 'category': 'SOP', 'file': _pdf(), **fields}
        return self.client.post(URL, data, **self._auth(user))

    def test_upload_needs_sign_in(self):
        r = self.client.post(URL, {'title': 'x', 'category': 'SOP', 'file': _pdf()})
        self.assertEqual(r.status_code, 401)
        self.assertFalse(PolicyDocument.objects.exists())

    def test_admin_upload_is_listed_for_everyone_with_page_count(self):
        r = self._upload(self.admin, version='3', approvalDate='2026-10-01')
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['pages'], 2)
        self.assertEqual(r.json()['version'], 3)

        listed = self.client.get(URL).json()
        self.assertEqual([d['title'] for d in listed], ['Leave SOP'])
        self.assertFalse(listed[0]['canDelete'])

    def test_employee_upload_waits_for_approval_but_its_uploader_sees_it(self):
        self.assertEqual(self._upload(self.alice).status_code, 201)
        doc = PolicyDocument.objects.get()
        self.assertEqual(doc.moderation_status, ModerationStatus.PENDING)

        self.assertEqual(self.client.get(URL).json(), [])
        self.assertEqual(self.client.get(URL, **self._auth(self.bob)).json(), [])
        mine = self.client.get(URL, **self._auth(self.alice)).json()
        self.assertEqual(len(mine), 1)
        self.assertTrue(mine[0]['isMine'] and mine[0]['canDelete'])

    def test_rejects_unknown_category(self):
        r = self._upload(self.admin, category='Memes')
        self.assertEqual(r.status_code, 400)

    def test_rejects_a_file_whose_bytes_are_not_what_its_name_says(self):
        fake = SimpleUploadedFile('policy.pdf', b'<script>alert(1)</script>',
                                  content_type='application/pdf')
        r = self._upload(self.admin, file=fake)
        self.assertEqual(r.status_code, 400)
        html = SimpleUploadedFile('policy.html', b'%PDF-', content_type='text/html')
        self.assertEqual(self._upload(self.admin, file=html).status_code, 400)

    def test_accepts_word_and_excel(self):
        docx = SimpleUploadedFile('format.xlsx', b'PK\x03\x04rest-of-zip')
        r = self._upload(self.admin, category='Formats', file=docx)
        self.assertEqual(r.status_code, 201, r.content)
        self.assertIsNone(r.json()['pages'])

    def test_uploader_can_delete_own_and_file_goes_with_it(self):
        self._upload(self.alice)
        doc = PolicyDocument.objects.get()
        storage, name = doc.file.storage, doc.file.name
        self.assertTrue(storage.exists(name))

        r = self.client.delete(f'{URL}{doc.id}/', **self._auth(self.alice))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(PolicyDocument.objects.exists())
        self.assertFalse(storage.exists(name))

    def test_someone_else_cannot_delete_but_admin_can(self):
        self._upload(self.alice)
        doc = PolicyDocument.objects.get()
        self.assertEqual(self.client.delete(f'{URL}{doc.id}/', **self._auth(self.bob)).status_code, 403)
        self.assertEqual(self.client.delete(f'{URL}{doc.id}/').status_code, 401)
        self.assertEqual(self.client.delete(f'{URL}{doc.id}/', **self._auth(self.admin)).status_code, 200)

    def test_pending_upload_reaches_the_approval_queue_with_a_link(self):
        self._upload(self.alice)
        payload = PolicyDocument.objects.get().moderation_payload()
        self.assertTrue(payload['link'].endswith('.pdf'))
        self.assertEqual(payload['type'], 'policydocument')

    # ── built-in PDFs (public/Policies/) ────────────────────────────────────
    BUILTIN = '/api/policies/built-in/removed/'

    def test_only_a_superadmin_can_remove_a_built_in_policy(self):
        body = {'file': 'LOAN POLICY 2025.pdf', 'title': 'Loan Policy'}
        self.assertEqual(self.client.post(self.BUILTIN, body).status_code, 401)
        self.assertEqual(self.client.post(self.BUILTIN, body, **self._auth(self.alice)).status_code, 403)
        self.assertFalse(BuiltInRemoval.objects.exists())

        self.assertEqual(self.client.post(self.BUILTIN, body, **self._auth(self.admin)).status_code, 200)
        # Twice is harmless.
        self.assertEqual(self.client.post(self.BUILTIN, body, **self._auth(self.admin)).status_code, 200)

        listed = self.client.get(self.BUILTIN).json()
        self.assertEqual(listed, {'removed': ['LOAN POLICY 2025.pdf'], 'canRemove': False})
        self.assertTrue(self.client.get(self.BUILTIN, **self._auth(self.admin)).json()['canRemove'])
        self.assertEqual(BuiltInRemoval.objects.get().removed_by, self.admin)

    def test_built_in_removal_takes_a_filename_not_a_path(self):
        for bad in ('../settings.py', 'a/b.pdf', 'a\\b.pdf', ''):
            r = self.client.post(self.BUILTIN, {'file': bad}, **self._auth(self.admin))
            self.assertEqual(r.status_code, 400, bad)
