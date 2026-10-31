# ADR-0005: Per-component templates, an optional REST API, and a JSON websocket mode

- **Status:** Accepted
- **Date:** 2026-10-31
- **Builds on:** ADR-0003

## Context

The first cut kept every fragment as a Django 6 template partial in one
`chat.html`. That reads well, but to restyle a single piece (say, a message) a
host had to copy and maintain the whole file. Some users will also want their
own frontend entirely (React, mobile) and need data, not HTML.

## Decision

- **One template per component** under `huddle/components/`, plus page
  templates extending `huddle/layout.html`, which extends
  `HUDDLE_BASE_TEMPLATE`. Overriding one piece means overriding one small file.
  Views and the websocket render the same component templates.
- **Templates get view models, not raw models.** `ChatRenderer` builds
  `MessageView` and `ChannelEntry` with per-viewer flags, so templates contain
  no permission logic.
- **The REST API is an optional extra** (`django-huddle[api]`, DRF). DRF
  serializers validate input only; responses come from `ChatPayloads`, a
  DRF-free builder.
- **JSON websocket mode** (`?format=json`) uses the same `ChatPayloads`
  shapes, so a custom frontend sees identical objects from REST and the
  socket. It works without DRF installed.
- `HuddleConsumerMixin` lets hosts fold chat into their own consumer.

## Consequences

- There are three output paths (templates, REST and websocket JSON). They stay
  in step through two shared builders (`ChatRenderer`, `ChatPayloads`) and the
  one service.
