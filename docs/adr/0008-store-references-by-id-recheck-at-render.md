# ADR-0008: Store references by id, record them as rows, re-check them at render

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0004, ADR-0007

## Context

With resolvers in place (ADR-0007) we had to decide what a message stores, who
may create a reference, and what a viewer sees when they lose access to an
item after it was linked. ADR-0004's rules for mentions (ids not names,
neutralise hand-typed tokens, escape on render) were the starting point.

## Decision

- **Encoding happens in the service.** `ChatService.encode()` finds the
  `#codes` in the text, keeps picked `kind:id` tokens the sender may see
  (`get`), sends the rest to `resolve_code`, and hands the result to
  `MessageMarkup.encode`, which only substitutes. The markup stays free of
  database access. Picked references win over free-typed resolution for the
  same code, and a pick only applies to the code that object actually has, so
  a client cannot attach a misleading label.
- **Store ids.** `#B3.2` becomes `<#requirement:42>`; renumbering an item
  doesn't break old messages. Unknown or unauthorised codes stay literal text.
- **No forging.** A hand-typed `<#…>` is neutralised with a zero-width space,
  exactly like `<@…>`. This now applies even with no resolvers configured; it
  renders identically, so 0.1.0 output is unchanged.
- **Record rows.** `MessageReference(message, kind, object_id, content_type)`
  mirrors the tokens in the body and is kept in step on edit (stale rows
  deleted) and on delete. `reference_added` fires after commit, once per newly
  referenced object, so hosts can notify or show "discussed in chat".
- **Rows gate rendering.** A token only renders as a chip if a row exists for
  it. 0.1.0 did not neutralise `<#…>`, so old bodies may contain hand-typed
  tokens that would otherwise come alive the day a host adds a resolver for
  that kind. Requiring the row costs one query per page and also protects
  against host code writing bodies directly. We considered a data migration
  that neutralises old tokens instead; it rewrites every message and doesn't
  cover direct writes, so the row check won.
- **Re-check at render, in bulk.** `ChatRenderer` loads the rows for a page in
  one query and calls `get()` once per channel and kind for the viewer. A
  reference the viewer can't see (or one without a row) renders as a neutral
  `#unavailable` placeholder; its label never reaches the page, the REST
  payload or the websocket. Tokens whose kind has no resolver render as
  literal text, as in 0.1.0.
- **Payloads.** `MessageView` gains `text` and `references`, computed in the
  same bulk pass, so `ChatPayloads` no longer resolves editable text per
  message. Payloads add `references: [{kind, id, code, label, url}]`; the API
  accepts `reference_ids` and offers `GET /channels/{id}/references/`.
- Chip URLs are only linked for relative, `http` or `https` URLs.

## Consequences

- Editing a message whose reference the editor can no longer see turns it
  into `#unavailable` text, and saving drops that reference. That is the
  correct outcome for a user who lost access, but it changes the message for
  everyone.
- At most 50 unpicked codes per message are sent to the resolvers; picked
  references are not capped.
- Each page costs at most `1 + kinds` extra queries when it contains
  references, and none when it doesn't or no resolvers are configured.
