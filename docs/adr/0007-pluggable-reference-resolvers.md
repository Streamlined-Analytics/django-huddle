# ADR-0007: `#` references resolve through pluggable, per-kind resolvers

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0004, ADR-0002

## Context

ADR-0004 reserved `<#kind:id>` tokens for links to host objects. The first
host, Comply 100, wants `#B3.2` to link to a Schedule 1 requirement and
`#0001` to a compliance risk, both scoped to the channel's project. The package
knows nothing about requirements or risks, so the host has to supply the
lookup, and it must be scoped and permission-checked.

## Options considered

- **One host callback** (`resolve(code) -> object`). Simple, but it mixes every
  kind of object into one function, has no way to autocomplete, and gives no
  bulk lookup for rendering. **Rejected.**
- **A model registry** (list models and a code field; the package queries them).
  It can't express the host's permission rules or scoping without growing a
  second policy language. **Rejected.**
- **Resolver classes, one per kind** (chosen). Each answers `search`,
  `resolve_code` and `get` for a channel and a user, and decides itself what
  that user may see.

## Decision

- `huddle.references.ReferenceResolver` has a `kind` and three lookups:
  `search(channel, user, query, *, limit)`, `resolve_code(channel, user, code)`
  and `get(channel, user, ids)`. `code`, `label`, `url` and `object_id` have
  defaults (`str(obj)`, `#code`, `get_absolute_url()`, `pk`).
- Resolvers are listed in `HUDDLE_REFERENCE_RESOLVERS` and loaded into a cached
  `ReferenceRegistry`. Kinds must be unique and fit in a token
  (`[A-Za-z0-9_-]+`); a bad entry fails loudly with its own `HuddleError`.
- **Scoping** uses `channel.scope`. DMs and unscoped channels have
  `scope is None`; resolvers whose objects only exist inside a scope return
  nothing there. That's the host's call: some hosts will want global objects
  linkable from anywhere.
- **Conflicts.** A reference picked from autocomplete posts its exact
  `kind:id`, so it is never ambiguous. A free-typed code goes to the resolvers
  in settings order and the first that resolves it wins. We considered "two
  claims means plain text", as we do for handles (ADR-0009), but a mis-linked
  item is visible and fixable by the sender, and settings order gives the host
  a deterministic, documented lever. Wrongly mentioning a person, by contrast,
  notifies them.
- **No new `ChatPolicy` hook.** The policy decides who may see *chat* objects;
  resolvers decide who may see *host* objects. The package only asks a
  resolver after the policy has already admitted the user to the channel (the
  search endpoint 404s otherwise, the service checks `can_post`, rendering
  happens for viewers who passed `can_view`). A `can_reference` method on the
  policy would be a second place answering the same object-level question, and
  the two could drift. Hosts that want a policy-level switch can return nothing
  from their resolvers based on the policy.

## Consequences

- Hosts write one small class per kind. The example project's
  `example/chat.py` is the reference implementation the tests use.
- Resolvers run on every send that contains a `#code` (one lookup per code and
  resolver until one answers) and once per channel and kind per rendered page.
