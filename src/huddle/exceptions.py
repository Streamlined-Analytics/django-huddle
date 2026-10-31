"""Failure modes raised by django-huddle.

Each exception carries the values that caused it in ``context``, so a host can
log them as structured fields (``logger.warning("...", **exc.context)``).
"""

from __future__ import annotations

from typing import Any


class HuddleError(Exception):
    """Base for every django-huddle failure."""

    def __init__(self, message: str, **context: Any) -> None:
        super().__init__(message)
        self.context = context


class ChatPermissionDeniedError(HuddleError):
    """The user may not perform ``action`` on the target."""

    def __init__(self, action: str, user_id: Any, target_id: Any) -> None:
        super().__init__(
            f"User {user_id} may not {action} {target_id}.",
            action=action,
            user_id=user_id,
            target_id=target_id,
        )


class NestedThreadError(HuddleError):
    """A reply was posted to a message that is itself a reply. Threads are one level deep."""

    def __init__(self, parent_id: Any) -> None:
        super().__init__(f"Message {parent_id} is a reply; reply to its thread instead.", parent_id=parent_id)


class ThreadChannelMismatchError(HuddleError):
    """A reply named a parent message from a different channel."""

    def __init__(self, parent_id: Any, channel_id: Any) -> None:
        super().__init__(
            f"Message {parent_id} is not in channel {channel_id}.",
            parent_id=parent_id,
            channel_id=channel_id,
        )


class InvalidDirectMessageError(HuddleError):
    """A direct message was requested with no other participants."""

    def __init__(self, user_id: Any) -> None:
        super().__init__(f"User {user_id} asked for a direct message with nobody else.", user_id=user_id)
