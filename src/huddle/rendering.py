"""Render messages and sidebar entries for one viewer.

Views and the websocket consumer share :class:`ChatRenderer`, so a message
looks the same whether it arrived with the page or over the socket, and the
per-viewer bits (edit/delete buttons, "you") are always computed for the user
who will see them.

References are re-checked here, at render time, for the viewer: someone who
lost access to an item since a message was sent sees a neutral placeholder,
never its label. A page of messages costs one query for the reference rows and
one resolver ``get`` per channel and kind, however many messages it holds.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils.safestring import SafeString

from huddle.conf import get_policy
from huddle.conf import get_reference_registry
from huddle.markup import MENTION_TOKEN
from huddle.markup import REFERENCE_TOKEN
from huddle.markup import MessageMarkup
from huddle.markup import display_name
from huddle.models import Channel
from huddle.models import Message
from huddle.models import MessageReference
from huddle.policy import ChatPolicy
from huddle.references import Reference
from huddle.references import ReferenceRegistry

ReferenceMap = dict[str, Reference | None]


@dataclass(frozen=True)
class MessageView:
    """Everything a template needs to draw one message for one viewer.

    ``text`` is the editable composer text and ``references`` the host objects
    this viewer may see in the message, in the order they appear.
    """

    message: Message
    author_name: str
    html: SafeString
    can_edit: bool
    can_delete: bool
    is_own: bool
    text: str = ""
    references: tuple[Reference, ...] = ()

    @property
    def pk(self) -> Any:
        """The message's primary key."""
        return self.message.pk


@dataclass(frozen=True)
class EditableLink:
    """An existing token in a message, and how the composer shows it to one editor.

    ``target`` is the mentioned user, the visible :class:`Reference`, or
    ``None`` when the editor can't see it (a deleted user, or a reference
    shown as ``#unavailable-N``).
    """

    token: str
    form: str
    target: Any


@dataclass(frozen=True)
class ChannelEntry:
    """A sidebar row: a channel or DM, its label for this viewer, and unread count."""

    channel: Channel
    label: str
    unread_count: int
    is_member: bool


class ChatRenderer:
    """Build view models and HTML fragments for ``viewer``."""

    def __init__(
        self,
        viewer: Any,
        policy: ChatPolicy | None = None,
        markup: MessageMarkup | None = None,
        references: ReferenceRegistry | None = None,
    ) -> None:
        self.viewer = viewer
        self.policy = policy or get_policy()
        self.markup = markup or MessageMarkup()
        self.references = references if references is not None else get_reference_registry()

    def message_views(self, messages: Iterable[Message]) -> list[MessageView]:
        """View models for ``messages``, resolving every mention and reference in bulk."""
        messages = list(messages)
        users_by_pk = self._mentioned_users(messages)
        references = self._references(messages)
        return [self._message_view(message, users_by_pk, references.get(message.pk, {})) for message in messages]

    def message_view(self, message: Message) -> MessageView:
        """View model for a single message."""
        return self.message_views([message])[0]

    def editable_text(self, message: Message) -> str:
        """The composer text for editing ``message``."""
        return self.message_view(message).text

    def editable_links(self, message: Message) -> list[EditableLink]:
        """Every token in ``message`` with the text this viewer's composer shows for it."""
        users_by_pk = self._mentioned_users([message])
        references = self._references([message]).get(message.pk, {})
        forms = self.markup.editable_forms(message.body, users_by_pk, references)
        return [
            EditableLink(token=token, form=form, target=self._link_target(token, users_by_pk, references))
            for token, form in forms.items()
        ]

    @staticmethod
    def _link_target(token: str, users_by_pk: dict[str, Any], references: ReferenceMap) -> Any:
        mention = MENTION_TOKEN.fullmatch(token)
        if mention is not None:
            return users_by_pk.get(mention.group(1))
        reference = REFERENCE_TOKEN.fullmatch(token)
        return references.get(reference.group(1)) if reference is not None else None

    def channel_label(self, channel: Channel) -> str:
        """``#name`` for channels; the other members' names for DMs."""
        if not channel.is_direct:
            return f"#{channel.name}"
        others = [membership.user for membership in channel.memberships.all() if membership.user_id != self.viewer.pk]
        return ", ".join(sorted(display_name(user) for user in others)) or display_name(self.viewer)

    def channel_entries(self, channels: Iterable[Channel]) -> list[ChannelEntry]:
        """Sidebar rows for ``channels`` (annotated with ``with_membership_of(viewer)``)."""
        return [
            ChannelEntry(
                channel=channel,
                label=self.channel_label(channel),
                unread_count=getattr(channel, "unread_count", 0),
                is_member=getattr(channel, "is_member", False),
            )
            for channel in channels
        ]

    def render_fragment(self, template_name: str, context: dict[str, Any]) -> str:
        """Render a template with the viewer in context."""
        return render_to_string(template_name, {"viewer": self.viewer, **context})

    def _message_view(self, message: Message, users_by_pk: dict[str, Any], references: ReferenceMap) -> MessageView:
        author = message.author
        return MessageView(
            message=message,
            author_name=display_name(author) if author is not None else "Deleted user",
            html=self.markup.render(message.body, users_by_pk, references),
            can_edit=self.policy.can_edit_message(self.viewer, message),
            can_delete=self.policy.can_delete_message(self.viewer, message),
            is_own=message.author_id == self.viewer.pk,
            text=self.markup.to_editable(message.body, users_by_pk, references),
            references=tuple(reference for reference in references.values() if reference is not None),
        )

    def _references(self, messages: list[Message]) -> dict[Any, ReferenceMap]:
        """Per message, each known ``kind:id`` token mapped to what the viewer may see (``None`` if nothing).

        Only tokens the service recorded a :class:`MessageReference` row for
        can resolve; anything else in a body renders as unavailable.
        """
        if not self.references:
            return {}
        tokens = {message.pk: self._known_tokens(message) for message in messages}
        referencing = [message for message in messages if tokens[message.pk]]
        if not referencing:
            return {}
        recorded = self._recorded_tokens(referencing)
        visible = self._visible_by_channel(referencing, recorded)
        return {
            message.pk: self._message_references(message, tokens[message.pk], recorded, visible)
            for message in referencing
        }

    def _known_tokens(self, message: Message) -> list[str]:
        return [token for token in self.markup.referenced_tokens(message.body) if self.references.knows(token)]

    @staticmethod
    def _recorded_tokens(messages: list[Message]) -> set[tuple[Any, str]]:
        rows = MessageReference.objects.filter(message__in=[message.pk for message in messages])
        return {(row.message_id, row.token) for row in rows}

    def _visible_by_channel(
        self, messages: list[Message], recorded: set[tuple[Any, str]]
    ) -> dict[Any, dict[str, Reference]]:
        channels = {message.channel_id: message.channel for message in messages}
        channel_of = {message.pk: message.channel_id for message in messages}
        tokens_by_channel: defaultdict[Any, set[str]] = defaultdict(set)
        for message_pk, token in recorded:
            tokens_by_channel[channel_of[message_pk]].add(token)
        return {
            channel_pk: self.references.visible(channels[channel_pk], self.viewer, tokens)
            for channel_pk, tokens in tokens_by_channel.items()
        }

    @staticmethod
    def _message_references(
        message: Message,
        tokens: list[str],
        recorded: set[tuple[Any, str]],
        visible: dict[Any, dict[str, Reference]],
    ) -> ReferenceMap:
        in_channel = visible.get(message.channel_id, {})
        return {token: in_channel.get(token) if (message.pk, token) in recorded else None for token in tokens}

    def _mentioned_users(self, messages: list[Message]) -> dict[str, Any]:
        pks: set[str] = set()
        for message in messages:
            pks |= self.markup.mentioned_pks(message.body)
        if not pks:
            return {}
        users = get_user_model()._default_manager.filter(pk__in=pks)
        return {str(user.pk): user for user in users}
