"""``#`` references from chat messages to the host project's own objects.

A host registers :class:`ReferenceResolver` subclasses in the
``HUDDLE_REFERENCE_RESOLVERS`` setting. Each resolver owns one ``kind`` (for
example ``"requirement"``) and answers three questions for a channel and a
user:

- :meth:`~ReferenceResolver.search` — what matches a partly typed ``#code``
  (composer autocomplete);
- :meth:`~ReferenceResolver.resolve_code` — which object a free-typed
  ``#code`` names, at send time;
- :meth:`~ReferenceResolver.get` — which of these stored ids the user may
  still see, at render time.

Messages store ``<#kind:id>`` tokens, never codes, so renumbering an item
doesn't break old messages. Every answer is scoped to the channel and the
user: resolvers usually look at ``channel.scope`` (the host object a channel
is attached to). Direct messages and unscoped channels have
``channel.scope is None``; a resolver that only makes sense inside a scope
should return nothing there.

Object visibility is the resolver's decision, because only the host knows its
objects. The chat side is already settled before a resolver is asked: the
package only resolves references for users the :class:`~huddle.policy.ChatPolicy`
lets read (or post in) the channel. See ADR-0007.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from collections.abc import Iterable
from collections.abc import Iterator
from dataclasses import dataclass
from dataclasses import field
from itertools import islice
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

from django.core.exceptions import ValidationError

from huddle.exceptions import DuplicateReferenceKindError
from huddle.exceptions import InvalidReferenceIdError
from huddle.exceptions import InvalidReferenceKindError
from huddle.exceptions import UntokenisableReferenceError

if TYPE_CHECKING:
    from huddle.models import Channel

KIND_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
OBJECT_ID_PATTERN = re.compile(r"[^>\s]+")


@dataclass(frozen=True)
class Reference:
    """One host object as a chat message shows it: a chip with a label and a link.

    ``target`` is the object the resolver returned. It is kept for signals and
    storage and is never serialised.
    """

    kind: str
    id: str
    code: str
    label: str
    url: str
    target: Any = field(default=None, compare=False, repr=False)

    @property
    def token(self) -> str:
        """The ``kind:id`` text stored inside ``<#…>`` and posted by the composer."""
        return f"{self.kind}:{self.id}"

    def as_dict(self) -> dict[str, str]:
        """The JSON shape used by the REST API and the websocket JSON mode."""
        return {"kind": self.kind, "id": self.id, "code": self.code, "label": self.label, "url": self.url}


class ReferenceResolver:
    """Turn ``#codes`` into host objects for one ``kind``. Subclass and register in settings.

    ``search``, ``resolve_code`` and ``get`` must only return objects ``user``
    may see in ``channel``. ``channel.scope`` is ``None`` in direct messages and
    unscoped channels. Ids arrive as strings.

    Override ``code``, ``label`` and ``url`` to control how an object is typed
    and shown; the defaults use ``str(obj)`` and ``obj.get_absolute_url()``.
    """

    kind: ClassVar[str] = ""

    def search(self, channel: Channel, user: Any, query: str, *, limit: int) -> Iterable[Any]:
        """Up to ``limit`` objects matching ``query`` (the text typed after ``#``) for autocomplete."""
        raise NotImplementedError

    def resolve_code(self, channel: Channel, user: Any, code: str) -> Any | None:
        """The object a free-typed ``#code`` names, or ``None`` if there is none the user may see."""
        raise NotImplementedError

    def get(self, channel: Channel, user: Any, ids: Collection[str]) -> Iterable[Any]:
        """The objects with these ids that ``user`` may see in ``channel``, in one query if possible."""
        raise NotImplementedError

    def object_id(self, obj: Any) -> str:
        """The id stored in the token. Defaults to the primary key."""
        return str(obj.pk)

    def code(self, obj: Any) -> str:
        """What people type after ``#`` to name ``obj`` (for example ``B3.2``)."""
        return str(obj)

    def label(self, obj: Any) -> str:
        """The text of the chip shown in messages and autocomplete."""
        return f"#{self.code(obj)}"

    def url(self, obj: Any) -> str:
        """Where the chip links to. Empty means no link."""
        get_absolute_url = getattr(obj, "get_absolute_url", None)
        return str(get_absolute_url()) if callable(get_absolute_url) else ""

    def describe(self, obj: Any) -> Reference:
        """``obj`` as a :class:`Reference`."""
        object_id = self.object_id(obj)
        if not OBJECT_ID_PATTERN.fullmatch(object_id):
            raise UntokenisableReferenceError(self.kind, object_id)
        return Reference(
            kind=self.kind,
            id=object_id,
            code=self.code(obj),
            label=self.label(obj),
            url=self.url(obj),
            target=obj,
        )


def _resolver_name(resolver: ReferenceResolver) -> str:
    return f"{type(resolver).__module__}.{type(resolver).__qualname__}"


class ReferenceRegistry:
    """The configured resolvers, in settings order, and the lookups that fan out to them.

    Settings order is the tie-break: when two resolvers both resolve the same
    free-typed code, the one listed first wins.
    """

    def __init__(self, resolvers: Iterable[ReferenceResolver] = ()) -> None:
        self.resolvers = list(resolvers)
        self._by_kind: dict[str, ReferenceResolver] = {}
        for resolver in self.resolvers:
            self._register(resolver)

    def __bool__(self) -> bool:
        return bool(self.resolvers)

    def _register(self, resolver: ReferenceResolver) -> None:
        kind = resolver.kind
        if not KIND_PATTERN.fullmatch(kind):
            raise InvalidReferenceKindError(_resolver_name(resolver), kind)
        if kind in self._by_kind:
            raise DuplicateReferenceKindError(kind, [_resolver_name(self._by_kind[kind]), _resolver_name(resolver)])
        self._by_kind[kind] = resolver

    def knows(self, token: str) -> bool:
        """Whether a ``kind:id`` token belongs to a configured resolver."""
        return token.partition(":")[0] in self._by_kind

    def search(self, channel: Channel, user: Any, query: str, *, limit: int) -> list[Reference]:
        """Autocomplete candidates from every resolver, in settings order, at most ``limit`` in all."""
        found: list[Reference] = []
        for resolver in self.resolvers:
            remaining = limit - len(found)
            if remaining <= 0:
                break
            matches = islice(resolver.search(channel, user, query, limit=remaining), remaining)
            found.extend(resolver.describe(obj) for obj in matches)
        return found

    def resolve_codes(self, channel: Channel, user: Any, codes: Iterable[str]) -> dict[str, Reference]:
        """Free-typed codes mapped to the reference the first willing resolver returns. Unknown codes are left out."""
        resolved: dict[str, Reference] = {}
        for code in codes:
            reference = self._resolve_code(channel, user, code)
            if reference is not None:
                resolved[code] = reference
        return resolved

    def _resolve_code(self, channel: Channel, user: Any, code: str) -> Reference | None:
        for resolver in self.resolvers:
            obj = resolver.resolve_code(channel, user, code)
            if obj is not None:
                return resolver.describe(obj)
        return None

    def visible(self, channel: Channel, user: Any, tokens: Iterable[str]) -> dict[str, Reference]:
        """The ``kind:id`` tokens ``user`` may see in ``channel``, one ``get`` call per kind.

        Tokens of unknown kinds, malformed tokens and anything the resolver
        does not return are left out. Picked tokens come from the client, so
        an id the resolver cannot even look up (``ValueError``, ``TypeError``
        or ``ValidationError``) raises :class:`InvalidReferenceIdError`, which
        the views and the API turn into a 400.
        """
        visible: dict[str, Reference] = {}
        for kind, ids in self._ids_by_kind(tokens):
            visible.update(self._visible_of_kind(channel, user, kind, ids))
        return visible

    def _ids_by_kind(self, tokens: Iterable[str]) -> Iterator[tuple[str, set[str]]]:
        grouped: dict[str, set[str]] = {}
        for token in tokens:
            kind, _, object_id = token.partition(":")
            if kind in self._by_kind and object_id:
                grouped.setdefault(kind, set()).add(object_id)
        return iter(grouped.items())

    def _visible_of_kind(self, channel: Channel, user: Any, kind: str, ids: set[str]) -> dict[str, Reference]:
        resolver = self._by_kind[kind]
        try:
            found = list(resolver.get(channel, user, ids))
        except (ValueError, TypeError, ValidationError) as error:
            raise InvalidReferenceIdError(kind, sorted(ids)) from error
        references = (resolver.describe(obj) for obj in found)
        return {reference.token: reference for reference in references if reference.id in ids}
