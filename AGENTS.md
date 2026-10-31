# django-huddle — agent guidance

A public, MIT-licensed Django package providing Slack-style chat (channels, DMs,
threads, @mentions) over Django Channels. Other projects depend on it, so the
**public API is a maintenance promise**.

## Commands

```bash
uv run pytest                                  # quick run
uvx --with tox-uv tox run -f py314             # Django 6.0 + 6.1 matrix
uv run ruff format . && uv run ruff check --fix .
uv run mypy src/ tests/ example/
pre-commit run --all-files                     # before every commit
```

## Invariants

1. **Every permission decision goes through `ChatPolicy`** (ADR-0002). Never add
   an inline `if user ...` access check in a view, API view or consumer. Add or
   extend a policy method instead.
2. **Invisible means 404.** Look channels and messages up through
   `Channel.objects.visible_to(user)`.
3. **Every write goes through `ChatService`.** It keeps counters and read state
   consistent and sends signals and broadcasts on commit.
4. **Websocket events carry ids, never HTML** (ADR-0003). Each connection
   re-checks and re-renders for its own user.
5. **Three outputs, two builders** (ADR-0005). Templates use `ChatRenderer`
   view models; the REST API and websocket JSON use `ChatPayloads`. Keep them in
   step.
6. **Stored bodies are tokens plus plain text** (ADR-0004). Never store HTML.

## Style

- Django 6+, Python 3.12+, `from __future__ import annotations`, mypy strict.
- Query logic lives on QuerySets (`querysets.py`), with docstrings saying what
  each method selects and where it's used.
- Guard clauses, max nesting depth 2, intention-revealing names.
- Docstrings carry the why. `#` comments are one line at most. No comments in
  templates, JS or CSS unless essential.
- Each failure mode gets its own exception under `HuddleError`, carrying
  `context`.
- One template per UI component under `templates/huddle/components/`.
- Every PR carries an ADR in `docs/adr/` for each distinct decision.
