# ADR-0002: One swappable ChatPolicy decides every permission

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0001

## Context

Hosts attach channels to their own objects and have their own access rules.
Spreading `if user …` checks across views, the API and the websocket would
let one path drift from the others, and a single missed check is a data leak.

## Decision

- **One class.** `huddle.policy.ChatPolicy`, selected by `HUDDLE_POLICY`,
  answers every question: visibility (`scope_q` / `filter_visible` /
  `can_view`), posting, creating, managing, editing, deleting, who can be
  mentioned, and who can be DM'd.
- **Visibility is a queryset filter**, not a per-object check, so lists and
  lookups share it. `Channel.objects.visible_to(user)` delegates to the policy.
  `scopes_q(Model, pks_queryset)` helps hosts express "channels of objects
  this user can see" as a subquery.
- **The default policy hides every scoped channel.** A host that forgets to
  override `scope_q` fails closed.
- **Invisible means 404.** Views, the API and the consumer look objects up
  through `visible_to`, so a non-member cannot tell a private channel exists.
  Rule violations on visible objects (for example, editing someone else's
  message) are 403.
- **The service re-checks.** `ChatService` enforces the policy itself, so host
  code calling it directly gets the same guarantees.

## Consequences

- Hosts implement one subclass.
- Visibility for a single object costs a query (`can_view` filters by pk).
  This is acceptable at chat scale, and it keeps one source of truth.
