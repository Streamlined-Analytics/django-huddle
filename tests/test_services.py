from __future__ import annotations

import pytest
from django.db import IntegrityError

from huddle.exceptions import ChatPermissionDeniedError
from huddle.exceptions import InvalidDirectMessageError
from huddle.exceptions import NestedThreadError
from huddle.exceptions import ThreadChannelMismatchError
from huddle.models import Channel
from huddle.models import ChannelMembership
from huddle.models import Mention
from huddle.models import Message
from huddle.models import ThreadParticipant

pytestmark = pytest.mark.django_db(transaction=True)


class TestChannels:
    def test_creator_becomes_member(self, general, alice):
        assert general.memberships.filter(user=alice).exists()
        assert str(general) == "#general"

    def test_cannot_create_scoped_channel_under_default_policy(self, service, alice, bob):
        with pytest.raises(ChatPermissionDeniedError) as error:
            service.create_channel(alice, "secret", scope=bob)
        assert error.value.context["action"] == "create a channel in"

    def test_join_public_channel(self, service, general, bob):
        service.join(bob, general)
        assert general.memberships.filter(user=bob).exists()

    def test_cannot_join_private_channel(self, service, alice, bob):
        private = service.create_channel(alice, "private", is_private=True)
        with pytest.raises(ChatPermissionDeniedError):
            service.join(bob, private)

    def test_leave(self, service, general, alice):
        service.leave(alice, general)
        assert not general.memberships.filter(user=alice).exists()

    def test_add_members_needs_manager(self, service, general, alice, bob, carol):
        with pytest.raises(ChatPermissionDeniedError):
            service.add_members(bob, general, [carol])
        service.add_members(alice, general, [bob, carol])
        service.add_members(alice, general, [bob])
        assert general.memberships.count() == 3


class TestDirectMessages:
    def test_open_direct_is_reused_for_same_people(self, service, alice, bob):
        first = service.open_direct(alice, [bob])
        second = service.open_direct(bob, [alice])
        assert first.pk == second.pk
        assert first.is_direct and first.is_private
        assert set(first.memberships.values_list("user", flat=True)) == {alice.pk, bob.pk}
        assert str(first).startswith("DM ")

    def test_group_direct_is_distinct(self, service, alice, bob, carol):
        assert service.open_direct(alice, [bob]).pk != service.open_direct(alice, [bob, carol]).pk

    def test_direct_with_nobody(self, service, alice):
        with pytest.raises(InvalidDirectMessageError):
            service.open_direct(alice, [alice])

    def test_direct_with_inactive_user_denied(self, service, alice, make_user):
        ghost = make_user("Ghost", is_active=False)
        with pytest.raises(ChatPermissionDeniedError):
            service.open_direct(alice, [ghost])

    def test_cannot_leave_direct(self, service, alice, bob):
        with pytest.raises(ChatPermissionDeniedError):
            service.leave(alice, service.open_direct(alice, [bob]))

    def test_direct_must_be_private(self, alice):
        with pytest.raises(IntegrityError):
            Channel.objects.create(kind=Channel.Kind.DIRECT, is_private=False, direct_key="x")


class TestPosting:
    def test_post_updates_channel_and_reads(self, service, general, alice, received_signals):
        message = service.post_message(alice, general, "hello")
        general.refresh_from_db()
        assert general.last_message_at == message.created_at
        assert ChannelMembership.objects.get(channel=general, user=alice).last_read_at == message.created_at
        assert received_signals["posted"][0]["message"] == message

    def test_posting_auto_joins_public_channel(self, service, general, bob):
        service.post_message(bob, general, "hi")
        assert general.memberships.filter(user=bob).exists()

    def test_cannot_post_in_private_channel_as_outsider(self, service, alice, bob):
        private = service.create_channel(alice, "private", is_private=True)
        with pytest.raises(ChatPermissionDeniedError):
            service.post_message(bob, private, "let me in")

    def test_cannot_post_in_archived_channel(self, service, general, alice):
        general.archived_at = general.created_at
        general.save()
        with pytest.raises(ChatPermissionDeniedError):
            service.post_message(alice, general, "anyone?")

    def test_mentions_recorded_and_signalled(self, service, general, alice, bob, received_signals):
        message = service.post_message(alice, general, "hi @Bob Jones", mentioned=[bob])
        assert message.body == f"hi <@{bob.pk}>"
        assert list(Mention.objects.values_list("user", flat=True)) == [bob.pk]
        event = received_signals["mentioned"][0]
        assert (event["sender"], event["message"], event["user"]) == (Message, message, bob)

    def test_mention_not_in_text_is_ignored(self, service, general, alice, bob, received_signals):
        service.post_message(alice, general, "no names here", mentioned=[bob])
        assert not Mention.objects.exists()
        assert received_signals["mentioned"] == []

    def test_self_mention_is_not_recorded(self, service, general, alice):
        service.post_message(alice, general, "note to @Alice Smith", mentioned=[alice])
        assert not Mention.objects.exists()

    def test_cannot_mention_outsider_in_private_channel(self, service, alice, bob):
        private = service.create_channel(alice, "private", is_private=True)
        message = service.post_message(alice, private, "hi @Bob Jones", mentioned=[bob])
        assert message.body == "hi @Bob Jones"
        assert not Mention.objects.exists()


class TestThreads:
    def test_reply_updates_thread(self, service, general, alice, bob, received_signals):
        root = service.post_message(alice, general, "question")
        reply = service.post_message(bob, general, "answer", parent=root)
        root.refresh_from_db()
        assert root.reply_count == 1
        assert root.last_reply_at == reply.created_at
        followers = set(ThreadParticipant.objects.filter(thread=root).values_list("user", flat=True))
        assert followers == {alice.pk, bob.pk}
        assert [person.pk for person in received_signals["replied"][0]["recipients"]] == [alice.pk]

    def test_mentioned_user_follows_thread(self, service, general, alice, bob, carol):
        root = service.post_message(alice, general, "question")
        service.post_message(bob, general, "ask @Carol White", parent=root, mentioned=[carol])
        assert ThreadParticipant.objects.filter(thread=root, user=carol).exists()

    def test_threads_are_one_level_deep(self, service, general, alice):
        root = service.post_message(alice, general, "question")
        reply = service.post_message(alice, general, "answer", parent=root)
        with pytest.raises(NestedThreadError):
            service.post_message(alice, general, "deeper", parent=reply)

    def test_reply_must_be_in_same_channel(self, service, general, alice):
        other = service.create_channel(alice, "other")
        root = service.post_message(alice, general, "question")
        with pytest.raises(ThreadChannelMismatchError) as error:
            service.post_message(alice, other, "wrong place", parent=root)
        assert error.value.context == {"parent_id": root.pk, "channel_id": other.pk}

    def test_mark_thread_read(self, service, general, alice, bob):
        root = service.post_message(alice, general, "question")
        service.post_message(bob, general, "answer", parent=root)
        service.mark_thread_read(alice, root)
        assert ThreadParticipant.objects.get(thread=root, user=alice).last_read_at is not None


class TestEditingAndDeleting:
    def test_edit_notifies_only_new_mentions(self, service, general, alice, bob, carol, received_signals):
        message = service.post_message(alice, general, "hi @Bob Jones", mentioned=[bob])
        service.edit_message(alice, message, "hi @Bob Jones and @Carol White", mentioned=[bob, carol])
        assert [event["user"] for event in received_signals["mentioned"]] == [bob, carol]
        assert message.edited_at is not None

    def test_edit_reply_adds_thread_follower(self, service, general, alice, carol):
        root = service.post_message(alice, general, "question")
        reply = service.post_message(alice, general, "answer", parent=root)
        service.edit_message(alice, reply, "cc @Carol White", mentioned=[carol])
        assert ThreadParticipant.objects.filter(thread=root, user=carol).exists()

    def test_only_author_edits(self, service, general, alice, bob):
        message = service.post_message(alice, general, "mine")
        with pytest.raises(ChatPermissionDeniedError):
            service.edit_message(bob, message, "yours now")

    def test_delete_is_soft_and_clears_mentions(self, service, general, alice, bob):
        message = service.post_message(alice, general, "hi @Bob Jones", mentioned=[bob])
        service.delete_message(alice, message)
        message.refresh_from_db()
        assert message.is_deleted and message.body == ""
        assert not Mention.objects.exists()

    def test_channel_manager_can_delete_others(self, service, general, alice, bob):
        message = service.post_message(bob, general, "oops")
        service.delete_message(alice, message)
        message.refresh_from_db()
        assert message.is_deleted

    def test_others_cannot_delete(self, service, general, alice, bob):
        message = service.post_message(alice, general, "mine")
        with pytest.raises(ChatPermissionDeniedError):
            service.delete_message(bob, message)

    def test_cannot_delete_twice(self, service, general, alice):
        message = service.post_message(alice, general, "mine")
        service.delete_message(alice, message)
        with pytest.raises(ChatPermissionDeniedError):
            service.delete_message(alice, message)
