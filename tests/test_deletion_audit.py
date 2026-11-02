"""Deleted messages are recorded and can be restored (ADR-0012)."""

from __future__ import annotations

from typing import Any

import pytest

from example.models import Project
from example.models import Ticket
from huddle import signals
from huddle.exceptions import ChatPermissionDeniedError
from huddle.exceptions import NothingToRestoreError
from huddle.models import Channel
from huddle.models import Mention
from huddle.models import MessageDeletion
from huddle.models import MessageReference
from huddle.services import ChatService

TICKETS = "example.chat.TicketResolver"


@pytest.fixture
def deletion_signals():
    received: dict[str, list[dict[str, Any]]] = {"deleted": [], "restored": []}

    def on_deleted(**kwargs):
        received["deleted"].append(kwargs)

    def on_restored(**kwargs):
        received["restored"].append(kwargs)

    signals.message_deleted.connect(on_deleted)
    signals.message_restored.connect(on_restored)
    yield received
    signals.message_deleted.disconnect(on_deleted)
    signals.message_restored.disconnect(on_restored)


class TestDeletionRecord:
    def test_deleting_keeps_the_text_mentions_and_who_deleted_it(
        self,
        service,
        general,
        alice,
        bob,
        deletion_signals,
        django_capture_on_commit_callbacks,
    ):
        message = service.post_message(bob, general, "hi @Alice Smith", mentioned=[alice])
        body = message.body
        with django_capture_on_commit_callbacks(execute=True):
            deletion = service.delete_message(alice, message)
        assert (deletion.message, deletion.deleted_by, deletion.body) == (message, alice, body)
        assert deletion.mentioned_user_ids == [str(alice.pk)]
        assert not deletion.is_restored
        assert deletion_signals["deleted"][0]["deletion"] == deletion
        assert deletion_signals["deleted"][0]["user"] == alice

    def test_thread_replies_are_recorded_too(self, service, general, alice):
        root = service.post_message(alice, general, "question")
        reply = service.post_message(alice, general, "answer", parent=root)
        service.delete_message(alice, reply)
        recorded = MessageDeletion.objects.in_channels(Channel.objects.filter(pk=general.pk)).get()
        assert recorded.message.parent == root
        assert recorded.body == "answer"


class TestRestore:
    def test_restore_brings_back_text_and_mentions(
        self,
        service,
        general,
        alice,
        bob,
        deletion_signals,
        django_capture_on_commit_callbacks,
    ):
        message = service.post_message(bob, general, "hi @Alice Smith", mentioned=[alice])
        body = message.body
        service.delete_message(alice, message)
        with django_capture_on_commit_callbacks(execute=True):
            deletion = service.restore_message(alice, message)
        message.refresh_from_db()
        assert not message.is_deleted
        assert message.body == body
        assert Mention.objects.filter(message=message, user=alice).exists()
        assert (deletion.restored_by, deletion.is_restored) == (alice, True)
        assert deletion_signals["restored"][0]["message"] == message

    def test_only_channel_managers_restore(self, service, general, alice, bob):
        message = service.post_message(bob, general, "mine")
        service.delete_message(bob, message)
        with pytest.raises(ChatPermissionDeniedError):
            service.restore_message(bob, message)

    def test_a_live_message_cannot_be_restored(self, service, general, alice):
        message = service.post_message(alice, general, "fine")
        with pytest.raises(ChatPermissionDeniedError):
            service.restore_message(alice, message)

    def test_a_message_without_a_record_has_nothing_to_restore(self, service, general, alice):
        message = service.post_message(alice, general, "old")
        message.body = ""
        message.deleted_at = message.created_at
        message.save()
        with pytest.raises(NothingToRestoreError):
            service.restore_message(alice, message)

    def test_delete_restore_delete_keeps_both_records(self, service, general, alice):
        message = service.post_message(alice, general, "again")
        service.delete_message(alice, message)
        service.restore_message(alice, message)
        message.refresh_from_db()
        service.delete_message(alice, message)
        assert message.deletions.count() == 2  # noqa: PLR2004
        assert message.deletions.unrestored().count() == 1


class TestReferences:
    @pytest.fixture(autouse=True)
    def project_chat(self, settings):
        settings.HUDDLE_POLICY = "example.chat.ProjectChatPolicy"
        settings.HUDDLE_REFERENCE_RESOLVERS = [TICKETS]

    def test_restore_brings_back_references(self, alice, bob):
        project = Project.objects.create(name="Tower")
        project.members.add(alice, bob)
        ticket = Ticket.objects.create(project=project, code="T1", title="Fix the door")
        service = ChatService()
        site = service.create_channel(alice, "site", scope=project)
        message = service.post_message(alice, site, "see #T1")
        service.delete_message(alice, message)
        assert not MessageReference.objects.exists()
        service.restore_message(alice, message)
        assert MessageReference.objects.get().target == ticket
