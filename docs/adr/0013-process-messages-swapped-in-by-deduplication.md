# ADR-0013: Process messages that de-duplication swaps in

- **Status:** Accepted
- **Date:** 2026-11-02
- **Builds on:** ADR-0003 (ids, not HTML, over the channel layer)

## Context

Someone who sends a message receives it twice: once in reply to their own
post and once as the channel broadcast. `huddleReplaceExisting` (an
`htmx:oobBeforeSwap` handler) stops the second copy being appended again: it
replaces the existing element with the incoming one and cancels htmx's swap.

Because htmx's swap is cancelled, htmx never processes the replacement. Its
`hx-*` attributes do nothing, so "Reply in thread", Edit and Delete on the
sender's newest message were dead until the page was reloaded. Other people
receive only the broadcast, so they were not affected. Found in Comply 100 by
replying in thread to a message just sent.

## Decision

`huddleReplaceExisting` calls `htmx.process(incoming)` after `replaceWith`.

## Alternatives considered

- **Return `shouldSwap = true` with an `outerHTML` swap on the existing
  element.** htmx would process the node, but the handler would have to
  rewrite the swap style and target that the out-of-band block chose, which
  is more fragile than one `process` call.
- **Stop sending the sender a second copy.** The broadcast is per channel
  group and the sender is in the group; filtering them out would need
  per-connection state in the consumer. The one-line client fix is smaller
  and also covers any other path that delivers a copy of a message already
  on the page.

## Consequences

- Anything else a host adds that swaps nodes in outside htmx must also call
  `htmx.process` on them.
- The test is a static check on `huddle.js`, like the existing placeholder
  test; there is no browser test suite in this package.
