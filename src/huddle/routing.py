"""Websocket routes. Add ``huddle.routing.websocket_urlpatterns`` to your ``URLRouter``."""

from __future__ import annotations

from django.urls import path

from huddle.consumers import HuddleConsumer

websocket_urlpatterns = [
    path("ws/huddle/", HuddleConsumer.as_asgi()),
]
