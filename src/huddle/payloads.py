"""JSON-ready representations of chat objects, for one viewer.

Shared by the REST API (``huddle.api``) and the websocket's JSON mode, so a
custom frontend sees exactly the same shapes from both. Built on
:class:`~huddle.rendering.ChatRenderer`, so per-viewer flags such as
``can_edit`` follow the configured policy. No Django REST Framework needed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from huddle.markup import display_name
from huddle.models import Message
from huddle.rendering import ChannelEntry
from huddle.rendering import ChatRenderer
from huddle.rendering import MessageView


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class ChatPayloads:
    """Turn messages, channels and users into plain dicts for ``viewer``."""

    def __init__(self, viewer: Any, renderer: ChatRenderer | None = None) -> None:
        self.viewer = viewer
        self.renderer = renderer or ChatRenderer(viewer)

    def person(self, user: Any) -> dict[str, Any] | None:
        """A user as ``{"id", "name"}``, or ``None`` for a deleted user."""
        if user is None:
            return None
        return {"id": user.pk, "name": display_name(user)}

    def messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """Several messages, resolving mentions and references in bulk."""
        return [self._message(view) for view in self.renderer.message_views(messages)]

    def message(self, message: Message) -> dict[str, Any]:
        """One message."""
        return self.messages([message])[0]

    def channels(self, entries: list[ChannelEntry]) -> list[dict[str, Any]]:
        """Sidebar entries as channel dicts."""
        return [self.channel(entry) for entry in entries]

    def channel(self, entry: ChannelEntry) -> dict[str, Any]:
        """One channel, with this viewer's label, membership and unread count."""
        channel = entry.channel
        return {
            "id": channel.pk,
            "kind": channel.kind,
            "name": channel.name,
            "label": entry.label,
            "topic": channel.topic,
            "is_private": channel.is_private,
            "is_archived": channel.is_archived,
            "is_member": entry.is_member,
            "unread_count": entry.unread_count,
            "last_message_at": _iso(channel.last_message_at),
            "created_at": _iso(channel.created_at),
        }

    def _message(self, view: MessageView) -> dict[str, Any]:
        message = view.message
        deleted = message.is_deleted
        return {
            "id": message.pk,
            "channel_id": message.channel_id,
            "parent_id": message.parent_id,
            "author": self.person(message.author),
            "body": message.body,
            "text": "" if deleted else view.text,
            "html": "" if deleted else str(view.html),
            "mention_ids": sorted(self.renderer.markup.mentioned_pks(message.body)),
            "references": [reference.as_dict() for reference in view.references],
            "created_at": _iso(message.created_at),
            "edited_at": _iso(message.edited_at),
            "deleted_at": _iso(message.deleted_at),
            "reply_count": message.reply_count,
            "last_reply_at": _iso(message.last_reply_at),
            "can_edit": view.can_edit,
            "can_delete": view.can_delete,
            "is_own": view.is_own,
        }
