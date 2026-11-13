"""Attachments, groups, replies, seen marks and search.

The attachment cases are the ones that matter most. A file on an intranet
share is still a file anyone holding the link can take, and what colleagues
send each other is payslips, ID scans and offer letters.
"""
import base64

from django.core.files.uploadedfile import SimpleUploadedFile

from .models import Attachment, Conversation, Message
from .tests import ChatTestCase, person

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE'
    'hQGAhKmMIQAAAABJRU5ErkJggg==')


def png(name='photo.png'):
    """A real (if tiny) PNG, so the upload path runs rather than a stub."""
    return SimpleUploadedFile(name, PNG, content_type='image/png')


def upload(name, body=b'hello', content_type='application/octet-stream'):
    return SimpleUploadedFile(name, body, content_type=content_type)


class AttachmentsReachOnlyTheConversation(ChatTestCase):
    def attach(self, user, conv, f, body=''):
        return self.client.post(f'/api/chat/conversations/{conv.id}/messages/',
                                {'files': f, 'body': body}, **self.as_(user))

    def test_a_photo_is_sent_and_comes_back_on_the_message(self):
        conv = self.thread(self.asha, self.bhav)
        r = self.attach(self.asha, conv, png())
        self.assertEqual(r.status_code, 201, r.content)
        a = r.json()['message']['attachments'][0]
        self.assertEqual((a['name'], a['kind']), ('photo.png', 'image'))
        self.assertGreater(a['size'], 0)

    def test_an_outsider_cannot_fetch_the_file(self):
        conv = self.thread(self.asha, self.bhav)
        aid = self.attach(self.asha, conv, png()).json()['message']['attachments'][0]['id']
        self.assertEqual(self.client.get(f'/api/chat/attachments/{aid}/',
                                         **self.as_(self.chandra)).status_code, 404)
        self.assertEqual(self.client.get(f'/api/chat/attachments/{aid}/').status_code, 401)

    def test_the_other_person_can_fetch_it(self):
        conv = self.thread(self.asha, self.bhav)
        aid = self.attach(self.asha, conv, png()).json()['message']['attachments'][0]['id']
        r = self.client.get(f'/api/chat/attachments/{aid}/', **self.as_(self.bhav))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['X-Content-Type-Options'], 'nosniff')
        self.assertIn('inline', r['Content-Disposition'])

    def test_a_file_downloads_rather_than_opening_in_a_tab(self):
        """Nothing stored here is rendered as a document by our own origin
        -- that is the route from an upload to a script on the intranet."""
        conv = self.thread(self.asha, self.bhav)
        aid = (self.attach(self.asha, conv, upload('report.pdf', b'%PDF-1.4 x'))
               .json()['message']['attachments'][0]['id'])
        r = self.client.get(f'/api/chat/attachments/{aid}/', **self.as_(self.bhav))
        self.assertIn('attachment', r['Content-Disposition'])

    def test_a_dangerous_file_is_refused(self):
        conv = self.thread(self.asha, self.bhav)
        for name in ('payload.svg', 'page.html', 'setup.exe', 'run.sh', 'app.apk'):
            r = self.attach(self.asha, conv, upload(name))
            self.assertEqual(r.status_code, 400, name)
            self.assertIn('cannot be sent', r.json()['error'])
        self.assertEqual(Message.objects.count(), 0, 'nothing was written')

    def test_the_type_is_decided_here_not_by_the_uploader(self):
        """A browser will happily label an HTML file image/png."""
        conv = self.thread(self.asha, self.bhav)
        r = self.attach(self.asha, conv, upload('note.txt', b'hi', content_type='image/png'))
        self.assertEqual(r.json()['message']['attachments'][0]['kind'], 'file')

    def test_a_file_that_is_too_big_is_refused(self):
        conv = self.thread(self.asha, self.bhav)
        r = self.attach(self.asha, conv, upload('huge.pdf', b'x' * (16 * 1024 * 1024)))
        self.assertEqual(r.status_code, 400)
        self.assertIn('limit', r.json()['error'])

    def test_one_bad_file_sends_none_of_them(self):
        """Otherwise a message arrives with two of three files on it and
        nobody can tell which one is missing."""
        conv = self.thread(self.asha, self.bhav)
        r = self.client.post(f'/api/chat/conversations/{conv.id}/messages/',
                             {'files': [png(), upload('x.exe')]}, **self.as_(self.asha))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Message.objects.count(), 0)

    def test_deleting_a_message_takes_its_files_with_it(self):
        """An unsent photo that is still downloadable is not unsent."""
        conv = self.thread(self.asha, self.bhav)
        msg = self.attach(self.asha, conv, png()).json()['message']
        aid = msg['attachments'][0]['id']
        self.client.delete(f'/api/chat/conversations/{conv.id}/messages/{msg["id"]}/',
                           **self.as_(self.asha))
        self.assertEqual(self.client.get(f'/api/chat/attachments/{aid}/',
                                         **self.as_(self.bhav)).status_code, 404)

    def test_a_message_with_only_a_file_is_allowed(self):
        conv = self.thread(self.asha, self.bhav)
        self.assertEqual(self.attach(self.asha, conv, png()).status_code, 201)

    def test_the_raw_media_path_is_not_served(self):
        conv = self.thread(self.asha, self.bhav)
        self.attach(self.asha, conv, png())
        self.assertEqual(self.client.get(f'/media/{Attachment.objects.get().file.name}').status_code, 404)


class RepliesQuoteWithinTheThread(ChatTestCase):
    def test_a_reply_carries_what_it_answers(self):
        conv = self.thread(self.asha, self.bhav)
        first = self.send(self.asha, conv, 'can you send the sheet?').json()['message']['id']
        r = self.client.post(f'/api/chat/conversations/{conv.id}/messages/',
                             {'body': 'sent', 'reply_to': first},
                             content_type='application/json', **self.as_(self.bhav))
        quoted = r.json()['message']['reply_to']
        self.assertEqual(quoted['body'], 'can you send the sheet?')
        self.assertEqual(quoted['sender_name'], 'Asha Rao')

    def test_you_cannot_quote_a_message_from_another_conversation(self):
        """A quote carries the text with it, so this would lift a line out
        of a thread the reader is not in."""
        theirs = self.thread(self.asha, self.chandra)
        secret = self.send(self.asha, theirs, 'about your appraisal').json()['message']['id']
        mine = self.thread(self.asha, self.bhav)
        r = self.client.post(f'/api/chat/conversations/{mine.id}/messages/',
                             {'body': 'look', 'reply_to': secret},
                             content_type='application/json', **self.as_(self.asha))
        self.assertEqual(r.status_code, 400)

    def test_deleting_the_original_does_not_delete_the_reply(self):
        conv = self.thread(self.asha, self.bhav)
        first = self.send(self.asha, conv, 'original').json()['message']['id']
        self.client.post(f'/api/chat/conversations/{conv.id}/messages/',
                         {'body': 'answer', 'reply_to': first},
                         content_type='application/json', **self.as_(self.bhav))
        self.client.delete(f'/api/chat/conversations/{conv.id}/messages/{first}/',
                           **self.as_(self.asha))
        msgs = self.client.get(f'/api/chat/conversations/{conv.id}/messages/',
                               **self.as_(self.bhav)).json()['messages']
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[1]['reply_to']['body'], 'Message deleted')


class SeenMarks(ChatTestCase):
    def test_your_message_is_seen_once_they_have_read_past_it(self):
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'hi')
        mine = self.client.get(f'/api/chat/conversations/{conv.id}/messages/',
                               **self.as_(self.asha)).json()['messages']
        self.assertFalse(mine[0]['seen'])
        self.client.post(f'/api/chat/conversations/{conv.id}/read/', **self.as_(self.bhav))
        mine = self.client.get(f'/api/chat/conversations/{conv.id}/messages/',
                               **self.as_(self.asha)).json()['messages']
        self.assertTrue(mine[0]['seen'])

    def test_their_messages_never_carry_a_seen_mark(self):
        """Seen is about the reader. Showing it on their own message would
        be telling somebody what they already know."""
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'hi')
        self.client.post(f'/api/chat/conversations/{conv.id}/read/', **self.as_(self.bhav))
        theirs = self.client.get(f'/api/chat/conversations/{conv.id}/messages/',
                                 **self.as_(self.bhav)).json()['messages']
        self.assertFalse(theirs[0]['seen'])


class SearchingAThread(ChatTestCase):
    def test_it_finds_the_line_and_only_within_that_thread(self):
        mine = self.thread(self.asha, self.bhav)
        self.send(self.asha, mine, 'the diwali schedule is out')
        self.send(self.asha, mine, 'something else')
        theirs = self.thread(self.asha, self.chandra)
        self.send(self.asha, theirs, 'diwali bonus details')

        found = self.client.get(f'/api/chat/conversations/{mine.id}/messages/?q=diwali',
                                **self.as_(self.bhav)).json()['messages']
        self.assertEqual([m['body'] for m in found], ['the diwali schedule is out'])

    def test_a_deleted_message_is_not_searchable(self):
        conv = self.thread(self.asha, self.bhav)
        mid = self.send(self.asha, conv, 'forget this').json()['message']['id']
        self.client.delete(f'/api/chat/conversations/{conv.id}/messages/{mid}/', **self.as_(self.asha))
        found = self.client.get(f'/api/chat/conversations/{conv.id}/messages/?q=forget',
                                **self.as_(self.bhav)).json()['messages']
        self.assertEqual(found, [])


class Groups(ChatTestCase):
    def make(self, owner, codes, title='Sales Huddle'):
        return self.client.post('/api/chat/conversations/',
                                {'employee_codes': codes, 'title': title},
                                content_type='application/json', **self.as_(owner))

    def test_a_group_is_created_with_everyone_in_it(self):
        r = self.make(self.asha, ['HO0102', 'HO0103'])
        self.assertEqual(r.status_code, 201)
        conv = Conversation.objects.get(id=r.json()['id'])
        self.assertTrue(conv.is_group)
        self.assertEqual(conv.members.count(), 3)

    def test_everyone_in_it_sees_the_messages(self):
        conv = Conversation.objects.get(id=self.make(self.asha, ['HO0102', 'HO0103']).json()['id'])
        self.send(self.bhav, conv, 'morning all')
        for who in (self.asha, self.chandra):
            msgs = self.client.get(f'/api/chat/conversations/{conv.id}/messages/',
                                   **self.as_(who)).json()['messages']
            self.assertIn('morning all', [m['body'] for m in msgs])

    def test_somebody_outside_it_sees_nothing(self):
        outsider = person('HO0107', 'Outsider')
        conv_id = self.make(self.asha, ['HO0102', 'HO0103']).json()['id']
        self.assertEqual(self.client.get(f'/api/chat/conversations/{conv_id}/messages/',
                                         **self.as_(outsider)).status_code, 404)

    def test_a_group_needs_a_name_and_somebody_in_it(self):
        self.assertEqual(self.make(self.asha, ['HO0102'], title='').status_code, 400)
        self.assertEqual(self.make(self.asha, []).status_code, 400)

    def test_adding_somebody_is_said_out_loud_in_the_thread(self):
        conv_id = self.make(self.asha, ['HO0102']).json()['id']
        person('HO0108', 'Late Joiner')
        r = self.client.post(f'/api/chat/conversations/{conv_id}/members/',
                             {'employee_code': 'HO0108'},
                             content_type='application/json', **self.as_(self.asha))
        self.assertEqual(r.status_code, 201)
        msgs = self.client.get(f'/api/chat/conversations/{conv_id}/messages/',
                               **self.as_(self.bhav)).json()['messages']
        self.assertEqual(msgs[-1]['kind'], 'system')
        self.assertIn('added Late Joiner', msgs[-1]['body'])

    def test_leaving_stops_you_reading_it(self):
        conv_id = self.make(self.asha, ['HO0102']).json()['id']
        self.assertEqual(self.client.delete(f'/api/chat/conversations/{conv_id}/members/',
                                            **self.as_(self.bhav)).status_code, 200)
        self.assertEqual(self.client.get(f'/api/chat/conversations/{conv_id}/messages/',
                                         **self.as_(self.bhav)).status_code, 404)

    def test_a_system_message_cannot_be_edited_by_anyone(self):
        conv_id = self.make(self.asha, ['HO0102']).json()['id']
        sysmsg = Message.objects.filter(kind='system').first()
        r = self.client.patch(f'/api/chat/conversations/{conv_id}/messages/{sysmsg.id}/',
                              {'body': 'Asha removed everyone'},
                              content_type='application/json', **self.as_(self.asha))
        self.assertEqual(r.status_code, 404)

    def test_a_direct_conversation_is_still_found_once(self):
        """Groups share the table with direct threads, so "open the thread
        with Bhavesh" must not match a group they are both in."""
        self.make(self.asha, ['HO0102'])
        a = self.client.post('/api/chat/conversations/', {'employee_code': 'HO0102'},
                             content_type='application/json', **self.as_(self.asha)).json()
        b = self.client.post('/api/chat/conversations/', {'employee_code': 'HO0102'},
                             content_type='application/json', **self.as_(self.asha)).json()
        self.assertEqual(a['id'], b['id'])
        self.assertFalse(Conversation.objects.get(id=a['id']).is_group)
