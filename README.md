# django-huddle

Slack-style team chat for Django: **channels, direct messages, threads and
@mentions**, live over [Django Channels](https://channels.readthedocs.io/).

- **Channels and DMs.** Public and private named channels, plus one-to-one and
  group direct messages (one conversation per group of people).
- **Threads.** Reply to any message in a side thread, as in Slack. Threads
  are one level deep and track reply counts, followers and read state.
- **@mentions.** Autocomplete offers only people who can read the channel.
  Mentions are stored by user id, so renaming someone never breaks old messages.
- **Live.** New messages, edits, deletions, thread replies and unread badges
  arrive over a websocket.
- **Your permissions.** One policy class decides every visibility and action
  check. Attach channels to your own objects (projects, teams) and say who can
  see them.
- **Bring your own frontend, or don't.** It ships a working htmx UI whose every
  piece is an overridable template. There's also a REST API, and a JSON
  websocket mode for React, mobile or anything else.

> **Status:** alpha. The public API may change before 1.0.

## Requirements

- Python 3.12+
- Django 6.0+
- Django Channels 4.2+ with a channel layer (Redis in production)
- Optional: Django REST Framework 3.16+ for the REST API

## Install

```bash
pip install django-huddle          # HTML UI + websocket
pip install "django-huddle[api]"   # plus the REST API
```

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "channels",
    "rest_framework",  # only for the REST API
    "huddle",
]
ASGI_APPLICATION = "myproject.asgi.application"
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": ["redis://localhost:6379/1"]},
    },
}
HUDDLE_BASE_TEMPLATE = "base.html"  # must define {% block content %} and load htmx + htmx-ext-ws
```

```python
# urls.py
urlpatterns = [
    path("chat/", include("huddle.urls")),
    path("api/chat/", include("huddle.api.urls")),  # optional
]
```

```python
# asgi.py
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from huddle.routing import websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns))),
    }
)
```

Then run `python manage.py migrate`.

## Permissions: the chat policy

Every decision goes through one `huddle.policy.ChatPolicy`. The default allows:

- public unscoped channels for every signed-in user;
- private channels and DMs for their members;
- **scoped channels for nobody**, until you say otherwise.

To attach channels to your own objects, subclass the policy:

```python
from django.db.models import Q
from huddle.policy import ChatPolicy


class ProjectChatPolicy(ChatPolicy):
    def scope_q(self, user):
        projects = Project.objects.visible_to(user).values("pk")
        return Q(scope_content_type__isnull=True) | self.scopes_q(Project, projects)

    def can_create_channel(self, user, scope):
        return scope is not None and user.has_perm("projects.manage", scope)

    def mentionable_users(self, channel):
        if channel.is_private or channel.scope is None:
            return super().mentionable_users(channel)
        return channel.scope.members.filter(is_active=True)

    def direct_message_candidates(self, user):
        return User.objects.filter(projects__in=Project.objects.visible_to(user)).exclude(pk=user.pk).distinct()
```

```python
HUDDLE_POLICY = "myapp.chat.ProjectChatPolicy"
```

Every lookup (HTML, API and websocket) goes through
`Channel.objects.visible_to(user)`, so a channel someone can't see returns a
**404**, never a 403 that would confirm it exists. Every websocket event is
re-checked for each viewer before it is sent, so losing access takes effect
immediately.

Overridable methods: `scope_q`, `filter_visible`, `can_view`, `can_post`,
`can_create_channel`, `can_manage_channel`, `can_edit_message`,
`can_delete_message`, `mentionable_users`, `direct_message_candidates`.

## Writing from your own code

```python
from huddle.services import ChatService

chat = ChatService()
channel = chat.create_channel(user, "site-visits", scope=project)
message = chat.post_message(user, channel, "Booked for Tuesday @Ann Lee", mentioned=[ann])
chat.post_message(ann, channel, "Thanks!", parent=message)  # a thread reply
dm = chat.open_direct(user, [ann, bob])
```

The service enforces the policy and raises
`huddle.exceptions.ChatPermissionDeniedError` (and friends). Each exception
has a `.context` dict for structured logging.

## Signals: notifications and integrations

Signals are sent **after the transaction commits**:

| Signal | Arguments |
|---|---|
| `huddle.signals.message_posted` | `message`, `channel`, `author` |
| `huddle.signals.user_mentioned` | `message`, `user` (once per newly mentioned user, including on edit) |
| `huddle.signals.thread_replied` | `message`, `thread`, `recipients` (the thread's followers, minus the author) |

```python
@receiver(user_mentioned)
def notify_mention(sender, message, user, **kwargs):
    notify([user], "chat.mention", target=message)
```

## Customising the UI

Every fragment is its own template under `huddle/components/`, and pages
extend `huddle/layout.html`. To restyle one piece, override one file in your
project's templates directory:

| Template | What it draws |
|---|---|
| `huddle/components/message.html` | One message, with its actions and thread summary |
| `huddle/components/composer.html`, `composer_fields.html` | The message box and @mention autocomplete |
| `huddle/components/sidebar.html`, `sidebar_entry.html` | Channel and DM list, unread badges |
| `huddle/components/thread.html` | The thread side panel |
| `huddle/components/channel_pane.html` | Channel header, timeline and composer |
| `huddle/layout.html` | The three-column chat layout |

Each message template receives a `message_view` with `message`,
`author_name`, `html` (safe, escaped), `can_edit`, `can_delete` and `is_own`.

The default stylesheet (`huddle/huddle.css`) is plain CSS with custom
properties (`--huddle-accent`, `--huddle-border`, …) for theming.

## REST API (`django-huddle[api]`)

| Method and path | Purpose |
|---|---|
| `GET /channels/` | Your channels and DMs (`?browse=true`: every visible named channel) |
| `POST /channels/` | Create a channel `{name, topic, is_private}` |
| `GET /channels/{id}/` | One channel, with `is_member` and `unread_count` |
| `POST /channels/direct/` | Open a DM `{user_ids: [...]}` |
| `POST /channels/{id}/join/` · `leave/` · `read/` | Membership and read state |
| `GET /channels/{id}/messages/?before=&limit=` | A page of top-level messages, oldest first |
| `POST /channels/{id}/messages/` | Post `{text, mention_ids, parent_id}` |
| `GET /channels/{id}/mentionable/?q=` | People who can be @mentioned |
| `GET`, `PATCH`, `DELETE /messages/{id}/` | Read, edit `{text, mention_ids}`, delete |
| `GET /messages/{id}/replies/` · `POST read/` | A thread, and marking it read |

Messages come back with `body` (stored tokens), `text` (editable, with
`@Name`), `html` (rendered and safe), `mention_ids`, `author`, the timestamps,
`reply_count`, and per-viewer `can_edit`, `can_delete` and `is_own`. To
attach API-created channels to your own objects, subclass
`huddle.api.views.ChannelViewSet` and override `get_scope()`.

## Websocket

Connect to `/ws/huddle/` (`HUDDLE_WEBSOCKET_URL`) and send
`{"subscribe": <channel id>}` / `{"unsubscribe": <channel id>}`.

- **HTML mode** (the default) sends htmx out-of-band swaps; the bundled UI
  needs no client-side rendering.
- **JSON mode** (`/ws/huddle/?format=json`) sends
  `{"type": "message.created" | "message.updated" | "message.deleted", "message": {...}}`
  and `{"type": "channel.updated", "channel": {...}}`, in the same shapes as
  the REST API.

If you already run your own consumer (for example, one socket per page that
also carries notifications), mix in `huddle.consumers.HuddleConsumerMixin`,
then call `huddle_connect()`, `huddle_receive(content)` and
`huddle_disconnect()`.

## Message format

Bodies are stored as plain text with tokens: `<@user-pk>` for mentions, and
`<#kind:id>` reserved for future references to your own objects. Rendering
escapes everything else, so there's no HTML sanitiser to keep patched.

## Settings

| Setting | Default |
|---|---|
| `HUDDLE_POLICY` | `"huddle.policy.ChatPolicy"` |
| `HUDDLE_BASE_TEMPLATE` | `"huddle/base.html"` (loads htmx from unpkg; replace it in production) |
| `HUDDLE_PAGE_SIZE` | `50` |
| `HUDDLE_MAX_MESSAGE_LENGTH` | `10000` |
| `HUDDLE_WEBSOCKET_URL` | `"/ws/huddle/"` |

## Development

```bash
uv sync
uv run pytest
uv run mypy src/ tests/ example/
uv run ruff check . && uv run ruff format --check .
uv run python manage.py migrate && uv run daphne example.asgi:application   # the example project
```

Design decisions are recorded in [`docs/adr/`](docs/adr/).

## License

MIT
