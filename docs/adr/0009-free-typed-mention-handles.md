# ADR-0009: Free-typed `@handles` mention when exactly one person fits

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0004

## Context

Mentions only counted when picked from autocomplete: the composer posts the
chosen ids and the server swaps `@Display Name` for `<@pk>`. People who type
`@benatkinson` without picking were surprised it stayed plain text, and API
clients had to look up ids for every mention.

## Decision

- After picked mentions are replaced, the service collects the remaining
  `@handles` (a word boundary, then letters, digits, `.`, `-`, `_`) and matches
  them case-insensitively against `ChatPolicy.mentionable_users(channel)`. A
  user's handles are their display name with spaces removed and their email
  local part.
- **Ambiguity links nobody.** If a handle fits more than one mentionable user,
  it stays plain text. Guessing would notify the wrong person, which is worse
  than not notifying.
- Handles match whole words, so `@bobjonesjr` never mentions "Bob Jones";
  longest-first ordering still applies to picked display names.
- Only mentionable users can match, so the policy still decides who can be
  mentioned. Forged `<@pk>` tokens stay neutralised.
- `HUDDLE_FREE_TYPED_MENTIONS = False` restores pick-only behaviour.

## Consequences

- Matching walks the mentionable users in Python, because display names come
  from `get_full_name()` and can't be filtered in SQL for every user model. It
  only runs when a message contains an unpicked `@handle`. Very large public
  channels may want a narrower `mentionable_users`.
- Email local parts become guessable handles. They are only ever matched, not
  shown.
