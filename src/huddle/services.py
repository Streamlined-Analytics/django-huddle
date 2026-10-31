"""Every chat write goes through :class:`ChatService`.

The service checks the policy, keeps denormalised counters and read state in
step, and — once the transaction commits — broadcasts to websockets and sends
the public signals. Views and host code should call it rather than writing
huddle models directly.

Encoding a message is a service job, not a markup job, because it needs the
database: picked mentions are limited to ``mentionable_users``, free-typed
``@handles`` are matched against them, and ``#codes`` are resolved by the
configured reference resolvers for the sender in that channel. The markup
then only substitutes what the service found.
"""

from __future__ import annotations

from collections import Counter
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from huddle import signals
from huddle.conf import get_policy
from huddle.conf import get_reference_registry
from huddle.conf import get_setting
from huddle.exceptions import ChatPermissionDeniedError
from huddle.exceptions import InvalidDirectMessageError
from huddle.exceptions import NestedThreadError
from huddle.exceptions import ThreadChannelMismatchError
from huddle.markup import MessageMarkup
from huddle.markup import mention_handles
from huddle.markup import mention_name
from huddle.models import Channel
from huddle.models import ChannelMembership
from huddle.models import Mention
from huddle.models import Message
from huddle.models import MessageReference
from huddle.models import ThreadParticipant
from huddle.policy import ChatPolicy
from huddle.realtime import Broadcaster
from huddle.references import Reference
from huddle.references import ReferenceRegistry
from huddle.rendering import ChatRenderer
from huddle.rendering import EditableLink

MAX_RESOLVED_CODES = 50


@dataclass(frozen=True)
class EncodedMessage:
    """A body ready to store, with the users and references the service allowed into it."""

    body: str
    mentioned: list[Any]
    references: list[Reference]


@dataclass(frozen=True)
class KeptLinks:
    """Links an edit may keep: what the editor's composer showed for each existing token.

    ``mentions`` maps the lower-case typed form (``ann lee``) to the user and
    ``references`` maps the code (``T1`` or ``unavailable-2``) to the
    reference. Only tokens already on the message are here, so keeping one can
    never forge a link.
    """

    mentions: dict[str, Any] = field(default_factory=dict)
    references: dict[str, Reference] = field(default_factory=dict)


def _unique_users(users: Iterable[Any]) -> list[Any]:
    return list({user.pk: user for user in users}.values())


def _only(users: dict[Any, Any]) -> Any | None:
    return next(iter(users.values())) if len(users) == 1 else None


def _kept_reference(link: EditableLink) -> Reference:
    if isinstance(link.target, Reference):
        return link.target
    kind, _, object_id = link.token.removeprefix("<#").removesuffix(">").partition(":")
    return Reference(kind=kind, id=object_id, code=link.form.removeprefix("#"), label="", url="")


class ChatService:
    """Create channels and DMs, post, edit and delete messages, and track reading."""

    def __init__(
        self,
        policy: ChatPolicy | None = None,
        markup: MessageMarkup | None = None,
        broadcaster: Broadcaster | None = None,
        references: ReferenceRegistry | None = None,
    ) -> None:
        self.policy = policy or get_policy()
        self.markup = markup or MessageMarkup()
        self.broadcaster = broadcaster or Broadcaster()
        self.references = references if references is not None else get_reference_registry()

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

    def post_message(  # noqa: PLR0913 - keyword-only options of a public method
        self,
        user: Any,
        channel: Channel,
        text: str,
        *,
        mentioned: Iterable[Any] = (),
        parent: Message | None = None,
        references: Iterable[str] = (),
    ) -> Message:
        """Post ``text`` to ``channel``, or as a reply in ``parent``'s thread.

        ``mentioned`` are the users picked from autocomplete; ``references``
        are the ``kind:id`` tokens picked for ``#codes``. Free-typed handles
        and codes are resolved too (see :meth:`encode`).
        """
        if not self.policy.can_post(user, channel):
            raise ChatPermissionDeniedError("post in", user.pk, channel.pk)
        if parent is not None:
            self._check_thread_parent(parent, channel)
        encoded = self.encode(user, channel, text, mentioned=mentioned, references=references)
        now = timezone.now()
        with transaction.atomic():
            message = Message.objects.create(
                channel=channel,
                author=user,
                parent=parent,
                body=encoded.body,
                created_at=now,
            )
            new_mentions = self._record_mentions(message, encoded.mentioned)
            new_references = self._record_references(message, encoded.references)
            self._mark_author_read(user, channel, now)
            Channel.objects.filter(pk=channel.pk).update(last_message_at=now)
            if parent is not None:
                self._record_reply(parent, user, new_mentions, now)
            transaction.on_commit(lambda: self._announce_post(message, new_mentions, new_references))
        return message

    def edit_message(
        self,
        user: Any,
        message: Message,
        text: str,
        *,
        mentioned: Iterable[Any] = (),
        references: Iterable[str] = (),
    ) -> Message:
        """Replace a message's text. Newly mentioned users and newly referenced objects are announced once.

        An existing mention or reference whose composer form (``@Ann Lee``,
        ``#T1``, ``#unavailable-2``) is still in ``text`` is kept as it was,
        even if the editor can no longer see its target. Removing the text
        removes the link. New ``@`` and ``#`` text follows the usual rules.
        """
        if not self.policy.can_edit_message(user, message):
            raise ChatPermissionDeniedError("edit", user.pk, message.pk)
        kept = self.kept_links(user, message)
        encoded = self.encode(user, message.channel, text, mentioned=mentioned, references=references, kept=kept)
        with transaction.atomic():
            message.body = encoded.body
            message.edited_at = timezone.now()
            message.save(update_fields=["body", "edited_at"])
            new_mentions = self._record_mentions(message, encoded.mentioned)
            new_references = self._record_references(message, encoded.references)
            if message.parent is not None:
                self._follow_thread(message.parent, new_mentions)
            transaction.on_commit(lambda: self._announce_edit(message, new_mentions, new_references))
        return message

    def encode(  # noqa: PLR0913 - keyword-only options of a public method
        self,
        user: Any,
        channel: Channel,
        text: str,
        *,
        mentioned: Iterable[Any] = (),
        references: Iterable[str] = (),
        kept: KeptLinks | None = None,
    ) -> EncodedMessage:
        """Work out what ``text`` from ``user`` may mention and reference in ``channel``, and encode it.

        - Picked users count only if the policy lets them be mentioned here.
        - A free-typed ``@handle`` or ``@Full Name`` counts if exactly one
          mentionable user owns it; the longest match wins.
        - ``kept`` links (see :meth:`kept_links`) win over everything else.
        - Picked references count only if the sender may see them here; other
          ``#codes`` go to the resolvers, first in settings order wins.
          Unknown or unauthorised codes stay plain text.
        """
        kept = kept or KeptLinks()
        picked_users = self._allowed_mentions(channel, mentioned)
        typed = {**self._typed_owners(channel, text, picked_users), **kept.mentions}
        found = {
            **self._found_references(user, channel, text, references, skip=kept.references.keys()),
            **kept.references,
        }
        typed_users = [person for person in typed.values() if person is not None]
        return EncodedMessage(
            body=self.markup.encode(text, picked_users, handles=typed, references=found),
            mentioned=_unique_users([*picked_users, *typed_users]),
            references=list(found.values()),
        )

    def kept_links(self, user: Any, message: Message) -> KeptLinks:
        """The links on ``message`` an edit by ``user`` keeps if their composer form is still in the text.

        Forms are what ``user``'s composer shows (``@Ann Lee``, ``#T1``,
        ``#unavailable-1``…). References need a recorded row and mentions an
        existing user. A form shared by two different tokens is ambiguous and
        is not kept; that text then goes through the usual rules.
        """
        renderer = ChatRenderer(user, self.policy, self.markup, self.references)
        recorded = {f"<#{row.token}>" for row in message.references.all()}
        links = [link for link in renderer.editable_links(message) if _is_keepable(link, recorded)]
        uses = Counter(link.form.lower() for link in links)
        links = [link for link in links if uses[link.form.lower()] == 1]
        return KeptLinks(
            mentions={link.form[1:].lower(): link.target for link in links if link.form.startswith("@")},
            references={link.form[1:]: _kept_reference(link) for link in links if link.form.startswith("#")},
        )

    def delete_message(self, user: Any, message: Message) -> None:
        """Soft-delete a message: its text and mentions are removed, a placeholder remains."""
        if not self.policy.can_delete_message(user, message):
            raise ChatPermissionDeniedError("delete", user.pk, message.pk)
        with transaction.atomic():
            message.body = ""
            message.deleted_at = timezone.now()
            message.save(update_fields=["body", "deleted_at"])
            message.mentions.all().delete()
            message.references.all().delete()
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

    def _typed_owners(self, channel: Channel, text: str, picked: list[Any]) -> dict[str, Any | None]:
        """Typed handles and full names in ``text`` mapped to the one mentionable user they fit.

        A key that fits several users maps to ``None``, so the markup leaves
        that text alone rather than falling back to a shorter match.
        """
        if not get_setting("HUDDLE_FREE_TYPED_MENTIONS") or not self.markup.has_typed_mentions(text, picked):
            return {}
        handles = self.markup.handles(text, picked)
        lowered = text.lower()
        owners: defaultdict[str, dict[Any, Any]] = defaultdict(dict)
        for candidate in self.policy.mentionable_users(channel).iterator():
            for key in _typed_keys(candidate, handles, lowered):
                owners[key][candidate.pk] = candidate
        return {key: _only(users) for key, users in owners.items()}

    def _found_references(
        self, user: Any, channel: Channel, text: str, picks: Iterable[str], *, skip: Iterable[str] = ()
    ) -> dict[str, Reference]:
        """Typed ``#codes`` mapped to the references they encode to: exact picks first, then free-typed lookups.

        At most ``MAX_RESOLVED_CODES`` unpicked codes are looked up per message,
        so a message full of ``#`` words cannot fan out into thousands of queries.
        """
        if not self.references:
            return {}
        codes = self.markup.reference_codes(text)
        if not codes:
            return {}
        picked = {reference.code: reference for reference in self.references.visible(channel, user, picks).values()}
        skipped = set(skip)
        unpicked = [code for code in codes if code not in picked and code not in skipped][:MAX_RESOLVED_CODES]
        return {**self.references.resolve_codes(channel, user, unpicked), **picked}

    def _record_references(
        self, message: Message, references: list[Reference]
    ) -> list[tuple[MessageReference, Reference]]:
        """Make the message's reference rows match its body. Returns the rows that are new."""
        if not self.references:
            return []
        in_body = set(self.markup.referenced_tokens(message.body))
        wanted = {reference.token: reference for reference in references if reference.token in in_body}
        existing = {row.token: row.pk for row in message.references.all()}
        stale = [pk for token, pk in existing.items() if token not in wanted]
        MessageReference.objects.filter(pk__in=stale).delete()
        new = [reference for token, reference in wanted.items() if token not in existing]
        rows = MessageReference.objects.bulk_create([self._reference_row(message, reference) for reference in new])
        return list(zip(rows, new, strict=True))

    @staticmethod
    def _reference_row(message: Message, reference: Reference) -> MessageReference:
        """A row for ``reference``. ``content_type`` is only set when the stored id is the target's primary key.

        A resolver may store something other than the pk (by overriding
        ``object_id``); a content type paired with that id would make
        ``target`` and ``for_target()`` point at the wrong row.
        """
        target = reference.target
        is_model_pk = isinstance(target, models.Model) and reference.id == str(target.pk)
        content_type = ContentType.objects.get_for_model(target) if is_model_pk else None
        return MessageReference(message=message, kind=reference.kind, object_id=reference.id, content_type=content_type)

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

    def _announce_post(
        self,
        message: Message,
        new_mentions: list[Any],
        new_references: list[tuple[MessageReference, Reference]],
    ) -> None:
        self._broadcast(message, "created")
        member_ids = list(message.channel.memberships.values_list("user_id", flat=True))
        self.broadcaster.channel_activity(channel_id=message.channel_id, user_ids=member_ids)
        signals.message_posted.send(Message, message=message, channel=message.channel, author=message.author)
        self._send_mentions(message, new_mentions)
        self._send_references(message, new_references)
        if message.parent is not None:
            followers = ThreadParticipant.objects.filter(thread=message.parent).select_related("user")
            recipients = [participant for participant in followers if participant.user_id != message.author_id]
            signals.thread_replied.send(
                Message,
                message=message,
                thread=message.parent,
                recipients=[participant.user for participant in recipients],
            )

    def _announce_edit(
        self,
        message: Message,
        new_mentions: list[Any],
        new_references: list[tuple[MessageReference, Reference]],
    ) -> None:
        self._broadcast(message, "updated")
        self._send_mentions(message, new_mentions)
        self._send_references(message, new_references)

    def _send_references(self, message: Message, references: list[tuple[MessageReference, Reference]]) -> None:
        for row, reference in references:
            signals.reference_added.send(Message, message=message, reference=row, target=reference.target)

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


def _typed_keys(candidate: Any, handles: set[str], lowered_text: str) -> set[str]:
    """The keys in the text that could name ``candidate``: its handles, and its full name if typed."""
    keys = mention_handles(candidate) & handles
    name = mention_name(candidate)
    if name and f"@{name}" in lowered_text:
        keys.add(name)
    return keys


def _is_keepable(link: EditableLink, recorded_references: set[str]) -> bool:
    if link.token.startswith("<@"):
        return link.target is not None
    return link.token in recorded_references
