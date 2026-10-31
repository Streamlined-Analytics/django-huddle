from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator

from huddle.consumers import UNAUTHENTICATED_CLOSE_CODE
from huddle.routing import websocket_urlpatterns
from huddle.services import ChatService

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]

application = URLRouter(websocket_urlpatterns)


async def connect(user):
    communicator = WebsocketCommunicator(application, "/ws/huddle/")
    communicator.scope["user"] = user
    connected, code = await communicator.connect()
    return communicator, connected, code


async def settle(communicator):
    """Give the consumer time to process subscriptions before anything is posted."""
    assert await communicator.receive_nothing(timeout=0.5)


@sync_to_async
def make_channel(user, **kwargs):
    return ChatService().create_channel(user, "general", **kwargs)


@sync_to_async
def post(user, channel, text, **kwargs):
    return ChatService().post_message(user, channel, text, **kwargs)


async def test_anonymous_rejected():
    from django.contrib.auth.models import AnonymousUser  # noqa: PLC0415

    _, connected, code = await connect(AnonymousUser())
    assert not connected
    assert code == UNAUTHENTICATED_CLOSE_CODE


async def test_subscriber_receives_rendered_message(alice, bob):
    channel = await make_channel(alice)
    communicator, connected, _ = await connect(bob)
    assert connected
    await communicator.send_json_to({"subscribe": str(channel.pk), "HEADERS": {}})
    await settle(communicator)
    await post(alice, channel, "hello bob")
    html = await communicator.receive_from()
    assert f"beforeend:#huddle-timeline-{channel.pk}" in html
    assert "hello bob" in html
    assert "Edit" not in html
    await communicator.disconnect()


async def test_reply_updates_thread_and_summary(alice):
    channel = await make_channel(alice)
    root = await post(alice, channel, "question")
    communicator, _, _ = await connect(alice)
    await communicator.send_json_to({"subscribe": channel.pk})
    await settle(communicator)
    await post(alice, channel, "answer", parent=root)
    messages = [await communicator.receive_from(), await communicator.receive_from()]
    reply_html = next(html for html in messages if "thread-replies" in html)
    assert f"#huddle-thread-summary-{root.pk}" in reply_html
    assert "1 reply" in reply_html
    assert any(f"huddle-entry-{channel.pk}" in html for html in messages)
    await communicator.disconnect()


async def test_cannot_subscribe_to_private_channel(alice, bob):
    channel = await make_channel(alice, is_private=True)
    communicator, _, _ = await connect(bob)
    await communicator.send_json_to({"subscribe": channel.pk})
    await communicator.send_json_to({"subscribe": "not-a-number"})
    await settle(communicator)
    await post(alice, channel, "secret")
    assert await communicator.receive_nothing()
    await communicator.disconnect()


async def test_losing_access_stops_delivery(alice, bob):
    channel = await make_channel(alice, is_private=True)
    await sync_to_async(ChatService().add_members)(alice, channel, [bob])
    communicator, _, _ = await connect(bob)
    await communicator.send_json_to({"subscribe": channel.pk})
    await settle(communicator)
    await sync_to_async(channel.memberships.filter(user=bob).delete)()
    await post(alice, channel, "after removal")
    assert await communicator.receive_nothing()
    await communicator.disconnect()


async def test_unsubscribe_and_unknown_messages(alice):
    channel = await make_channel(alice)
    communicator, _, _ = await connect(alice)
    await communicator.send_json_to({"subscribe": channel.pk})
    await communicator.send_json_to({"unsubscribe": channel.pk})
    await communicator.send_json_to({"unsubscribe": None})
    await communicator.send_json_to({"something": "else"})
    await communicator.send_json_to(["not", "a", "dict"])
    await settle(communicator)
    await post(alice, channel, "quiet")
    html = await communicator.receive_from()
    assert "huddle-entry-" in html
    assert await communicator.receive_nothing()
    await communicator.disconnect()


async def test_json_mode(alice):
    import json  # noqa: PLC0415

    channel = await make_channel(alice)
    communicator = WebsocketCommunicator(application, "/ws/huddle/?format=json")
    communicator.scope["user"] = alice
    await communicator.connect()
    await communicator.send_json_to({"subscribe": channel.pk})
    await settle(communicator)
    await post(alice, channel, "json please")
    events = [json.loads(await communicator.receive_from()), json.loads(await communicator.receive_from())]
    by_type = {event["type"]: event for event in events}
    assert by_type["message.created"]["message"]["text"] == "json please"
    assert by_type["channel.updated"]["channel"]["id"] == channel.pk
    await communicator.disconnect()
