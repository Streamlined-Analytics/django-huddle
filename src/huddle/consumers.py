"""Websocket support: live messages, thread replies and sidebar updates.

The browser subscribes to the channel it is viewing by sending
``{"subscribe": <channel id>}`` (htmx's ``ws-send`` does this from a hidden
form). Every incoming event is re-checked against the policy for this
connection's user and re-rendered for them. By default events go out as htmx
out-of-band swaps, so the client needs no rendering code; a custom frontend
connects with ``?format=json`` and receives ``{"type": "message.created",
"message": {...}}`` / ``{"type": "channel.updated", "channel": {...}}`` built
by :class:`~huddle.payloads.ChatPayloads`, the same shapes as the REST API.

:class:`HuddleConsumerMixin` holds the behaviour so a host with its own
multiplexed consumer can mix it in; :class:`HuddleConsumer` is the ready-made
consumer for everyone else.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from huddle.conf import get_policy
from huddle.models import Channel
from huddle.models import Message
from huddle.payloads import ChatPayloads
from huddle.realtime import channel_group
from huddle.realtime import user_group
from huddle.rendering import ChatRenderer

logger = logging.getLogger(__name__)

COMPONENTS = "huddle/components"
UNAUTHENTICATED_CLOSE_CODE = 4401


class HuddleConsumerMixin:
    """Chat behaviour for an ``AsyncJsonWebsocketConsumer``.

    Call :meth:`huddle_connect` once the user is known, :meth:`huddle_receive`
    with each JSON message, and :meth:`huddle_disconnect` on disconnect.
    """

    scope: dict[str, Any]
    channel_layer: Any
    channel_name: str
    huddle_subscriptions: set[int]
    huddle_json: bool
    send: Callable[..., Awaitable[None]]

    async def huddle_connect(self) -> None:
        """Join this user's personal group for sidebar updates."""
        self.huddle_subscriptions = set()
        query = parse_qs(self.scope.get("query_string", b"").decode())
        self.huddle_json = query.get("format") == ["json"]
        await self.channel_layer.group_add(user_group(self.scope["user"].pk), self.channel_name)

    async def huddle_disconnect(self) -> None:
        """Leave every group this connection joined."""
        await self.channel_layer.group_discard(user_group(self.scope["user"].pk), self.channel_name)
        for channel_id in getattr(self, "huddle_subscriptions", set()):
            await self.channel_layer.group_discard(channel_group(channel_id), self.channel_name)

    async def huddle_receive(self, content: dict[str, Any]) -> bool:
        """Handle a subscribe/unsubscribe request. Returns whether the message was for huddle."""
        if "subscribe" in content:
            await self._subscribe(content["subscribe"])
            return True
        if "unsubscribe" in content:
            await self._unsubscribe(content["unsubscribe"])
            return True
        return False

    async def huddle_message(self, event: dict[str, Any]) -> None:
        """A message was created, updated or deleted in a subscribed channel."""
        text = await self._message_event_text(event)
        if text is None:
            await self._unsubscribe(event["channel_id"])
            return
        await self.send(text_data=text)

    async def huddle_channel(self, event: dict[str, Any]) -> None:
        """A sidebar entry for this user changed."""
        text = await self._channel_event_text(event["channel_id"])
        if text:
            await self.send(text_data=text)

    async def _subscribe(self, raw_channel_id: Any) -> None:
        channel_id = _as_int(raw_channel_id)
        if channel_id is None or not await self._can_view(channel_id):
            logger.info("huddle.subscribe.denied", extra={"channel_id": raw_channel_id})
            return
        self.huddle_subscriptions.add(channel_id)
        await self.channel_layer.group_add(channel_group(channel_id), self.channel_name)

    async def _unsubscribe(self, raw_channel_id: Any) -> None:
        channel_id = _as_int(raw_channel_id)
        if channel_id is None:
            return
        self.huddle_subscriptions.discard(channel_id)
        await self.channel_layer.group_discard(channel_group(channel_id), self.channel_name)

    @database_sync_to_async
    def _can_view(self, channel_id: int) -> bool:
        channel = Channel.objects.filter(pk=channel_id).first()
        return channel is not None and get_policy().can_view(self.scope["user"], channel)

    @database_sync_to_async
    def _message_event_text(self, event: dict[str, Any]) -> str | None:
        user = self.scope["user"]
        message = Message.objects.for_display().filter(pk=event["message_id"]).select_related("parent").first()
        if message is None or not get_policy().can_view(user, message.channel):
            return None
        renderer = ChatRenderer(user)
        if self.huddle_json:
            payload = ChatPayloads(user, renderer).message(message)
            return json.dumps({"type": f"message.{event['action']}", "message": payload})
        context = {"message_view": renderer.message_view(message), "action": event["action"], "parent": message.parent}
        if message.parent is not None:
            context["parent_view"] = renderer.message_view(message.parent)
        return renderer.render_fragment(f"{COMPONENTS}/ws_message.html", context)

    @database_sync_to_async
    def _channel_event_text(self, channel_id: int) -> str:
        user = self.scope["user"]
        channel = (
            Channel.objects.visible_to(user)
            .with_membership_of(user)
            .filter(pk=channel_id, memberships__user=user)
            .prefetch_related("memberships__user")
            .first()
        )
        if channel is None:
            return ""
        renderer = ChatRenderer(user)
        entry = renderer.channel_entries([channel])[0]
        if self.huddle_json:
            return json.dumps({"type": "channel.updated", "channel": ChatPayloads(user, renderer).channel(entry)})
        is_current = channel_id in self.huddle_subscriptions
        return renderer.render_fragment(
            f"{COMPONENTS}/ws_sidebar_entry.html", {"entry": entry, "is_current": is_current}
        )


class HuddleConsumer(HuddleConsumerMixin, AsyncJsonWebsocketConsumer):  # type: ignore[misc]
    """Ready-made websocket consumer for chat. Route it at ``ws/huddle/``."""

    async def connect(self) -> None:
        """Accept signed-in users only."""
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=UNAUTHENTICATED_CLOSE_CODE)
            return
        await self.accept()
        await self.huddle_connect()

    async def disconnect(self, code: int) -> None:
        """Clean up group memberships."""
        user = self.scope.get("user")
        if user is not None and user.is_authenticated:
            await self.huddle_disconnect()

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        """Route subscribe/unsubscribe requests."""
        if isinstance(content, dict):
            await self.huddle_receive(content)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
