"""Changes reaching the other person without a reload.

The bug these exist for: the thread poll asked for messages `after` the
highest id it had. A delete and an edit both change a row that is already on
the reader's screen without raising that id, so neither ever arrived -- a
deleted message sat there looking perfectly normal until the reader happened
to reload the page. Which is the one moment a delete needs to be quick.
"""
from django.test import TestCase
from django.utils import timezone

from chat.models import Conversation, Membership, Message
from chat.tests import person


class ChangesSince(TestCase):
    def setUp(self):
        self.asha = self._person('HO0401', 'Asha Mehta')
        self.bhavesh = self._person('HO0402', 'Bhavesh Shah')
        self.conv = Conversation.objects.create()
        for u in (self.asha, self.bhavesh):
            Membership.objects.create(conversation=self.conv, user=u)
        self.m = Message.objects.create(conversation=self.conv, sender=self.asha,
                                        body='eleven lakh, final')

    _person = staticmethod(person)

    def _get(self, user, **params):
        from accounts.models import PortalSession
        return self.client.get(
            f'/api/chat/conversations/{self.conv.id}/messages/', params,
            HTTP_AUTHORIZATION='Bearer ' + PortalSession.start(user))

    def test_a_delete_reaches_the_other_person(self):
        """The whole point. Asha deletes; Bhavesh's poll must learn of it
        without him reloading."""
        before = self._get(self.bhavesh)
        self.assertEqual(before.status_code, 200)
        mark = before.json()['now']

        self.m.body, self.m.deleted_at = '', timezone.now()
        self.m.save(update_fields=['body', 'deleted_at'])

        after = self._get(self.bhavesh, since=mark)
        self.assertEqual(after.status_code, 200)
        rows = after.json()['messages']
        self.assertEqual([r['id'] for r in rows], [self.m.id])
        self.assertTrue(rows[0]['deleted'])
        self.assertEqual(rows[0]['body'], '')

    def test_an_edit_reaches_the_other_person(self):
        mark = self._get(self.bhavesh).json()['now']
        self.m.body, self.m.edited_at = 'twelve lakh, final', timezone.now()
        self.m.save(update_fields=['body', 'edited_at'])

        rows = self._get(self.bhavesh, since=mark).json()['messages']
        self.assertEqual([r['id'] for r in rows], [self.m.id])
        self.assertEqual(rows[0]['body'], 'twelve lakh, final')
        self.assertTrue(rows[0]['edited'])

    def test_a_new_message_still_arrives(self):
        """`since` replaces `after` on the poll, so it has to do that job
        too, or the fix for deletes breaks ordinary chatting."""
        mark = self._get(self.bhavesh).json()['now']
        fresh = Message.objects.create(conversation=self.conv, sender=self.asha,
                                       body='and the freight?')
        rows = self._get(self.bhavesh, since=mark).json()['messages']
        self.assertEqual([r['id'] for r in rows], [fresh.id])

    def test_a_quiet_thread_answers_with_nothing(self):
        """This runs every few seconds for everyone with a thread open, so
        the settled case must stay cheap."""
        mark = self._get(self.bhavesh).json()['now']
        self.assertEqual(self._get(self.bhavesh, since=mark).json()['messages'], [])

    def test_one_poll_carries_a_delete_and_a_new_message_together(self):
        mark = self._get(self.bhavesh).json()['now']
        self.m.body, self.m.deleted_at = '', timezone.now()
        self.m.save(update_fields=['body', 'deleted_at'])
        fresh = Message.objects.create(conversation=self.conv, sender=self.asha,
                                       body='ignore that')

        rows = self._get(self.bhavesh, since=mark).json()['messages']
        self.assertEqual([r['id'] for r in rows], [self.m.id, fresh.id])
        self.assertTrue(rows[0]['deleted'])

    def test_a_rubbish_since_is_refused_rather_than_ignored(self):
        """Silently treating it as 'no filter' would return the whole thread
        on every poll, which is the sort of thing nobody notices until the
        server is under load."""
        r = self._get(self.bhavesh, since='yesterday afternoon')
        self.assertEqual(r.status_code, 400)

    def test_somebody_outside_the_conversation_still_gets_nothing(self):
        """The new parameter must not become a way around membership."""
        outsider = self._person('HO0403', 'Chandra Iyer')
        r = self._get(outsider, since=timezone.now().isoformat())
        self.assertEqual(r.status_code, 404)

    def test_after_still_works(self):
        """An old bundle in somebody's browser keeps polling with `after`
        through the deploy."""
        fresh = Message.objects.create(conversation=self.conv, sender=self.asha, body='hi')
        rows = self._get(self.bhavesh, after=self.m.id).json()['messages']
        self.assertEqual([r['id'] for r in rows], [fresh.id])


class TooMuchChangedAtOnce(ChangesSince):
    """A laptop shut on Friday, opened on Monday.

    One answer carries 200 changes. If it quietly returned the first 200
    and the client moved its mark past the lot, every message after those
    200 would be invisible until a reload -- the same class of bug as the
    one this whole change fixes, just rarer and harder to notice.
    """

    def test_a_normal_poll_is_not_marked_truncated(self):
        mark = self._get(self.bhavesh).json()['now']
        Message.objects.create(conversation=self.conv, sender=self.asha, body='one')
        self.assertFalse(self._get(self.bhavesh, since=mark).json()['truncated'])

    def test_an_overflowing_poll_says_so(self):
        mark = self._get(self.bhavesh).json()['now']
        Message.objects.bulk_create([
            Message(conversation=self.conv, sender=self.asha, body=f'n{i}')
            for i in range(205)
        ])
        body = self._get(self.bhavesh, since=mark).json()
        self.assertTrue(body['truncated'])
        self.assertEqual(len(body['messages']), 200)
