"""The stored message format.

Bodies are plain text plus two kinds of token:

- ``<@pk>`` — a mention of the user with that primary key.
- ``<#kind:id>`` — a reference to a host object. Reserved: rendered as plain
  text until reference support lands, so bodies written today stay valid.

The composer shows people as ``@Display Name`` and posts the chosen user ids
alongside the text; :meth:`MessageMarkup.encode` swaps each ``@Display Name``
for its token. Storing ids, not names, means renaming a user never breaks old
messages. Rendering escapes every text segment, so bodies are XSS-safe without
an HTML sanitiser.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any

from django.template.defaultfilters import linebreaksbr
from django.utils.html import format_html
from django.utils.html import urlize
from django.utils.safestring import SafeString
from django.utils.safestring import mark_safe

MENTION_TOKEN = re.compile(r"<@([^>\s]+)>")
TOKEN = re.compile(r"<@([^>\s]+)>|<#([^>\s]+)>")


def display_name(user: Any) -> str:
    """The name shown for ``user`` in messages and autocomplete."""
    full_name = (getattr(user, "get_full_name", lambda: "")() or getattr(user, "name", "") or "").strip()
    return full_name or str(user)


class MessageMarkup:
    """Encode composer text into stored bodies, and render bodies to HTML."""

    def encode(self, text: str, mentioned: Iterable[Any]) -> str:
        """Replace ``@Display Name`` for each ``mentioned`` user with a ``<@pk>`` token.

        Longer names are replaced first so "@Ann Lee" wins over "@Ann".
        Stray ``<@...>`` typed by hand is neutralised so users cannot forge mentions.
        """
        body = MENTION_TOKEN.sub(lambda match: f"<\u200b@{match.group(1)}>", text)
        for user in sorted(mentioned, key=lambda person: len(display_name(person)), reverse=True):
            body = body.replace(f"@{display_name(user)}", f"<@{user.pk}>")
        return body

    def mentioned_pks(self, body: str) -> set[str]:
        """Primary keys (as strings) of the users mentioned in ``body``."""
        return set(MENTION_TOKEN.findall(body))

    def to_editable(self, body: str, users_by_pk: Mapping[str, Any]) -> str:
        """Turn a stored body back into composer text (tokens become ``@Display Name``)."""

        def replace(match: re.Match[str]) -> str:
            user = users_by_pk.get(match.group(1))
            return f"@{display_name(user)}" if user is not None else "@unknown"

        return MENTION_TOKEN.sub(replace, body)

    def render(self, body: str, users_by_pk: Mapping[str, Any]) -> SafeString:
        """Render ``body`` as safe HTML: escaped text, links, line breaks and mention chips."""
        parts: list[str] = []
        position = 0
        for match in TOKEN.finditer(body):
            parts.append(self._render_text(body[position : match.start()]))
            parts.append(self._render_token(match, users_by_pk))
            position = match.end()
        parts.append(self._render_text(body[position:]))
        return mark_safe("".join(parts))  # noqa: S308 - every part is escaped or format_html output

    def _render_text(self, text: str) -> str:
        if not text:
            return ""
        return str(linebreaksbr(urlize(text, nofollow=True, autoescape=True), autoescape=False))

    def _render_token(self, match: re.Match[str], users_by_pk: Mapping[str, Any]) -> str:
        mention_pk, reference = match.groups()
        if reference is not None:
            return self._render_text(match.group(0))
        user = users_by_pk.get(mention_pk)
        if user is None:
            return '<span class="huddle-mention huddle-mention--unknown">@unknown</span>'
        return format_html(
            '<span class="huddle-mention" data-user="{}">@{}</span>',
            user.pk,
            display_name(user),
        )
