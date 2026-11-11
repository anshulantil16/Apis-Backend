"""Getting space back without taking anything somebody can still see.

The risk in this file is not a leak, it is a loss: every function under test
destroys messages permanently. So most of these cases check that a purge
does NOT fire -- a sweep that is too eager is far worse than one that leaves
a few rows behind.
"""
from datetime import timedelta

from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from io import StringIO

from accounts.models import PortalUser
from chat import retention
from chat.models import Attachment, Conversation, Membership, Message


def _user(code, name):
    return PortalUser.objects.create(employee_code=code, name=name, email=f'{code}@apisindia.com')


class PurgeAbandoned(TestCase):
    def setUp(self):
        self.a = _user('HO0101', 'Asha Mehta')
        self.b = _user('HO0102', 'Bhavesh Shah')
        self.long_ago = timezone.now() - timedelta(days=retention.GRACE_DAYS + 5)

    def _thread(self, cleared_a=None, cleared_b=None, last=None):
        c = Conversation.objects.create(last_message_at=last or self.long_ago)
        Membership.objects.create(conversation=c, user=self.a, cleared_at=cleared_a)
        Membership.objects.create(conversation=c, user=self.b, cleared_at=cleared_b)
        m = Message.objects.create(conversation=c, sender=self.a, body='about the Nagpur order')
        return c, m

    def test_thread_both_sides_removed_long_ago_is_destroyed(self):
        c, _ = self._thread(cleared_a=self.long_ago, cleared_b=self.long_ago)
        self.assertEqual(retention.purge_abandoned()['conversations'], 1)
        self.assertFalse(Conversation.objects.filter(id=c.id).exists())
        self.assertFalse(Message.objects.filter(conversation_id=c.id).exists())
        self.assertFalse(Membership.objects.filter(conversation_id=c.id).exists())

    def test_one_side_still_has_it(self):
        """The commonest case, and the one that must never fire.

        The history belongs to both people. One of them tidying their list
        is not consent to destroy the other's copy."""
        c, _ = self._thread(cleared_a=self.long_ago, cleared_b=None)
        self.assertEqual(retention.purge_abandoned()['conversations'], 0)
        self.assertTrue(Message.objects.filter(conversation_id=c.id).exists())

    def test_nobody_has_cleared_it(self):
        c, _ = self._thread()
        self.assertEqual(retention.purge_abandoned()['conversations'], 0)
        self.assertTrue(Conversation.objects.filter(id=c.id).exists())

    def test_inside_the_grace_period(self):
        """Clearing a thread is one click and people do it by accident."""
        recent = timezone.now() - timedelta(days=2)
        c, _ = self._thread(cleared_a=recent, cleared_b=recent, last=recent)
        self.assertEqual(retention.purge_abandoned()['conversations'], 0)
        self.assertTrue(Conversation.objects.filter(id=c.id).exists())

    def test_something_was_said_after_both_cleared_it(self):
        """A cleared thread comes back when somebody speaks again, so it is
        on both their screens and is not abandoned at all."""
        c, _ = self._thread(cleared_a=self.long_ago, cleared_b=self.long_ago,
                            last=timezone.now())
        self.assertEqual(retention.purge_abandoned()['conversations'], 0)
        self.assertTrue(Conversation.objects.filter(id=c.id).exists())

    def test_a_group_needs_every_member_to_have_removed_it(self):
        c = Conversation.objects.create(is_group=True, title='Nagpur dispatch',
                                        last_message_at=self.long_ago)
        third = _user('HO0103', 'Chandra Iyer')
        for u, cleared in ((self.a, self.long_ago), (self.b, self.long_ago), (third, None)):
            Membership.objects.create(conversation=c, user=u, cleared_at=cleared)
        Message.objects.create(conversation=c, sender=self.a, body='loading at six')
        self.assertEqual(retention.purge_abandoned()['conversations'], 0)

        Membership.objects.filter(conversation=c, user=third).update(cleared_at=self.long_ago)
        self.assertEqual(retention.purge_abandoned()['conversations'], 1)

    def test_dry_run_destroys_nothing(self):
        c, _ = self._thread(cleared_a=self.long_ago, cleared_b=self.long_ago)
        self.assertEqual(retention.purge_abandoned(dry_run=True)['conversations'], 1)
        self.assertTrue(Conversation.objects.filter(id=c.id).exists())

    def test_the_file_leaves_the_disk_not_only_the_table(self):
        """A row deleted while its photo stays on disk is not deleted in any
        sense somebody asking for a delete would recognise -- and the disk
        is where nearly all of the space is."""
        c, m = self._thread(cleared_a=self.long_ago, cleared_b=self.long_ago)
        att = Attachment.objects.create(
            message=m, name='scan.pdf', size=2048, kind='file',
            content_type='application/pdf',
            file=ContentFile(b'%PDF-1.4 fake', name='scan.pdf'))
        path = att.file.path
        import os
        self.assertTrue(os.path.exists(path))

        r = retention.purge_abandoned()
        self.assertEqual(r['attachments'], 1)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(Attachment.objects.filter(id=att.id).exists())

    def test_leaves_other_peoples_threads_alone(self):
        dead, _ = self._thread(cleared_a=self.long_ago, cleared_b=self.long_ago)
        live, live_m = self._thread()
        retention.purge_abandoned()
        self.assertFalse(Conversation.objects.filter(id=dead.id).exists())
        self.assertTrue(Message.objects.filter(id=live_m.id).exists())


class PurgeOlderThan(TestCase):
    def setUp(self):
        self.a = _user('HO0201', 'Asha Mehta')
        self.b = _user('HO0202', 'Bhavesh Shah')
        self.c = Conversation.objects.create()
        for u in (self.a, self.b):
            Membership.objects.create(conversation=self.c, user=u)

    def _msg(self, body, days_old):
        m = Message.objects.create(conversation=self.c, sender=self.a, body=body)
        # created_at is auto_now_add, so it has to be written past the model.
        Message.objects.filter(id=m.id).update(
            created_at=timezone.now() - timedelta(days=days_old))
        return m

    def test_old_goes_and_recent_stays(self):
        old = self._msg('last year', 400)
        new = self._msg('this morning', 0)
        r = retention.purge_older_than(365)
        self.assertEqual(r['messages'], 1)
        self.assertFalse(Message.objects.filter(id=old.id).exists())
        self.assertTrue(Message.objects.filter(id=new.id).exists())

    def test_a_reply_to_a_deleted_message_survives_without_its_quote(self):
        """SET_NULL rather than CASCADE: losing the quote is a cosmetic
        loss, losing the answer somebody wrote is not."""
        old = self._msg('what did we quote them?', 400)
        reply = Message.objects.create(conversation=self.c, sender=self.b,
                                       body='eleven lakh', reply_to=old)
        retention.purge_older_than(365)
        reply.refresh_from_db()
        self.assertIsNone(reply.reply_to_id)
        self.assertEqual(reply.body, 'eleven lakh')

    def test_refuses_a_period_under_a_day(self):
        """Zero days would delete the conversation people are having."""
        with self.assertRaises(ValueError):
            retention.purge_older_than(0)

    def test_dry_run_destroys_nothing(self):
        old = self._msg('last year', 400)
        self.assertEqual(retention.purge_older_than(365, dry_run=True)['messages'], 1)
        self.assertTrue(Message.objects.filter(id=old.id).exists())


class SpaceCommand(TestCase):
    def test_reports_without_deleting_unless_asked(self):
        a, b = _user('HO0301', 'Asha Mehta'), _user('HO0302', 'Bhavesh Shah')
        long_ago = timezone.now() - timedelta(days=retention.GRACE_DAYS + 5)
        c = Conversation.objects.create(last_message_at=long_ago)
        for u in (a, b):
            Membership.objects.create(conversation=c, user=u, cleared_at=long_ago)
        Message.objects.create(conversation=c, sender=a, body='hello')

        out = StringIO()
        call_command('chat_space', stdout=out)
        self.assertIn('1 conversations', out.getvalue())
        self.assertIn('Nothing deleted', out.getvalue())
        self.assertTrue(Conversation.objects.filter(id=c.id).exists())

        out = StringIO()
        call_command('chat_space', '--purge-abandoned', stdout=out)
        self.assertIn('removed 1 conversations', out.getvalue())
        self.assertFalse(Conversation.objects.filter(id=c.id).exists())
