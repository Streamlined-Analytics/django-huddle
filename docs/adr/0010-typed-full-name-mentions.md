# ADR-0010: Typed full names mention, longest match first

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0009

## Context

ADR-0009 made typed single-token handles (`@benatkinson`, `@b.atkinson`)
mention people. The product rule is wider: "if someone types @ then it should
show autocomplete as they type, and if they type the exact match, it matches
up". Someone who types `@Ben Atkinson` in full without picking from
autocomplete expects a mention, and the autocomplete stopped narrowing at the
first space.

## Options considered

- **A separate pass for names, then handles.** This is simple, but an
  ambiguous full name (two "Ann Lee"s) would leave the text for the handle
  pass, which could then mention a different person called "Ann". **Rejected:**
  ambiguity must not fall back to a shorter match.
- **One matcher per `@`, longest key wins** (chosen).

## Decision

- The service collects the mentionable users' keys that could appear in the
  text: handles (ADR-0009) and the full display name in lower case with single
  spaces (`mention_name()`). Each key maps to its one owner, or to `None` if
  several users share it.
- `MessageMarkup.encode` visits each `@` that follows whitespace or an opening
  bracket. It takes the longest full-name key whose text matches there,
  case-insensitively, and that ends at a word boundary. If no name matches, it
  takes the single-token handle, captured greedily as before, so
  `@ann.lee` never matches "ann". A key that maps to `None` leaves the text
  untouched and stops shorter keys matching at that `@`.
- Picked mentions are still replaced first, forged tokens are still
  neutralised, and `HUDDLE_FREE_TYPED_MENTIONS` (on by default) covers both
  forms.
- The composer's `@` trigger now allows up to three space-separated words, so
  `@Ben A` keeps narrowing. A multi-word query with no match renders nothing,
  because it is usually ordinary text after a finished mention, and the CSS
  hides an empty results box.

## Consequences

- Display names are matched as typed with single spaces. A name stored with a
  double space only matches when typed that way.
- The cost is unchanged from ADR-0009: one pass over the mentionable users,
  and only when the text contains an unpicked `@word`.
