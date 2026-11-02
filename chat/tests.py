"""What Messages must never do.

This is the first thing here that holds conversations people expect to be
private, so most of what follows is about who can read what rather than
about whether a message arrives.
"""
from django.test import TestCase
from django.utils import timezone

from accounts.models import PortalSession, PortalUser

from .models import Conversation, Membership, Message


def person(code, name, apps=('chat',), superadmin=False):
    return PortalUser.objects.create(
        employee_code=code, name=name, email=f'{code.lower()}@apisindia.com',
        app_access=list(apps), is_superadmin=superadmin)


class ChatTestCase(TestCase):
    def setUp(self):
        self.asha = person('HO0101', 'Asha Rao')
        self.bhav = person('HO0102', 'Bhavesh Shah')
        self.chandra = person('HO0103', 'Chandra Iyer')

    def as_(self, user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + PortalSession.start(user)}

    def thread(self, a, b):
        conv = Conversation.objects.create()
        Membership.objects.create(conversation=conv, user=a)
        Membership.objects.create(conversation=conv, user=b)
        return conv

    def send(self, user, conv, body):
        return self.client.post(f'/api/chat/conversations/{conv.id}/messages/',
                                {'body': body}, content_type='application/json',
                                **self.as_(user))


class NobodyReadsSomebodyElsesMessages(ChatTestCase):
    def test_an_outsider_cannot_read_a_thread(self):
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'about your increment')
        r = self.client.get(f'/api/chat/conversations/{conv.id}/messages/', **self.as_(self.chandra))
        # 404, not 403: that these two are talking is itself private.
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(b'increment', r.content)

    def test_a_superadmin_cannot_read_a_thread_either(self):
        """A superadmin decides who may OPEN Messages. That is not the same
        as being able to read what colleagues say to each other, and the
        difference is the whole reason anyone would use this."""
        boss = person('HO0900', 'Boss', superadmin=True)
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'strictly between us')
        r = self.client.get(f'/api/chat/conversations/{conv.id}/messages/', **self.as_(boss))
        self.assertEqual(r.status_code, 404)
        self.assertNotIn(b'strictly between us', r.content)

    def test_an_outsider_cannot_post_into_a_thread(self):
        conv = self.thread(self.asha, self.bhav)
        self.assertEqual(self.send(self.chandra, conv, 'hello').status_code, 404)
        self.assertEqual(Message.objects.count(), 0)

    def test_somebody_elses_list_is_not_in_yours(self):
        self.send(self.asha, self.thread(self.asha, self.bhav), 'hi')
        r = self.client.get('/api/chat/conversations/', **self.as_(self.chandra))
        self.assertEqual(r.json()['conversations'], [])

    def test_signed_out_is_401_and_without_the_grant_is_403(self):
        conv = self.thread(self.asha, self.bhav)
        self.assertEqual(self.client.get('/api/chat/conversations/').status_code, 401)
        nobody = person('HO0104', 'No Access', apps=())
        self.assertEqual(self.client.get('/api/chat/conversations/', **self.as_(nobody)).status_code, 403)
        self.assertEqual(self.send(nobody, conv, 'hi').status_code, 403)

    def test_nobody_can_rewrite_what_the_other_person_said(self):
        conv = self.thread(self.asha, self.bhav)
        mid = self.send(self.asha, conv, 'I said this').json()['message']['id']
        r = self.client.patch(f'/api/chat/conversations/{conv.id}/messages/{mid}/',
                              {'body': 'I said something else'},
                              content_type='application/json', **self.as_(self.bhav))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(Message.objects.get(id=mid).body, 'I said this')


class SendingAndReading(ChatTestCase):
    def test_a_message_reaches_the_other_person(self):
        conv = self.thread(self.asha, self.bhav)
        self.assertEqual(self.send(self.asha, conv, 'morning').status_code, 201)
        r = self.client.get(f'/api/chat/conversations/{conv.id}/messages/', **self.as_(self.bhav))
        body = r.json()
        self.assertEqual([m['body'] for m in body['messages']], ['morning'])
        self.assertFalse(body['messages'][0]['mine'])
        self.assertEqual(body['person']['name'], 'Asha Rao')

    def test_messages_come_back_oldest_first(self):
        conv = self.thread(self.asha, self.bhav)
        for word in ('one', 'two', 'three'):
            self.send(self.asha, conv, word)
        r = self.client.get(f'/api/chat/conversations/{conv.id}/messages/', **self.as_(self.bhav))
        self.assertEqual([m['body'] for m in r.json()['messages']], ['one', 'two', 'three'])

    def test_every_message_carries_the_moment_it_was_sent(self):
        """The screen groups by day -- Today, Yesterday, 12 October -- so a
        message without a timestamp has nowhere to sit."""
        conv = self.thread(self.asha, self.bhav)
        m = self.send(self.asha, conv, 'hi').json()['message']
        self.assertTrue(m['sent_at'])
        self.assertEqual(Message.objects.get(id=m['id']).created_at.date(), timezone.now().date())

    def test_polling_asks_only_for_what_is_new(self):
        conv = self.thread(self.asha, self.bhav)
        first = self.send(self.asha, conv, 'one').json()['message']['id']
        self.send(self.asha, conv, 'two')
        r = self.client.get(f'/api/chat/conversations/{conv.id}/messages/?after={first}',
                            **self.as_(self.bhav))
        self.assertEqual([m['body'] for m in r.json()['messages']], ['two'])

    def test_an_empty_message_is_refused(self):
        conv = self.thread(self.asha, self.bhav)
        self.assertEqual(self.send(self.asha, conv, '   ').status_code, 400)

    def test_a_deleted_message_leaves_the_thread_readable(self):
        """Removing the row would silently drop a message somebody had
        already replied to, leaving an answer to nothing."""
        conv = self.thread(self.asha, self.bhav)
        mid = self.send(self.asha, conv, 'ignore that').json()['message']['id']
        self.send(self.bhav, conv, 'ok')
        r = self.client.delete(f'/api/chat/conversations/{conv.id}/messages/{mid}/', **self.as_(self.asha))
        self.assertEqual(r.status_code, 200)
        msgs = self.client.get(f'/api/chat/conversations/{conv.id}/messages/',
                               **self.as_(self.bhav)).json()['messages']
        self.assertEqual(len(msgs), 2)
        self.assertTrue(msgs[0]['deleted'])
        self.assertEqual(msgs[0]['body'], '')


class UnreadCounts(ChatTestCase):
    def test_your_own_message_is_never_unread_to_you(self):
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'hi')
        self.assertEqual(self.client.get('/api/chat/unread/', **self.as_(self.asha)).json()['unread'], 0)
        self.assertEqual(self.client.get('/api/chat/unread/', **self.as_(self.bhav)).json()['unread'], 1)

    def test_opening_the_thread_clears_it(self):
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'hi')
        self.client.post(f'/api/chat/conversations/{conv.id}/read/', **self.as_(self.bhav))
        self.assertEqual(self.client.get('/api/chat/unread/', **self.as_(self.bhav)).json()['unread'], 0)

    def test_a_later_message_counts_again(self):
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'one')
        self.client.post(f'/api/chat/conversations/{conv.id}/read/', **self.as_(self.bhav))
        self.send(self.asha, conv, 'two')
        self.assertEqual(self.client.get('/api/chat/unread/', **self.as_(self.bhav)).json()['unread'], 1)


class StartingAConversation(ChatTestCase):
    def test_opening_the_same_person_twice_lands_in_one_thread(self):
        """Otherwise the second tap opens an empty thread and the history
        looks lost."""
        for _ in range(2):
            r = self.client.post('/api/chat/conversations/', {'employee_code': 'HO0102'},
                                 content_type='application/json', **self.as_(self.asha))
            self.assertIn(r.status_code, (200, 201))
        self.assertEqual(Conversation.objects.count(), 1)

    def test_it_finds_the_thread_the_other_person_started(self):
        self.client.post('/api/chat/conversations/', {'employee_code': 'HO0101'},
                         content_type='application/json', **self.as_(self.bhav))
        r = self.client.post('/api/chat/conversations/', {'employee_code': 'HO0102'},
                             content_type='application/json', **self.as_(self.asha))
        self.assertFalse(r.json()['created'])
        self.assertEqual(Conversation.objects.count(), 1)

    def test_you_cannot_message_somebody_who_cannot_read_it(self):
        """A message to somebody without the tool is never seen and never
        answered, which reads as being ignored."""
        person('HO0105', 'Not Granted', apps=())
        r = self.client.post('/api/chat/conversations/', {'employee_code': 'HO0105'},
                             content_type='application/json', **self.as_(self.asha))
        self.assertEqual(r.status_code, 400)
        self.assertIn('not been given access', r.json()['error'])

    def test_you_cannot_message_yourself(self):
        r = self.client.post('/api/chat/conversations/', {'employee_code': 'HO0101'},
                             content_type='application/json', **self.as_(self.asha))
        self.assertEqual(r.status_code, 400)

    def test_the_picker_only_offers_people_who_can_reply(self):
        person('HO0106', 'Not Granted', apps=())
        names = [p['name'] for p in
                 self.client.get('/api/chat/people/', **self.as_(self.asha)).json()['people']]
        self.assertIn('Bhavesh Shah', names)
        self.assertNotIn('Not Granted', names)
        self.assertNotIn('Asha Rao', names, 'not yourself')


class ClearingYourOwnList(ChatTestCase):
    def test_clearing_hides_it_from_you_and_not_from_them(self):
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'hi')
        self.client.post(f'/api/chat/conversations/{conv.id}/clear/', **self.as_(self.bhav))
        self.assertEqual(self.client.get('/api/chat/conversations/', **self.as_(self.bhav))
                         .json()['conversations'], [])
        self.assertEqual(len(self.client.get('/api/chat/conversations/', **self.as_(self.asha))
                             .json()['conversations']), 1)

    def test_the_history_is_still_there_when_it_comes_back(self):
        conv = self.thread(self.asha, self.bhav)
        self.send(self.asha, conv, 'the old message')
        self.client.post(f'/api/chat/conversations/{conv.id}/clear/', **self.as_(self.bhav))
        self.send(self.asha, conv, 'are you there?')
        convs = self.client.get('/api/chat/conversations/', **self.as_(self.bhav)).json()['conversations']
        self.assertEqual(len(convs), 1)
        bodies = [m['body'] for m in
                  self.client.get(f'/api/chat/conversations/{conv.id}/messages/',
                                  **self.as_(self.bhav)).json()['messages']]
        self.assertEqual(bodies, ['the old message', 'are you there?'])
