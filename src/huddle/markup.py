"""The stored message format.

Bodies are plain text plus two kinds of token:

- ``<@pk>`` — a mention of the user with that primary key.
- ``<#kind:id>`` — a reference to a host object, resolved by the
  :class:`~huddle.references.ReferenceResolver` registered for ``kind``.

The composer shows people as ``@Display Name`` and posts the chosen user ids
alongside the text; :meth:`MessageMarkup.encode` swaps each ``@Display Name``
for its token. A free-typed ``@handle`` and a ``#code`` are swapped too, once
the service has worked out who or what they name. Storing ids, not names or
codes, means renaming a user or renumbering an item never breaks old
messages. Rendering escapes every text segment, so bodies are XSS-safe without
an HTML sanitiser.

Hand-typed tokens are neutralised with a zero-width space, so neither a
mention nor a reference can be forged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from django.template.defaultfilters import linebreaksbr
from django.utils.html import format_html
from django.utils.html import urlize
from django.utils.safestring import SafeString
from django.utils.safestring import mark_safe

from huddle.references import Reference

MENTION_TOKEN = re.compile(r"<@([^>\s]+)>")
REFERENCE_TOKEN = re.compile(r"<#([^>\s]+)>")
TOKEN = re.compile(r"<@([^>\s]+)>|<#([^>\s]+)>")
FORGEABLE_TOKEN = re.compile(r"<([@#])([^>\s]+)>")

_AFTER_SPACE = r"(?<![^\s(\[])"
HANDLE = re.compile(_AFTER_SPACE + r"@(\w(?:[\w.\-]*\w)?)")
REFERENCE_CODE = re.compile(_AFTER_SPACE + r"#(\w(?:[\w.\-/]*\w)?)")

UNAVAILABLE_REFERENCE = "#unavailable"
SAFE_URL_SCHEMES = frozenset({"", "http", "https"})


def display_name(user: Any) -> str:
    """The name shown for ``user`` in messages and autocomplete."""
    full_name = (getattr(user, "get_full_name", lambda: "")() or getattr(user, "name", "") or "").strip()
    return full_name or str(user)


def mention_handles(user: Any) -> set[str]:
    """The lower-case ``@handles`` that mention ``user`` when typed: display name without spaces, email local part."""
    handles = {"".join(display_name(user).split()).lower()}
    email = str(getattr(user, "email", "") or "")
    handles.add(email.partition("@")[0].lower())
    return {handle for handle in handles if handle}


def _is_safe_url(url: str) -> bool:
    return urlsplit(url).scheme.lower() in SAFE_URL_SCHEMES


class MessageMarkup:
    """Encode composer text into stored bodies, and render bodies to HTML."""

    def encode(
        self,
        text: str,
        mentioned: Iterable[Any] = (),
        *,
        handles: Mapping[str, Any] | None = None,
        references: Mapping[str, Reference] | None = None,
    ) -> str:
        """Turn composer text into a stored body.

        - ``@Display Name`` for each ``mentioned`` user becomes ``<@pk>``,
          longer names first so "@Ann Lee" wins over "@Ann".
        - A free-typed ``@handle`` whose lower-case form is a key of
          ``handles`` becomes that user's token.
        - A ``#code`` that is a key of ``references`` becomes ``<#kind:id>``.
          Anything else stays plain text.

        Stray ``<@…>`` and ``<#…>`` typed by hand are neutralised first, so
        users cannot forge mentions or references.
        """
        body = self._replace_picked(self._neutralise(text), mentioned)
        if handles:
            body = HANDLE.sub(lambda match: self._handle_token(match, handles), body)
        if references:
            body = REFERENCE_CODE.sub(lambda match: self._reference_token(match, references), body)
        return body

    def handles(self, text: str, mentioned: Iterable[Any] = ()) -> set[str]:
        """Lower-case ``@handles`` in ``text`` that are not already covered by a picked ``mentioned`` user."""
        body = self._replace_picked(self._neutralise(text), mentioned)
        return {match.group(1).lower() for match in HANDLE.finditer(body)}

    def reference_codes(self, text: str) -> list[str]:
        """The distinct ``#codes`` in ``text``, in order of first appearance."""
        codes = (match.group(1) for match in REFERENCE_CODE.finditer(self._neutralise(text)))
        return list(dict.fromkeys(codes))

    def mentioned_pks(self, body: str) -> set[str]:
        """Primary keys (as strings) of the users mentioned in ``body``."""
        return set(MENTION_TOKEN.findall(body))

    def referenced_tokens(self, body: str) -> list[str]:
        """The distinct ``kind:id`` references in ``body``, in order of first appearance."""
        return list(dict.fromkeys(REFERENCE_TOKEN.findall(body)))

    def to_editable(
        self,
        body: str,
        users_by_pk: Mapping[str, Any],
        references: Mapping[str, Reference | None] | None = None,
    ) -> str:
        """Turn a stored body back into composer text.

        Mention tokens become ``@Display Name``. Reference tokens in
        ``references`` become ``#code``, or ``#unavailable`` when mapped to
        ``None`` (no longer visible). Tokens of unknown kinds are left as they are.
        """

        def replace_mention(match: re.Match[str]) -> str:
            user = users_by_pk.get(match.group(1))
            return f"@{display_name(user)}" if user is not None else "@unknown"

        text = MENTION_TOKEN.sub(replace_mention, body)
        if references is None:
            return text
        return REFERENCE_TOKEN.sub(lambda match: self._editable_reference(match, references), text)

    def render(
        self,
        body: str,
        users_by_pk: Mapping[str, Any],
        references: Mapping[str, Reference | None] | None = None,
    ) -> SafeString:
        """Render ``body`` as safe HTML: escaped text, links, line breaks, mention and reference chips.

        ``references`` maps ``kind:id`` to what this viewer may see; ``None``
        renders a neutral placeholder that reveals nothing about the object.
        Tokens missing from ``references`` (no resolver for their kind) render
        as literal text.
        """
        parts: list[str] = []
        position = 0
        for match in TOKEN.finditer(body):
            parts.append(self._render_text(body[position : match.start()]))
            parts.append(self._render_token(match, users_by_pk, references or {}))
            position = match.end()
        parts.append(self._render_text(body[position:]))
        return mark_safe("".join(parts))  # noqa: S308 - every part is escaped or format_html output

    @staticmethod
    def _neutralise(text: str) -> str:
        return FORGEABLE_TOKEN.sub(lambda match: f"<\u200b{match.group(1)}{match.group(2)}>", text)

    @staticmethod
    def _replace_picked(body: str, mentioned: Iterable[Any]) -> str:
        for user in sorted(mentioned, key=lambda person: len(display_name(person)), reverse=True):
            body = body.replace(f"@{display_name(user)}", f"<@{user.pk}>")
        return body

    @staticmethod
    def _handle_token(match: re.Match[str], handles: Mapping[str, Any]) -> str:
        user = handles.get(match.group(1).lower())
        return f"<@{user.pk}>" if user is not None else match.group(0)

    @staticmethod
    def _reference_token(match: re.Match[str], references: Mapping[str, Reference]) -> str:
        reference = references.get(match.group(1))
        return f"<#{reference.token}>" if reference is not None else match.group(0)

    @staticmethod
    def _editable_reference(match: re.Match[str], references: Mapping[str, Reference | None]) -> str:
        token = match.group(1)
        if token not in references:
            return match.group(0)
        reference = references[token]
        return f"#{reference.code}" if reference is not None else UNAVAILABLE_REFERENCE

    def _render_text(self, text: str) -> str:
        if not text:
            return ""
        return str(linebreaksbr(urlize(text, nofollow=True, autoescape=True), autoescape=False))

    def _render_token(
        self,
        match: re.Match[str],
        users_by_pk: Mapping[str, Any],
        references: Mapping[str, Reference | None],
    ) -> str:
        mention_pk, reference_token = match.groups()
        if reference_token is not None:
            return self._render_reference(match, references)
        user = users_by_pk.get(mention_pk)
        if user is None:
            return '<span class="huddle-mention huddle-mention--unknown">@unknown</span>'
        return format_html(
            '<span class="huddle-mention" data-user="{}">@{}</span>',
            user.pk,
            display_name(user),
        )

    def _render_reference(self, match: re.Match[str], references: Mapping[str, Reference | None]) -> str:
        token = match.group(2)
        if token not in references:
            return self._render_text(match.group(0))
        reference = references[token]
        if reference is None:
            return f'<span class="huddle-reference huddle-reference--unavailable">{UNAVAILABLE_REFERENCE}</span>'
        if reference.url and _is_safe_url(reference.url):
            return format_html(
                '<a class="huddle-reference" href="{}" data-reference="{}">{}</a>',
                reference.url,
                reference.token,
                reference.label,
            )
        return format_html(
            '<span class="huddle-reference" data-reference="{}">{}</span>', reference.token, reference.label
        )
