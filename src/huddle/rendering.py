"""Render messages and sidebar entries for one viewer.

Views and the websocket consumer share :class:`ChatRenderer`, so a message
looks the same whether it arrived with the page or over the socket, and the
per-viewer bits (edit/delete buttons, "you") are always computed for the user
who will see them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.utils.safestring import SafeString

from huddle.conf import get_policy
from huddle.markup import MessageMarkup
from huddle.markup import display_name
from huddle.models import Channel
from huddle.models import Message
from huddle.policy import ChatPolicy


@dataclass(frozen=True)
class MessageView:
    """Everything a template needs to draw one message for one viewer."""

    message: Message
    author_name: str
    html: SafeString
    can_edit: bool
    can_delete: bool
    is_own: bool

    @property
    def pk(self) -> Any:
        """The message's primary key."""
        return self.message.pk


@dataclass(frozen=True)
class ChannelEntry:
    """A sidebar row: a channel or DM, its label for this viewer, and unread count."""

    channel: Channel
    label: str
    unread_count: int
    is_member: bool


class ChatRenderer:
    """Build view models and HTML fragments for ``viewer``."""

    def __init__(self, viewer: Any, policy: ChatPolicy | None = None, markup: MessageMarkup | None = None) -> None:
        self.viewer = viewer
        self.policy = policy or get_policy()
        self.markup = markup or MessageMarkup()

    def message_views(self, messages: Iterable[Message]) -> list[MessageView]:
        """View models for ``messages``, resolving every mention in one query."""
        messages = list(messages)
        users_by_pk = self._mentioned_users(messages)
        return [self._message_view(message, users_by_pk) for message in messages]

    def message_view(self, message: Message) -> MessageView:
        """View model for a single message."""
        return self.message_views([message])[0]

    def editable_text(self, message: Message) -> str:
        """The composer text for editing ``message``."""
        return self.markup.to_editable(message.body, self._mentioned_users([message]))

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

    def _message_view(self, message: Message, users_by_pk: dict[str, Any]) -> MessageView:
        author = message.author
        return MessageView(
            message=message,
            author_name=display_name(author) if author is not None else "Deleted user",
            html=self.markup.render(message.body, users_by_pk),
            can_edit=self.policy.can_edit_message(self.viewer, message),
            can_delete=self.policy.can_delete_message(self.viewer, message),
            is_own=message.author_id == self.viewer.pk,
        )

    def _mentioned_users(self, messages: list[Message]) -> dict[str, Any]:
        pks: set[str] = set()
        for message in messages:
            pks |= self.markup.mentioned_pks(message.body)
        if not pks:
            return {}
        users = get_user_model()._default_manager.filter(pk__in=pks)
        return {str(user.pk): user for user in users}
