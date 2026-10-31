# ADR-0001: Build our own chat app on Django Channels

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** none (first ADR for chat)

## Context

Comply 100 (a construction-compliance platform) needs Slack-style chat:
channels inside each project, direct messages, threaded replies, @mentions,
and later `#`-links to compliance items. Access must follow the host's own
project permissions exactly. We wanted to build on a strong existing package
rather than write chat from scratch.

## Options considered

- **django-realtime-chat-messaging.** This was the closest match: private,
  group and broadcast rooms, reactions and receipts. But it had a single
  0.1.0 release with no author or source repository listed. It pulls in
  django-guardian, django-polymorphic, JWT auth and Daphne, sanitises with
  the unmaintained `bleach`, and has no Slack-style reply threads. **Rejected**
  as too young and too heavy for something to depend on.
- **django-slack and djangobot.** These post to the real Slack rather than
  providing chat. **Not applicable.**
- **Zulip.** Mature and Django-based, but a whole separate server. Project
  membership would have to be synced into its channels through the API, its
  interface is separate from the host app's, and `#`-links to host objects
  would be awkward. **Rejected** as an integration burden larger than the
  feature.

## Decision

Write a focused, reusable app on **Django Channels** with our own models:
`Channel` (named or direct, optionally scoped to any host object through a
generic foreign key), `ChannelMembership` (with read state), `Message`
(with `parent` for one-level threads, and denormalised `reply_count` and
`last_reply_at`), `Mention` and `ThreadParticipant`. Every write goes through
one `ChatService`, which keeps counters and read state consistent and sends
signals on commit.

It is published as an MIT open-source package so other projects can depend
on it, with no ties to Comply 100.

## Consequences

- We own the maintenance: a small surface, but ours.
- No dependency beyond Django and Channels for the core; DRF is optional
  (ADR-0005).
