from __future__ import annotations

import pytest
from django.middleware import csrf
from django.test import override_settings
from django.urls import reverse

from huddle.models import Channel
from huddle.models import ChannelMembership
from huddle.models import Message

pytestmark = pytest.mark.django_db(transaction=True)

HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def alice_client(client, alice):
    client.force_login(alice)
    return client


@pytest.fixture
def bob_client(client, bob):
    client.force_login(bob)
    return client


def _shows(content: str, message: Message) -> bool:
    """Whether ``content`` renders ``message``, matched by its element id.

    Bodies like ``m2`` are not unique enough to search for: the page's random
    CSRF token contains any given two characters about 1.6% of the time.
    """
    return f'id="huddle-message-{message.pk}"' in content


def _assert_pages_by_two(service, general, alice, client) -> None:
    """Post five messages; the page shows the latest two, ``?before=`` the two before."""
    messages = [service.post_message(alice, general, f"m{n}") for n in range(5)]
    page = client.get(reverse("huddle:channel", args=[general.pk])).content.decode()
    assert [_shows(page, message) for message in messages] == [False, False, False, True, True]
    older = client.get(
        reverse("huddle:channel", args=[general.pk]), {"before": messages[3].pk}, **HTMX
    ).content.decode()
    assert [_shows(older, message) for message in messages] == [False, True, True, False, False]
    assert "Load older messages" in older


class TestAccess:
    def test_login_required(self, client, general):
        response = client.get(reverse("huddle:channel", args=[general.pk]))
        assert response.status_code == 302

    def test_private_channel_is_404_for_outsiders(self, service, alice, bob_client):
        private = service.create_channel(alice, "private", is_private=True)
        root = service.post_message(alice, private, "secret")
        for url in [
            reverse("huddle:channel", args=[private.pk]),
            reverse("huddle:mentions", args=[private.pk]),
            reverse("huddle:thread", args=[root.pk]),
            reverse("huddle:edit", args=[root.pk]),
        ]:
            assert bob_client.get(url).status_code == 404, url
        assert bob_client.post(reverse("huddle:post", args=[private.pk]), {"text": "x"}).status_code == 404
        assert bob_client.post(reverse("huddle:delete", args=[root.pk])).status_code == 404

    def test_service_denial_becomes_403(self, service, general, alice, bob_client):
        message = service.post_message(alice, general, "mine")
        assert bob_client.post(reverse("huddle:delete", args=[message.pk])).status_code == 403
        assert bob_client.get(reverse("huddle:edit", args=[message.pk])).status_code == 403


class TestPages:
    def test_home_empty_state(self, alice_client):
        response = alice_client.get(reverse("huddle:home"))
        assert response.status_code == 200
        assert b"Welcome to chat" in response.content

    def test_home_redirects_to_latest_channel(self, alice_client, general):
        response = alice_client.get(reverse("huddle:home"))
        assert response.url == reverse("huddle:channel", args=[general.pk])

    def test_channel_page_marks_read_and_lists_messages(self, service, general, alice, bob, alice_client):
        service.post_message(bob, general, "hello <b>world</b>")
        response = alice_client.get(reverse("huddle:channel", args=[general.pk]))
        content = response.content.decode()
        assert "hello &lt;b&gt;world&lt;/b&gt;" in content
        assert f'name="subscribe" value="{general.pk}"' in content
        assert ChannelMembership.objects.get(channel=general, user=alice).last_read_at is not None

    @override_settings(HUDDLE_PAGE_SIZE=2)
    def test_pagination(self, service, general, alice, alice_client):
        _assert_pages_by_two(service, general, alice, alice_client)

    @override_settings(HUDDLE_PAGE_SIZE=2)
    def test_pagination_ignores_csrf_token_text(self, monkeypatch, service, general, alice, alice_client):
        hostile_token = ("m0m1m2m3m4" * 7)[:64]
        monkeypatch.setattr(csrf, "_mask_cipher_secret", lambda secret: hostile_token)
        _assert_pages_by_two(service, general, alice, alice_client)

    def test_sidebar_unread_counts(self, service, general, alice, bob, alice_client):
        other = service.create_channel(alice, "other")
        service.post_message(bob, other, "unread one")
        service.post_message(bob, other, "unread two")
        content = alice_client.get(reverse("huddle:channel", args=[general.pk])).content.decode()
        assert '<span class="huddle-badge">2</span>' in content

    def test_direct_message_label(self, service, alice, bob, alice_client):
        dm = service.open_direct(alice, [bob])
        content = alice_client.get(reverse("huddle:channel", args=[dm.pk])).content.decode()
        assert "Bob Jones" in content


class TestPosting:
    def test_post_returns_composer_and_message(self, general, alice_client):
        response = alice_client.post(reverse("huddle:post", args=[general.pk]), {"text": "hi there"}, **HTMX)
        content = response.content.decode()
        assert 'id="huddle-composer"' in content
        assert f'hx-swap-oob="beforeend:#huddle-timeline-{general.pk}"' in content
        assert Message.objects.get().body == "hi there"

    def test_blank_post_shows_errors(self, general, alice_client):
        response = alice_client.post(reverse("huddle:post", args=[general.pk]), {"text": "  "}, **HTMX)
        assert "This field is required" in response.content.decode()
        assert not Message.objects.exists()

    @override_settings(HUDDLE_MAX_MESSAGE_LENGTH=5)
    def test_long_post_rejected(self, general, alice_client):
        alice_client.post(reverse("huddle:post", args=[general.pk]), {"text": "too long"}, **HTMX)
        assert not Message.objects.exists()

    def test_post_with_mention(self, general, alice_client, bob):
        alice_client.post(
            reverse("huddle:post", args=[general.pk]), {"text": "hi @Bob Jones", "mentions": [bob.pk]}, **HTMX
        )
        assert Message.objects.get().body == f"hi <@{bob.pk}>"

    def test_mention_search(self, general, alice_client, bob, carol):
        content = alice_client.get(reverse("huddle:mentions", args=[general.pk]), {"q": "@bo"}).content.decode()
        assert "Bob Jones" in content and "Carol White" not in content and "Alice Smith" not in content
        everyone = alice_client.get(reverse("huddle:mentions", args=[general.pk])).content.decode()
        assert "Carol White" in everyone
        nobody = alice_client.get(reverse("huddle:mentions", args=[general.pk]), {"q": "zzz"}).content.decode()
        assert "No matches" in nobody

    def test_mark_read(self, general, alice_client):
        assert alice_client.post(reverse("huddle:mark_read", args=[general.pk])).status_code == 204


class TestThreadsAndEditing:
    def test_thread_and_reply(self, service, general, alice, alice_client):
        root = service.post_message(alice, general, "question")
        panel = alice_client.get(reverse("huddle:thread", args=[root.pk]), **HTMX).content.decode()
        assert "question" in panel and 'id="huddle-reply-composer"' in panel
        response = alice_client.post(reverse("huddle:reply", args=[root.pk]), {"text": "answer"}, **HTMX)
        assert f"#huddle-thread-replies-{root.pk}" in response.content.decode()
        root.refresh_from_db()
        assert root.reply_count == 1

    def test_thread_of_reply_redirects_to_root(self, service, general, alice, alice_client):
        root = service.post_message(alice, general, "question")
        reply = service.post_message(alice, general, "answer", parent=root)
        response = alice_client.get(reverse("huddle:thread", args=[reply.pk]))
        assert response.url == reverse("huddle:thread", args=[root.pk])

    def test_blank_reply_shows_errors(self, service, general, alice, alice_client):
        root = service.post_message(alice, general, "question")
        response = alice_client.post(reverse("huddle:reply", args=[root.pk]), {"text": ""}, **HTMX)
        assert "This field is required" in response.content.decode()

    def test_edit_flow(self, service, general, alice, bob, alice_client):
        message = service.post_message(alice, general, "hi @Bob Jones", mentioned=[bob])
        form = alice_client.get(reverse("huddle:edit", args=[message.pk]), **HTMX).content.decode()
        assert "hi @Bob Jones" in form and f'value="{bob.pk}"' in form
        alice_client.post(reverse("huddle:edit", args=[message.pk]), {"text": "changed"}, **HTMX)
        message.refresh_from_db()
        assert message.body == "changed" and message.edited_at
        invalid = alice_client.post(reverse("huddle:edit", args=[message.pk]), {"text": ""}, **HTMX)
        assert "This field is required" in invalid.content.decode()
        cancel = alice_client.get(reverse("huddle:cancel_edit", args=[message.pk]), **HTMX)
        assert "changed" in cancel.content.decode()

    def test_delete(self, service, general, alice, alice_client):
        message = service.post_message(alice, general, "oops")
        response = alice_client.post(reverse("huddle:delete", args=[message.pk]), **HTMX)
        assert "This message was deleted." in response.content.decode()


class TestChannelsAndDirect:
    def test_create_channel(self, alice_client):
        assert alice_client.get(reverse("huddle:create_channel")).status_code == 200
        response = alice_client.post(reverse("huddle:create_channel"), {"name": "#Site Visits", "topic": ""})
        channel = Channel.objects.get()
        assert channel.name == "site-visits"
        assert response.url == reverse("huddle:channel", args=[channel.pk])

    def test_create_channel_needs_name(self, alice_client):
        response = alice_client.post(reverse("huddle:create_channel"), {"name": "#", "topic": ""})
        assert "Give the channel a name." in response.content.decode()

    def test_browse_join_leave(self, general, bob, bob_client):
        assert "#general" in bob_client.get(reverse("huddle:browse")).content.decode()
        bob_client.post(reverse("huddle:join", args=[general.pk]))
        assert general.memberships.filter(user=bob).exists()
        bob_client.post(reverse("huddle:leave", args=[general.pk]))
        assert not general.memberships.filter(user=bob).exists()

    def test_new_direct(self, alice_client, bob):
        assert "Bob" in alice_client.get(reverse("huddle:new_direct")).content.decode()
        response = alice_client.post(reverse("huddle:new_direct"), {"users": [bob.pk]})
        dm = Channel.objects.direct().get()
        assert response.url == reverse("huddle:channel", args=[dm.pk])
