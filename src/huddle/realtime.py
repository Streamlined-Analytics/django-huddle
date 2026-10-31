"""Publishing chat events to Django Channels groups.

Events carry ids, never rendered HTML: each websocket connection re-reads the
message and re-checks the policy for its own user before rendering, so a
broadcast can never show someone a message they may not see.
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger(__name__)

MESSAGE_EVENT = "huddle.message"
CHANNEL_EVENT = "huddle.channel"


def channel_group(channel_id: Any) -> str:
    """Group every connection viewing a channel subscribes to."""
    return f"huddle.channel.{channel_id}"


def user_group(user_id: Any) -> str:
    """Group every connection of one user subscribes to (sidebar and DM updates)."""
    return f"huddle.user.{user_id}"


class Broadcaster:
    """Send huddle events to channel-layer groups. A no-op when no channel layer is configured."""

    def message_changed(self, *, action: str, message_id: Any, channel_id: Any, thread_id: Any | None) -> None:
        """Tell viewers of a channel that a message was created, updated or deleted."""
        self._send(
            channel_group(channel_id),
            {
                "type": MESSAGE_EVENT,
                "action": action,
                "message_id": message_id,
                "channel_id": channel_id,
                "thread_id": thread_id,
            },
        )

    def channel_activity(self, *, channel_id: Any, user_ids: list[Any]) -> None:
        """Tell users their sidebar entry for a channel changed (new message, new DM, membership)."""
        for user_id in user_ids:
            self._send(user_group(user_id), {"type": CHANNEL_EVENT, "channel_id": channel_id})

    def _send(self, group: str, event: dict[str, Any]) -> None:
        layer = get_channel_layer()
        if layer is None:
            logger.debug("huddle.broadcast.skipped", extra={"group": group, "reason": "no_channel_layer"})
            return
        async_to_sync(layer.group_send)(group, event)
