# ADR-0004: Store bodies as plain text plus tokens

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0001

## Context

Mentions must survive users being renamed. Comply 100 also wants `#`-links to
compliance items later. Storing HTML would need a sanitiser (`bleach` is
deprecated), and storing names would break on renames.

## Decision

- Bodies are plain text with `<@pk>` mention tokens. `<#kind:id>` is
  **reserved** for references and rendered as literal text until reference
  support ships, so bodies written now stay valid.
- The composer shows `@Display Name` and posts the chosen user ids. The server
  swaps names for tokens (longest name first) and keeps only users the policy
  says may be mentioned there. A typed `<@1>` is neutralised with a zero-width
  space, so mentions cannot be forged.
- Rendering escapes every text segment, then adds links and line breaks. Safety
  comes from construction, not sanitising.

## Consequences

- There's no rich-text formatting yet. Markdown can be added later as another
  escaping pass without changing what is stored.
