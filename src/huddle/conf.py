"""Settings for django-huddle, with defaults.

``HUDDLE_POLICY``
    Dotted path to a :class:`~huddle.policy.ChatPolicy` subclass.
``HUDDLE_BASE_TEMPLATE``
    Template the chat pages extend. It must define a ``content`` block.
``HUDDLE_PAGE_SIZE``
    Messages loaded per page of channel history.
``HUDDLE_MAX_MESSAGE_LENGTH``
    Longest message body accepted, in characters.
``HUDDLE_WEBSOCKET_URL``
    Path the browser opens its chat websocket on.
"""

from __future__ import annotations

import functools
from typing import Any

from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from huddle.policy import ChatPolicy

DEFAULTS: dict[str, Any] = {
    "HUDDLE_POLICY": "huddle.policy.ChatPolicy",
    "HUDDLE_BASE_TEMPLATE": "huddle/base.html",
    "HUDDLE_PAGE_SIZE": 50,
    "HUDDLE_MAX_MESSAGE_LENGTH": 10_000,
    "HUDDLE_WEBSOCKET_URL": "/ws/huddle/",
}


def get_setting(name: str) -> Any:
    """Return a huddle setting, falling back to its default."""
    return getattr(settings, name, DEFAULTS[name])


@functools.cache
def get_policy() -> ChatPolicy:
    """Return the configured chat policy instance (cached until settings change)."""
    policy_class = import_string(get_setting("HUDDLE_POLICY"))
    if not (isinstance(policy_class, type) and issubclass(policy_class, ChatPolicy)):
        msg = f"HUDDLE_POLICY must point at a ChatPolicy subclass, got {policy_class!r}."
        raise TypeError(msg)
    return policy_class()


@receiver(setting_changed)
def _reset_policy(*, setting: str, **kwargs: Any) -> None:
    if setting == "HUDDLE_POLICY":
        get_policy.cache_clear()
