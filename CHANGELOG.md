# Changelog

## 0.2.2 (2026-10-31)

- The "No messages yet" placeholder now disappears as soon as the first
  message arrives, whether it was sent live by someone else or posted by you.
  A CSS rule hides it once a message follows it, and the after-swap handler
  also removes it for browsers without `:has()`.
- Fixed a flaky view test (`TestPages::test_pagination`, about 1 run in 30):
  it searched the page for two-character message bodies such as `m2`, which
  the random CSRF token sometimes contained. It now checks each message's
  element id. Test-only change; no package behaviour changed.

## 0.2.1 (2026-10-31)

- Typed full names mention: `@Ben Atkinson` (case-insensitive) mentions the one
  mentionable user with that display name, without picking. The longest name
  wins (`@Ann Lee` over `@Ann`), a name must end at a word boundary
  (`@Ann Leeds` is not Ann Lee), and a name shared by several people mentions
  nobody. Still governed by `HUDDLE_FREE_TYPED_MENTIONS` (on by default).
- The composer's `@` autocomplete keeps narrowing across spaces (`@Ben A`). A
  multi-word query with no match shows nothing rather than "No matches".
- Edits keep links whose text remains. An existing mention or reference whose
  `@Name`, `#code` or `#unavailable-N` text is still in the edited text is
  kept verbatim, even if the editor can no longer see it; removing the text
  removes the link and its `MessageReference` row. References the editor can't
  see now show as numbered `#unavailable-1`, `#unavailable-2`… in the edit box
  (previously a single `#unavailable` that was dropped on save). This applies
  to the HTML edit form, REST `PATCH` and `ChatService.edit_message()`. New:
  `ChatService.kept_links()`, `MessageMarkup.editable_forms()`,
  `ChatRenderer.editable_links()`.
- The HTML edit form only pre-picks mentions of people who are still
  mentionable, so editing a message that mentions someone who has since lost
  access no longer fails validation.

## 0.2.0 (2026-10-31)

- `#` references to host objects. Register `huddle.references.ReferenceResolver`
  subclasses in `HUDDLE_REFERENCE_RESOLVERS`; `#codes` are resolved for the
  sender and stored as `<#kind:id>` tokens, and each viewer's permission is
  re-checked at render time. Unknown or forbidden codes stay plain text and
  hand-typed tokens cannot forge a reference. With no resolvers configured,
  `#` stays plain text as before.
- `huddle.models.MessageReference` rows (migration `0002`), with
  `for_target()` and `visible_to()` lookups, and the `reference_added` signal.
- `#` autocomplete in the composer (`huddle:references`,
  `components/reference_results.html`), `GET /channels/{id}/references/` and
  `reference_ids` in the REST API, and a `references` list in message payloads
  (REST and websocket JSON).
- `ChatService.post_message()` and `edit_message()` take `references=`;
  `ChatService.encode()` exposes the encoding step. `MessageView` gains `text`
  and `references`.
- Free-typed `@handles`: the display name without spaces or the email local
  part mentions that user without picking; ambiguous handles mention nobody.
  Turn off with `HUDDLE_FREE_TYPED_MENTIONS = False`.

## 0.1.0 (2026-10-31)

First release.

- Channels (public and private), direct and group messages, one-level threads, @mentions.
- `ChatPolicy` permission hook with scoped channels.
- `ChatService` for every write, with `message_posted`, `user_mentioned` and `thread_replied` signals.
- htmx UI built from overridable component templates.
- Websocket consumer (HTML or JSON mode) and `HuddleConsumerMixin`.
- Optional REST API (`django-huddle[api]`).
