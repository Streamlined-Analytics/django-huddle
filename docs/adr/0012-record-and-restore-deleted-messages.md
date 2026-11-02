# ADR-0012: Record deleted messages so they can be audited and restored

- **Status:** Accepted
- **Date:** 2026-11-02
- **Builds on:** ADR-0002 (single chat policy), ADR-0008 (references by id)

## Context

Deleting a message blanked its body, removed its mention and reference rows
and stamped `deleted_at`. Nothing recorded what it said or who deleted it,
and nothing could undo it. A host (Comply 100) lets moderators delete other
people's messages and needs to confirm who deleted what, including thread
replies, and to undo a mistaken deletion.

## Decision

- **`MessageDeletion`** rows, one per deletion: the message, `deleted_by`,
  `deleted_at`, the encoded `body`, the mentioned user ids and the reference
  rows (`kind`, `object_id`, `content_type_id`) as they were, plus
  `restored_by` and `restored_at`. A message deleted, restored and deleted
  again has two rows. Replies are messages, so thread deletions are covered.
- **`ChatService.delete_message`** writes the row in the same transaction as
  the soft delete and returns it. The message itself is still blanked, so no
  existing render, API or search path can show deleted text.
- **`ChatService.restore_message(user, message)`** undoes the latest
  unrestored deletion: body back, `deleted_at` cleared, mention and reference
  rows recreated (users deleted since are skipped). It doesn't re-announce
  mentions or references; they were announced when the message was posted.
  Viewers are told through the usual `edited` broadcast.
- **Policy hooks.** `can_restore_message` (default: whoever manages the
  channel, and only for a deleted message) and `can_view_deletions` (default:
  whoever manages the channel) let hosts decide who audits and restores.
- **Signals** `message_deleted` and `message_restored`
  (`message`, `deletion`, `user`), sent on commit, for host audit trails.
- `huddle.message.deleted` / `huddle.message.restored` log lines.
- `MessageDeletion.objects.in_channels(channels)` lists deletions for an
  audit page; `.unrestored()` narrows to ones not yet undone.

## Alternatives considered

- **Keep the body on the message and hide it when `deleted_at` is set.** Every
  read path (templates, REST serializers, websocket payloads, host queries)
  would have to remember to hide it; one miss leaks deleted text.
- **Recording deletions in the host only, from a signal.** The body is gone
  by the time a post-commit signal fires, and the host can't see who deleted
  it without the service passing the user.
- **Restoring by re-encoding the body through `encode()`.** That would re-check
  mentions and references against today's visibility and could drop links
  the author legitimately made. Restoring the recorded rows puts the message
  back exactly as it was; render-time checks still apply (ADR-0008).

## Consequences

- Deleted text is kept indefinitely in `MessageDeletion`. Hosts that must
  purge it (for example on a data-erasure request) delete those rows.
- Hosts build their own audit page and restore button from the queryset,
  policy hooks and service method; huddle ships no UI for it.
