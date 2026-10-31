# Decision records

Architecture Decision Records capture *why* the project is the way it is. They
are written when the decision is made and are not rewritten afterwards; a
superseded decision gets a new record.

| ADR | Decision |
|---|---|
| [ADR-0001](0001-build-chat-on-channels.md) | Build our own chat app on Django Channels instead of adopting a package or Zulip |
| [ADR-0002](0002-single-chat-policy.md) | One swappable `ChatPolicy` decides every permission; invisible means 404 |
| [ADR-0003](0003-ids-not-html-over-the-channel-layer.md) | Broadcast ids, re-check and re-render per connection |
| [ADR-0004](0004-token-message-format.md) | Store bodies as escaped text plus `<@pk>` / `<#kind:id>` tokens |
| [ADR-0005](0005-overridable-components-and-api.md) | Per-component templates, an optional REST API and a JSON websocket mode |
| [ADR-0006](0006-django-6-floor.md) | Django 6.0 is the minimum supported version |
| [ADR-0007](0007-pluggable-reference-resolvers.md) | `#` references resolve through pluggable per-kind resolvers; settings order breaks ties; no new policy hook |
| [ADR-0008](0008-store-references-by-id-recheck-at-render.md) | Store references by id, record `MessageReference` rows, re-check visibility at render |
| [ADR-0009](0009-free-typed-mention-handles.md) | Free-typed `@handles` mention when exactly one mentionable user fits |
| [ADR-0010](0010-typed-full-name-mentions.md) | Typed full names mention; longest match first; ambiguity mentions nobody |
| [ADR-0011](0011-edits-keep-links-whose-text-remains.md) | Edits keep links whose text remains, using numbered `#unavailable-N` placeholders |
