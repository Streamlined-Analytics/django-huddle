# Changelog

## 0.2.0 (unreleased)

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

## 0.1.0 (unreleased)

First release.

- Channels (public and private), direct and group messages, one-level threads, @mentions.
- `ChatPolicy` permission hook with scoped channels.
- `ChatService` for every write, with `message_posted`, `user_mentioned` and `thread_replied` signals.
- htmx UI built from overridable component templates.
- Websocket consumer (HTML or JSON mode) and `HuddleConsumerMixin`.
- Optional REST API (`django-huddle[api]`).
