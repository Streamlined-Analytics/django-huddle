# ADR-0003: Broadcast ids; re-check and re-render per connection

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0002

## Context

Real-time delivery needs to fan a new message out to every viewer. The obvious
shortcut is to render HTML once and broadcast it, but a message looks different
for each viewer (edit and delete buttons, "you"), and access can change between
subscribing and receiving. A pre-rendered broadcast would leak to someone who
was just removed.

## Decision

- Group events carry only ids: `{"type": "huddle.message", "action",
  "message_id", "channel_id", "thread_id"}`.
- Each connection re-reads the message, **re-checks `can_view` for its own
  user**, and renders for that user. If the check fails, the connection
  unsubscribes from the channel.
- Events are sent with `transaction.on_commit`, so a viewer never receives an
  id for a row it cannot read yet.
- Posting is done with ordinary HTTP forms or the API, not over the socket. The
  socket is push-only, apart from subscribe and unsubscribe.

## Consequences

- One query and one render per recipient per event. That is fine for team chat;
  very large public channels would need batching.
- Removing someone from a project or channel takes effect on the next event,
  with no explicit socket teardown.
