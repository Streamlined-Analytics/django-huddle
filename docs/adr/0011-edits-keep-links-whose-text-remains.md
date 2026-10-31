# ADR-0011: Edits keep links whose text remains, with numbered placeholders

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0008, ADR-0010
- **Supersedes:** the "editing drops unavailable references" consequence of ADR-0008

## Context

ADR-0008 accepted that editing a message whose reference the editor can no
longer see turns it into `#unavailable` text and drops it for everyone on
save. The product rule is: "if the @ or # is removed, the link is removed, if
they remain, the link remains". The same applied to mentions of people who are
no longer mentionable: the edit re-ran the normal rules and lost them.
Several hidden references in one message all looked the same
(`#unavailable`), so the server couldn't tell which one the editor had kept.

## Options considered

- **Carry existing tokens as hidden inputs.** Exact, but the client could post
  back any token. The server would still have to check each one against the
  message's existing tokens, and the hidden inputs would have to stay in sync
  with what the user deletes from the text. REST clients would need a new
  field. **Rejected.**
- **Numbered placeholders in the editable text** (chosen). The text is the
  single source of truth, and the HTML form, REST `PATCH` and host code all
  already send text.

## Decision

- `MessageMarkup.editable_forms()` gives each token in a body the text the
  editor's composer shows: `@Display Name`, `#code`, or for references the
  editor can't see, `#unavailable-1`, `#unavailable-2`… The number is assigned
  per distinct token in order of first appearance, so repeats share a number.
  `to_editable()` (and so `MessageView.text`, the REST `text` field and the
  edit form) uses these forms. The rendered HTML still shows a plain
  `#unavailable`.
- On edit, `ChatService.kept_links(user, message)` rebuilds those forms for
  the editor and keeps the links that are safe to keep: mentions of users
  who still exist, and references with a recorded `MessageReference` row. A
  form shared by two different tokens (two people with the same display name)
  is ambiguous and is not kept. That text goes through the normal rules
  instead.
- `ChatService.encode(kept=…)` substitutes kept forms ahead of every other
  rule, verbatim, with no permission re-check: the link was already there and
  the editor is only leaving it in place. Everything else follows the usual
  rules: picks, exact names, handles and resolver lookups with permission
  checks.
- Kept references keep their rows; references whose text was removed lose
  theirs. Kept links aren't new, so they send no `reference_added` or
  `user_mentioned`.
- Forging stays impossible: kept forms only come from tokens already on that
  message, hand-typed `<@…>`/`<#…>` are still neutralised, and typing
  `#unavailable-1` in a message without that placeholder is just text.
- The HTML edit form only pre-picks mentions of still-mentionable people.
  Pre-picking someone who is no longer mentionable failed form validation; the
  keep rule now covers them.

## Consequences

- Mention rows are not deleted when an `@Name` is removed in an edit (the
  existing behaviour); only reference rows track the body exactly.
- Placeholder numbers depend on what the editor can see, so two editors with
  different access can see different numbers. Each edit is checked against
  the forms of its own editor, so this is consistent.
