"""Every chat write goes through :class:`ChatService`.

The service checks the policy, keeps denormalised counters and read state in
step, and — once the transaction commits — broadcasts to websockets and sends
the public signals. Views and host code should call it rather than writing
huddle models directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.db import models
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from huddle import signals
from huddle.conf import get_policy
from huddle.exceptions import ChatPermissionDeniedError
from huddle.exceptions import InvalidDirectMessageError
from huddle.exceptions import NestedThreadError
from huddle.exceptions import ThreadChannelMismatchError
from huddle.markup import MessageMarkup
from huddle.models import Channel
from huddle.models import ChannelMembership
from huddle.models import Mention
from huddle.models import Message
from huddle.models import ThreadParticipant
from huddle.policy import ChatPolicy
from huddle.realtime import Broadcaster


class ChatService:
    """Create channels and DMs, post, edit and delete messages, and track reading."""

    def __init__(
        self,
        policy: ChatPolicy | None = None,
        markup: MessageMarkup | None = None,
        broadcaster: Broadcaster | None = None,
    ) -> None:
        self.policy = policy or get_policy()
        self.markup = markup or MessageMarkup()
        self.broadcaster = broadcaster or Broadcaster()

    def create_channel(
        self,
        user: Any,
        name: str,
        *,
        scope: models.Model | None = None,
        is_private: bool = False,
        topic: str = "",
    ) -> Channel:
        """Create a named channel in ``scope`` with ``user`` as its first member."""
        if not self.policy.can_create_channel(user, scope):
            raise ChatPermissionDeniedError("create a channel in", user.pk, getattr(scope, "pk", None))
        with transaction.atomic():
            channel = Channel(name=name, topic=topic, is_private=is_private, created_by=user)
            if scope is not None:
                channel.scope = scope
            channel.save()
            ChannelMembership.objects.create(channel=channel, user=user)
        return channel

    def open_direct(self, user: Any, others: Iterable[Any]) -> Channel:
        """Return the direct message between ``user`` and ``others``, creating it if needed."""
        allowed = self.policy.direct_message_candidates(user)
        other_pks = {person.pk for person in others} - {user.pk}
        if not other_pks:
            raise InvalidDirectMessageError(user.pk)
        if allowed.filter(pk__in=other_pks).count() != len(other_pks):
            raise ChatPermissionDeniedError("message", user.pk, sorted(other_pks))
        member_pks = sorted({user.pk, *other_pks}, key=str)
        direct_key = "-".join(str(pk) for pk in member_pks)
        with transaction.atomic():
            channel, created = Channel.objects.get_or_create(
                direct_key=direct_key,
                defaults={"kind": Channel.Kind.DIRECT, "is_private": True, "created_by": user},
            )
            if created:
                ChannelMembership.objects.bulk_create(
                    [ChannelMembership(channel=channel, user_id=pk) for pk in member_pks]
                )
        return channel

    def join(self, user: Any, channel: Channel) -> ChannelMembership:
        """Join a channel ``user`` can see (public channels, or ones they already belong to)."""
        if not self.policy.can_view(user, channel):
            raise ChatPermissionDeniedError("join", user.pk, channel.pk)
        membership, _ = ChannelMembership.objects.get_or_create(
            channel=channel, user=user, defaults={"last_read_at": timezone.now()}
        )
        return membership

    def leave(self, user: Any, channel: Channel) -> None:
        """Leave a channel. Leaving a direct message is not supported."""
        if channel.is_direct:
            raise ChatPermissionDeniedError("leave", user.pk, channel.pk)
        ChannelMembership.objects.filter(channel=channel, user=user).delete()

    def add_members(self, actor: Any, channel: Channel, users: Iterable[Any]) -> None:
        """Add ``users`` to a channel ``actor`` manages."""
        if not self.policy.can_manage_channel(actor, channel):
            raise ChatPermissionDeniedError("add members to", actor.pk, channel.pk)
        ChannelMembership.objects.bulk_create(
            [ChannelMembership(channel=channel, user=user) for user in users],
            ignore_conflicts=True,
        )

    def post_message(
        self,
        user: Any,
        channel: Channel,
        text: str,
        *,
        mentioned: Iterable[Any] = (),
        parent: Message | None = None,
    ) -> Message:
        """Post ``text`` to ``channel``, or as a reply in ``parent``'s thread."""
        if not self.policy.can_post(user, channel):
            raise ChatPermissionDeniedError("post in", user.pk, channel.pk)
        if parent is not None:
            self._check_thread_parent(parent, channel)
        mention_users = self._allowed_mentions(channel, mentioned)
        now = timezone.now()
        with transaction.atomic():
            message = Message.objects.create(
                channel=channel,
                author=user,
                parent=parent,
                body=self.markup.encode(text, mention_users),
                created_at=now,
            )
            new_mentions = self._record_mentions(message, mention_users)
            self._mark_author_read(user, channel, now)
            Channel.objects.filter(pk=channel.pk).update(last_message_at=now)
            if parent is not None:
                self._record_reply(parent, user, new_mentions, now)
            transaction.on_commit(lambda: self._announce_post(message, new_mentions))
        return message

    def edit_message(self, user: Any, message: Message, text: str, *, mentioned: Iterable[Any] = ()) -> Message:
        """Replace a message's text. Newly mentioned users are notified; existing mentions are not repeated."""
        if not self.policy.can_edit_message(user, message):
            raise ChatPermissionDeniedError("edit", user.pk, message.pk)
        mention_users = self._allowed_mentions(message.channel, mentioned)
        with transaction.atomic():
            message.body = self.markup.encode(text, mention_users)
            message.edited_at = timezone.now()
            message.save(update_fields=["body", "edited_at"])
            new_mentions = self._record_mentions(message, mention_users)
            if message.parent is not None:
                self._follow_thread(message.parent, new_mentions)
            transaction.on_commit(lambda: self._announce_edit(message, new_mentions))
        return message

    def delete_message(self, user: Any, message: Message) -> None:
        """Soft-delete a message: its text and mentions are removed, a placeholder remains."""
        if not self.policy.can_delete_message(user, message):
            raise ChatPermissionDeniedError("delete", user.pk, message.pk)
        with transaction.atomic():
            message.body = ""
            message.deleted_at = timezone.now()
            message.save(update_fields=["body", "deleted_at"])
            message.mentions.all().delete()
            transaction.on_commit(lambda: self._broadcast(message, "deleted"))

    def mark_read(self, user: Any, channel: Channel) -> None:
        """Record that ``user`` has read ``channel`` up to now."""
        ChannelMembership.objects.filter(channel=channel, user=user).update(last_read_at=timezone.now())

    def mark_thread_read(self, user: Any, thread: Message) -> None:
        """Record that ``user`` has read ``thread`` up to now."""
        ThreadParticipant.objects.filter(thread=thread, user=user).update(last_read_at=timezone.now())

    def _check_thread_parent(self, parent: Message, channel: Channel) -> None:
        if parent.channel_id != channel.pk:
            raise ThreadChannelMismatchError(parent.pk, channel.pk)
        if parent.parent_id is not None:
            raise NestedThreadError(parent.pk)

    def _allowed_mentions(self, channel: Channel, mentioned: Iterable[Any]) -> list[Any]:
        pks = {person.pk for person in mentioned}
        if not pks:
            return []
        return list(self.policy.mentionable_users(channel).filter(pk__in=pks))

    def _record_mentions(self, message: Message, users: list[Any]) -> list[Any]:
        mentioned_pks = self.markup.mentioned_pks(message.body)
        in_body = [user for user in users if str(user.pk) in mentioned_pks and user.pk != message.author_id]
        existing = set(message.mentions.values_list("user_id", flat=True))
        new_users = [user for user in in_body if user.pk not in existing]
        Mention.objects.bulk_create([Mention(message=message, user=user) for user in new_users])
        return new_users

    def _mark_author_read(self, user: Any, channel: Channel, now: Any) -> None:
        membership, created = ChannelMembership.objects.get_or_create(
            channel=channel, user=user, defaults={"last_read_at": now}
        )
        if not created:
            ChannelMembership.objects.filter(pk=membership.pk).update(last_read_at=now)

    def _record_reply(self, parent: Message, user: Any, mentioned: list[Any], now: Any) -> None:
        Message.objects.filter(pk=parent.pk).update(reply_count=F("reply_count") + 1, last_reply_at=now)
        followers = [parent.author, user, *mentioned]
        self._follow_thread(parent, [person for person in followers if person is not None])
        ThreadParticipant.objects.filter(thread=parent, user=user).update(last_read_at=now)

    def _follow_thread(self, thread: Message, users: Iterable[Any]) -> None:
        ThreadParticipant.objects.bulk_create(
            [ThreadParticipant(thread=thread, user=user) for user in users],
            ignore_conflicts=True,
        )

    def _announce_post(self, message: Message, new_mentions: list[Any]) -> None:
        self._broadcast(message, "created")
        member_ids = list(message.channel.memberships.values_list("user_id", flat=True))
        self.broadcaster.channel_activity(channel_id=message.channel_id, user_ids=member_ids)
        signals.message_posted.send(Message, message=message, channel=message.channel, author=message.author)
        self._send_mentions(message, new_mentions)
        if message.parent is not None:
            followers = ThreadParticipant.objects.filter(thread=message.parent).select_related("user")
            recipients = [participant for participant in followers if participant.user_id != message.author_id]
            signals.thread_replied.send(
                Message,
                message=message,
                thread=message.parent,
                recipients=[participant.user for participant in recipients],
            )

    def _announce_edit(self, message: Message, new_mentions: list[Any]) -> None:
        self._broadcast(message, "updated")
        self._send_mentions(message, new_mentions)

    def _send_mentions(self, message: Message, users: list[Any]) -> None:
        for user in users:
            signals.user_mentioned.send(Message, message=message, user=user)

    def _broadcast(self, message: Message, action: str) -> None:
        self.broadcaster.message_changed(
            action=action,
            message_id=message.pk,
            channel_id=message.channel_id,
            thread_id=message.parent_id,
        )
