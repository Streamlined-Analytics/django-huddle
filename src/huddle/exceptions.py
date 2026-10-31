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


class InvalidReferenceResolverError(HuddleError):
    """An entry in ``HUDDLE_REFERENCE_RESOLVERS`` is not a ``ReferenceResolver`` subclass."""

    def __init__(self, path: str, got: Any) -> None:
        super().__init__(
            f"HUDDLE_REFERENCE_RESOLVERS entry {path!r} must be a ReferenceResolver subclass, got {got!r}.",
            path=path,
            got=repr(got),
        )


class InvalidReferenceKindError(HuddleError):
    """A resolver's ``kind`` is empty or cannot appear inside a ``<#kind:id>`` token."""

    def __init__(self, resolver: str, kind: Any) -> None:
        super().__init__(
            f"Reference resolver {resolver} has kind {kind!r}; use letters, digits, '_' or '-'.",
            resolver=resolver,
            kind=repr(kind),
        )


class DuplicateReferenceKindError(HuddleError):
    """Two configured resolvers claim the same ``kind``."""

    def __init__(self, kind: str, resolvers: list[str]) -> None:
        super().__init__(
            f"Reference kind {kind!r} is claimed by more than one resolver: {', '.join(resolvers)}.",
            kind=kind,
            resolvers=resolvers,
        )


class UntokenisableReferenceError(HuddleError):
    """A resolver returned an object whose id cannot be stored in a ``<#kind:id>`` token."""

    def __init__(self, kind: str, object_id: str) -> None:
        super().__init__(
            f"Reference {kind}:{object_id!r} has an id with whitespace or '>' and cannot be stored.",
            kind=kind,
            object_id=object_id,
        )


class InvalidReferenceIdError(HuddleError):
    """A resolver could not look up these ids (for example a picked ``ticket:abc`` where ids are integers)."""

    def __init__(self, kind: str, object_ids: list[str]) -> None:
        super().__init__(
            f"Reference ids {object_ids!r} are not valid for kind {kind!r}.",
            kind=kind,
            object_ids=object_ids,
        )
