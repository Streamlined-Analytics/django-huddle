from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from huddle.models import Channel
from huddle.models import Message

pytestmark = pytest.mark.django_db(transaction=True)

API = "/api/chat"


@pytest.fixture
def api(alice):
    client = APIClient()
    client.force_authenticate(alice)
    return client


@pytest.fixture
def bob_api(bob):
    client = APIClient()
    client.force_authenticate(bob)
    return client


def test_requires_authentication(general):
    assert APIClient().get(f"{API}/channels/").status_code == 403


def test_list_and_browse(api, bob_api, general, service, alice):
    service.create_channel(alice, "private", is_private=True)
    mine = api.get(f"{API}/channels/").json()
    assert {channel["label"] for channel in mine} == {"#general", "#private"}
    assert bob_api.get(f"{API}/channels/").json() == []
    browse = bob_api.get(f"{API}/channels/", {"browse": "true"}).json()
    assert [(channel["name"], channel["is_member"]) for channel in browse] == [("general", False)]


def test_create_join_leave_read(api, bob_api):
    created = api.post(f"{API}/channels/", {"name": "#Site Visits", "topic": "visits"}, format="json")
    assert created.status_code == 201
    channel_id = created.json()["id"]
    assert created.json()["name"] == "site-visits"
    assert api.post(f"{API}/channels/", {"name": "#"}, format="json").status_code == 400
    assert bob_api.post(f"{API}/channels/{channel_id}/join/").json()["is_member"] is True
    assert bob_api.post(f"{API}/channels/{channel_id}/read/").status_code == 204
    assert bob_api.post(f"{API}/channels/{channel_id}/leave/").status_code == 204
    assert bob_api.get(f"{API}/channels/{channel_id}/").json()["is_member"] is False


def test_direct(api, bob):
    response = api.post(f"{API}/channels/direct/", {"user_ids": [str(bob.pk)]}, format="json")
    assert response.json()["kind"] == "direct"
    assert response.json()["label"] == "Bob Jones"
    assert api.post(f"{API}/channels/direct/", {"user_ids": ["999"]}, format="json").status_code == 400


def test_private_channel_is_404(bob_api, service, alice):
    private = service.create_channel(alice, "private", is_private=True)
    message = service.post_message(alice, private, "secret")
    assert bob_api.get(f"{API}/channels/{private.pk}/").status_code == 404
    assert bob_api.get(f"{API}/channels/{private.pk}/messages/").status_code == 404
    assert bob_api.get(f"{API}/messages/{message.pk}/").status_code == 404


def test_post_page_and_thread(api, general, bob):
    url = f"{API}/channels/{general.pk}/messages/"
    created = api.post(url, {"text": "hi @Bob Jones", "mention_ids": [str(bob.pk)]}, format="json").json()
    assert created["body"] == f"hi <@{bob.pk}>"
    assert created["text"] == "hi @Bob Jones"
    assert created["mention_ids"] == [str(bob.pk)]
    assert created["can_edit"] is True
    reply = api.post(url, {"text": "a reply", "parent_id": created["id"]}, format="json").json()
    assert reply["parent_id"] == created["id"]
    nested = api.post(url, {"text": "too deep", "parent_id": reply["id"]}, format="json")
    assert nested.status_code == 400
    page = api.get(url, {"limit": "abc"}).json()
    assert [message["id"] for message in page["results"]] == [created["id"]]
    assert page["results"][0]["reply_count"] == 1
    thread = api.get(f"{API}/messages/{created['id']}/replies/").json()
    assert [message["text"] for message in thread["results"]] == ["a reply"]
    assert api.post(f"{API}/messages/{created['id']}/read/").status_code == 204
    assert api.post(url, {"text": ""}, format="json").status_code == 400


def test_pagination(api, general, service, alice):
    messages = [service.post_message(alice, general, f"m{n}") for n in range(4)]
    url = f"{API}/channels/{general.pk}/messages/"
    first = api.get(url, {"limit": 2}).json()
    assert [message["text"] for message in first["results"]] == ["m2", "m3"]
    assert first["has_older"] is True
    older = api.get(url, {"limit": 2, "before": messages[2].pk}).json()
    assert [message["text"] for message in older["results"]] == ["m0", "m1"]


def test_edit_and_delete(api, bob_api, general, service, alice):
    message = service.post_message(alice, general, "original")
    assert bob_api.patch(f"{API}/messages/{message.pk}/", {"text": "hijack"}, format="json").status_code == 403
    edited = api.patch(f"{API}/messages/{message.pk}/", {"text": "changed"}, format="json").json()
    assert edited["text"] == "changed" and edited["edited_at"]
    assert api.delete(f"{API}/messages/{message.pk}/").status_code == 204
    deleted = api.get(f"{API}/messages/{message.pk}/").json()
    assert deleted["deleted_at"] and deleted["html"] == "" and deleted["can_delete"] is False


def test_mentionable(api, general, bob, carol):
    people = api.get(f"{API}/channels/{general.pk}/mentionable/", {"q": "bo"}).json()
    assert people == [{"id": bob.pk, "name": "Bob Jones"}]


@pytest.mark.parametrize("limit", ["0", "5000"])
def test_limit_is_bounded(api, general, limit):
    assert api.get(f"{API}/channels/{general.pk}/messages/", {"limit": limit}).status_code == 200


def test_message_too_long(api, general, settings):
    settings.HUDDLE_MAX_MESSAGE_LENGTH = 3
    response = api.post(f"{API}/channels/{general.pk}/messages/", {"text": "long"}, format="json")
    assert response.status_code == 400
    assert not Message.objects.exists()
    assert Channel.objects.count() == 1
