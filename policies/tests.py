import io
import shutil
import tempfile
from unittest import mock

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

    COMPLETE = {'department': 'P & C', 'approvedBy': 'Vimal Anand',
                'reviewedBy': 'Pankaj', 'approvalDate': '2026-10-01'}

    def _upload(self, user, **fields):
        data = {'title': 'Leave SOP', 'category': 'SOP', 'file': _pdf(), **self.COMPLETE, **fields}
        return self.client.post(URL, data, **self._auth(user))

    def test_every_field_is_required(self):
        for missing in ('department', 'approvedBy', 'reviewedBy', 'approvalDate'):
            r = self._upload(self.admin, **{missing: ''})
            self.assertEqual(r.status_code, 400, missing)
        self.assertEqual(self._upload(self.admin, approvalDate='not-a-date').status_code, 400)
        self.assertFalse(PolicyDocument.objects.exists())

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
        # Still theirs to see while it waits for approval -- but not theirs
        # to remove; only an administrator does that.
        self.assertTrue(mine[0]['isMine'])
        self.assertFalse(mine[0]['canDelete'])

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

    def test_word_and_excel_are_stored_and_served_as_pdf(self):
        """Whatever is uploaded, the register holds a PDF, so opening any
        document shows it in the browser rather than downloading it."""
        converted = _pdf(pages=3).read()
        with mock.patch('policies.views.convert_to_pdf', return_value=converted) as conv:
            for name in ('format.xlsx', 'leave.docx'):
                office = SimpleUploadedFile(name, b'PK\x03\x04rest-of-zip')
                r = self._upload(self.admin, category='Formats', file=office)
                self.assertEqual(r.status_code, 201, r.content)
                self.assertTrue(r.json()['file'].endswith('/view/'), r.json()['file'])
                self.assertEqual(r.json()['pages'], 3)
                self.assertEqual(r.json()['fileName'], name)   # what was sent
                self.assertEqual(r.json()['sizeBytes'], len(converted))
        self.assertEqual(conv.call_count, 2)
        for d in PolicyDocument.objects.all():
            with d.file.open('rb') as fh:
                self.assertEqual(fh.read(5), b'%PDF-')

    def test_a_pdf_is_not_sent_through_the_converter(self):
        with mock.patch('policies.views.convert_to_pdf') as conv:
            self.assertEqual(self._upload(self.admin).status_code, 201)
        conv.assert_not_called()

    def test_a_file_that_wont_convert_is_refused_not_stored_as_is(self):
        from .pdf_convert import ConversionError
        office = SimpleUploadedFile('broken.docx', b'PK\x03\x04rest-of-zip')
        with mock.patch('policies.views.convert_to_pdf',
                        side_effect=ConversionError('Please save it as a PDF.')):
            r = self._upload(self.admin, file=office)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'Please save it as a PDF.')
        self.assertFalse(PolicyDocument.objects.exists())

    def test_without_libreoffice_the_message_says_to_upload_a_pdf(self):
        office = SimpleUploadedFile('format.xlsx', b'PK\x03\x04rest-of-zip')
        with mock.patch('policies.pdf_convert.soffice_path', return_value=None):
            r = self._upload(self.admin, file=office)
        self.assertEqual(r.status_code, 400)
        self.assertIn('PDF', r.json()['error'])

    def test_the_uploader_cannot_delete_their_own(self):
        """It used to be allowed, for the "added it by accident" case.

        This register is the company's standing policies: a document here is
        something people are expected to follow, and taking it down is not
        the uploader's call any more than publishing it was. The accident
        case goes through an administrator, which leaves a trail.
        """
        self._upload(self.alice)
        doc = PolicyDocument.objects.get()
        r = self.client.delete(f'{URL}{doc.id}/', **self._auth(self.alice))
        self.assertEqual(r.status_code, 403, r.content[:200])
        self.assertTrue(PolicyDocument.objects.exists(),
                        'the uploader removed a published policy')

    def test_an_admin_deletes_and_the_file_goes_with_it(self):
        self._upload(self.alice)
        doc = PolicyDocument.objects.get()
        storage, name = doc.file.storage, doc.file.name
        self.assertTrue(storage.exists(name))

        r = self.client.delete(f'{URL}{doc.id}/', **self._auth(self.admin))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(PolicyDocument.objects.exists())
        self.assertFalse(storage.exists(name),
                         'the row went but the file stayed served')

    def test_nobody_but_an_admin_is_offered_the_delete_control(self):
        """canDelete drives the icon on the page, so an employee who cannot
        delete must not be shown a button that will refuse them."""
        self._upload(self.alice)
        for who in (self.alice, self.bob):
            rows = self.client.get(URL, **self._auth(who)).json()
            for row in rows:
                self.assertFalse(row['canDelete'], f'{who.email} was offered delete')
        admin_rows = self.client.get(URL, **self._auth(self.admin)).json()
        self.assertTrue(all(r['canDelete'] for r in admin_rows))

    def test_someone_else_cannot_delete_but_admin_can(self):
        self._upload(self.alice)
        doc = PolicyDocument.objects.get()
        self.assertEqual(self.client.delete(f'{URL}{doc.id}/', **self._auth(self.bob)).status_code, 403)
        self.assertEqual(self.client.delete(f'{URL}{doc.id}/').status_code, 401)
        self.assertEqual(self.client.delete(f'{URL}{doc.id}/', **self._auth(self.admin)).status_code, 200)

    def test_file_link_is_root_relative_not_the_host_django_was_reached_on(self):
        # Behind the QA proxy Django is reached as 127.0.0.1:8001; a link
        # carrying that host sends the browser to the viewer's own machine.
        r = self.client.post(URL, {'title': 'x', 'category': 'SOP', 'file': _pdf(), **self.COMPLETE},
                             HTTP_HOST='127.0.0.1:8001', **self._auth(self.admin))
        did = r.json()['id']
        self.assertEqual(r.json()['file'], f'/api/policies/documents/{did}/view/')
        listed = self.client.get(URL, HTTP_HOST='127.0.0.1:8001').json()
        self.assertEqual(listed[0]['file'], f'/api/policies/documents/{did}/view/')

    # ── reading a document, not keeping a copy of it ────────────────────────

    def test_the_register_never_hands_out_the_stored_files_media_url(self):
        """Nothing in the payload points at /media/. That URL is a plain
        download, and it skips the approval check as well."""
        self._upload(self.admin)
        d = PolicyDocument.objects.get()
        self.assertNotIn('/media/', str(self.client.get(URL).content))
        self.assertNotIn('/media/', d.moderation_payload()['link'])
        self.assertEqual(self.client.get(d.file.url).status_code, 404)

    def test_a_document_is_served_to_be_read_in_place_not_saved(self):
        self._upload(self.admin, title='Leave Policy')
        d = PolicyDocument.objects.get()
        r = self.client.get(f'/api/policies/documents/{d.id}/view/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        # inline, never attachment — attachment is the Save dialog.
        self.assertEqual(r['Content-Disposition'], 'inline; filename="Leave Policy.pdf"')
        self.assertEqual(r['X-Content-Type-Options'], 'nosniff')
        self.assertIn('no-store', r['Cache-Control'])
        self.assertEqual(b''.join(r.streaming_content)[:5], b'%PDF-')

    def test_a_title_cannot_smuggle_anything_into_the_header(self):
        """The filename is built from the title, and a title is free text. A
        quote would close the header's value early; a newline would start a
        header of the attacker's choosing."""
        nasty = 'Leave"; attachment; x="y' + chr(13) + chr(10) + 'Set-Cookie: a=b'
        self._upload(self.admin, title=nasty)
        d = PolicyDocument.objects.get()
        r = self.client.get(f'/api/policies/documents/{d.id}/view/')
        self.assertEqual(r['Content-Disposition'],
                         'inline; filename="Leave attachment xySet-Cookie ab.pdf"')

    def test_an_unapproved_document_is_not_readable_by_a_link_alone(self):
        """It was, from /media/: the file was there the moment it was
        uploaded, whatever the register showed."""
        self._upload(self.alice)
        d = PolicyDocument.objects.get()
        url = f'/api/policies/documents/{d.id}/view/'
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url, **self._auth(self.bob)).status_code, 404)
        self.assertEqual(self.client.get(url, **self._auth(self.alice)).status_code, 200)
        self.assertEqual(self.client.get(url, **self._auth(self.admin)).status_code, 200)

    def test_a_missing_document_or_missing_file_answers_404(self):
        self._upload(self.admin)
        d = PolicyDocument.objects.get()
        d.file.storage.delete(d.file.name)
        self.assertEqual(self.client.get(f'/api/policies/documents/{d.id}/view/').status_code, 404)
        self.assertEqual(self.client.get('/api/policies/documents/99999/view/').status_code, 404)

    def test_pending_upload_reaches_the_approval_queue_with_a_link(self):
        self._upload(self.alice)
        payload = PolicyDocument.objects.get().moderation_payload()
        self.assertTrue(payload['link'].endswith('/view/'), payload['link'])
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
