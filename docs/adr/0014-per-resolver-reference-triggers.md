# ADR-0014: Each reference resolver chooses its trigger character

- **Status:** Accepted
- **Date:** 2026-11-05
- **Builds on:** ADR-0007 (pluggable reference resolvers), ADR-0008 (store
  references by id), ADR-0011 (edits keep links whose text remains)

## Context

`#` was the only way to type a reference. It was hard-coded in the markup
regexes (`REFERENCE_CODE`, `UNAVAILABLE_REFERENCE`), in the composer's
autocomplete pattern, and in the default `label()`. Comply 100 wants compliance
risks typed as `!SSP-0004` next to `#B3.2` items, so people can tell at a
glance which kind of object they're naming, and so autocomplete for one kind
isn't crowded out by the other. The package request was "let users of the
package designate the characters and what they're used for".

## Options considered

- **A setting mapping characters to resolver lists**
  (`HUDDLE_REFERENCE_TRIGGERS = {"!": [...]}`). It splits one resolver's
  configuration across two places, and a resolver could end up under two
  characters or none. **Rejected.**
- **A new token per trigger** (`<!risk:42>`). The kind already names the
  resolver, so the character adds nothing to storage. It would need a data
  migration, a second forgeable token to neutralise, and would freeze the
  trigger into every old message. **Rejected.**
- **A `trigger` class attribute on each resolver, `#` by default** (chosen).
  The resolver already owns how its objects are typed (`code`) and shown
  (`label`); the character in front of the code belongs with them.

## Decision

- `ReferenceResolver.trigger: ClassVar[str] = "#"`. `Reference` gains a
  `trigger` field and a `typed` property (`!SSP-0004`); `as_dict()` includes
  `trigger`. `label()` defaults to `trigger + code`.
- **Validation at registry build time.** A trigger must be one character that
  is not a word character or whitespace (`InvalidReferenceTriggerError`), and
  not `@` (mentions) or `<`/`>` (token delimiters)
  (`ReservedReferenceTriggerError`). Both carry the resolver path and trigger
  in `context`. Several resolvers may share a trigger; duplicate *kinds* are
  still the only collision between resolvers.
- **Stored tokens stay `<#kind:id>`** for every trigger. No migration; old
  messages render unchanged, and a host can change a trigger later and every
  message shows the new one on its next render or edit.
- **Lookups are per trigger.** `ReferenceRegistry.triggers` lists the distinct
  triggers in settings order. `search(..., trigger=)` and
  `resolve_codes(..., trigger=)` ask only resolvers with that trigger; settings
  order still breaks ties within a trigger (ADR-0007). `search` with no
  trigger asks everyone, which keeps the old REST API behaviour.
- **Parsing.** `MessageMarkup` takes the registry and builds one pattern:
  after whitespace, `(` or `[`, one of the configured triggers, then a code
  that starts and ends with a word character. `typed_references()` returns
  typed forms (`#T1`, `!M1`), and everything keyed by "what was typed"
  (`encode()`'s references, `KeptLinks.references`) now uses the typed form
  including the trigger, because `#T1` and `!T1` can name different objects.
- **Placeholders** use the kind's trigger: `!unavailable` when rendered,
  `!unavailable-N` in the edit box, so ADR-0011's keep-on-edit still works
  for every trigger. The trigger is escaped (`format_html`) because `&` or `"`
  are valid triggers.
- **Composer.** `composer_fields.html` renders the triggers into
  `data-reference-triggers`. `huddle.js` builds its pattern from them,
  escaping `\ ] ^ -` for the character class and using the `u` flag so a
  non-BMP trigger is one character. It sends `?trigger=` to the search view,
  and a pick is inserted with the trigger from `data-trigger`.

## Why `!` in prose is safe

A `!` trigger is the obvious worry: prose is full of `!`. A code has to follow
whitespace, `(` or `[` and start with a word character, so `done!`, `!!!`,
`really?!`, `wow!M1` and `! M1` never reach a resolver (tested by patching
`resolve_code` and asserting it is never called). A standalone word such as
`!important` is asked about, at most `MAX_RESOLVED_CODES` per message as
before, and stays plain text unless the resolver has an object with that code.

## Consequences

- Existing hosts need no change: every resolver defaults to `#`.
- `MessageMarkup`'s internals changed shape (`reference_codes` became
  `typed_references`; `encode()` keys include the trigger). It isn't
  documented as public API; the changelog lists the change.
- The example project gains `Milestone` and a `!M1` `MilestoneResolver`, the
  reference implementation for a second trigger.
