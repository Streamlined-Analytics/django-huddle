# Changelog

## 0.1.0 (unreleased)

First release.

- Channels (public and private), direct and group messages, one-level threads, @mentions.
- `ChatPolicy` permission hook with scoped channels.
- `ChatService` for every write, with `message_posted`, `user_mentioned` and `thread_replied` signals.
- htmx UI built from overridable component templates.
- Websocket consumer (HTML or JSON mode) and `HuddleConsumerMixin`.
- Optional REST API (`django-huddle[api]`).
