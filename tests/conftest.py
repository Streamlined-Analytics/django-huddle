from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth import get_user_model

from huddle import signals
from huddle.services import ChatService


@pytest.fixture
def make_user(db):
    counter = {"n": 0}

    def make(name: str | None = None, **extra: Any):
        counter["n"] += 1
        username = name or f"user{counter['n']}"
        first, _, last = username.partition(" ")
        return get_user_model().objects.create_user(
            username=username.replace(" ", "").lower(),
            email=f"{username.replace(' ', '').lower()}@example.com",
            password="pw",  # noqa: S106
            first_name=first,
            last_name=last,
            **extra,
        )

    return make


@pytest.fixture
def alice(make_user):
    return make_user("Alice Smith")


@pytest.fixture
def bob(make_user):
    return make_user("Bob Jones")


@pytest.fixture
def carol(make_user):
    return make_user("Carol White")


@pytest.fixture
def service():
    return ChatService()


@pytest.fixture
def general(service, alice):
    return service.create_channel(alice, "general")


@pytest.fixture
def received_signals():
    received: dict[str, list[dict[str, Any]]] = {"posted": [], "mentioned": [], "replied": []}

    def on_posted(**kwargs):
        received["posted"].append(kwargs)

    def on_mentioned(**kwargs):
        received["mentioned"].append(kwargs)

    def on_replied(**kwargs):
        received["replied"].append(kwargs)

    signals.message_posted.connect(on_posted)
    signals.user_mentioned.connect(on_mentioned)
    signals.thread_replied.connect(on_replied)
    yield received
    signals.message_posted.disconnect(on_posted)
    signals.user_mentioned.disconnect(on_mentioned)
    signals.thread_replied.disconnect(on_replied)
