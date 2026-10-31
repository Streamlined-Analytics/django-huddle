from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from example.chat import ProjectChatPolicy
from example.chat import TicketResolver
from example.models import Project
from example.models import Ticket
from huddle import signals
from huddle.conf import get_reference_registry
from huddle.exceptions import DuplicateReferenceKindError
from huddle.exceptions import InvalidReferenceIdError
from huddle.exceptions import InvalidReferenceKindError
from huddle.exceptions import InvalidReferenceResolverError
from huddle.exceptions import UntokenisableReferenceError
from huddle.markup import MessageMarkup
from huddle.models import Message
from huddle.models import MessageReference
from huddle.references import Reference
from huddle.references import ReferenceRegistry
from huddle.references import ReferenceResolver
from huddle.rendering import ChatRenderer
from huddle.routing import websocket_urlpatterns
from huddle.services import ChatService

pytestmark = pytest.mark.django_db(transaction=True)

markup = MessageMarkup()
HTMX = {"HTTP_HX_REQUEST": "true"}
TICKETS = "example.chat.TicketResolver"


class ConfidentialTicketResolver(TicketResolver):
    """Tickets titled ``[confidential] …`` are visible to staff only."""

    def tickets(self, channel, user):
        tickets = super().tickets(channel, user)
        return tickets if user.is_staff else tickets.exclude(title__startswith="[confidential]")


class ShadowResolver(ReferenceResolver):
    """A second resolver that claims the same codes as tickets, to test conflicts."""

    kind = "shadow"

    def search(self, channel, user, query, *, limit):
        return [SimpleNamespace(pk=f"s-{query}")]

    def resolve_code(self, channel, user, code):
        return SimpleNamespace(pk=f"s-{code}") if code.startswith("T") else None

    def get(self, channel, user, ids):
        return [SimpleNamespace(pk=object_id) for object_id in ids]

    def code(self, obj):
        return obj.pk.removeprefix("s-")


class CodeIdTicketResolver(TicketResolver):
    """Stores the ticket code rather than its primary key."""

    def object_id(self, obj):
        return obj.code

    def get(self, channel, user, ids):
        return self.tickets(channel, user).filter(code__in=ids)


class SpacedIdResolver(ShadowResolver):
    kind = "spaced"

    def object_id(self, obj):
        return "has space"


@pytest.fixture(autouse=True)
def project_chat(settings):
    settings.HUDDLE_POLICY = "example.chat.ProjectChatPolicy"
    settings.HUDDLE_REFERENCE_RESOLVERS = [TICKETS]


@pytest.fixture
def project(alice, bob):
    project = Project.objects.create(name="Tower")
    project.members.add(alice, bob)
    return project


@pytest.fixture
def ticket(project):
    return Ticket.objects.create(project=project, code="T1", title="Fix the door")


@pytest.fixture
def site(project, alice):
    return ChatService().create_channel(alice, "site", scope=project)


@pytest.fixture
def references_added():
    received = []

    def on_reference(**kwargs):
        received.append(kwargs)

    signals.reference_added.connect(on_reference)
    yield received
    signals.reference_added.disconnect(on_reference)


def post(user, channel, text, **kwargs):
    return ChatService().post_message(user, channel, text, **kwargs)


def render(message, viewer):
    return ChatRenderer(viewer).message_view(Message.objects.for_display().get(pk=message.pk))


class TestMarkup:
    ticket = Reference(kind="ticket", id="7", code="T1", label="#T1 <Door>", url="/t/7/")

    def test_encode_swaps_known_codes_only(self):
        body = markup.encode("see #T1, #T9 and #T1.", references={"T1": self.ticket})
        assert body == "see <#ticket:7>, #T9 and <#ticket:7>."

    def test_encode_neutralises_forged_references(self):
        body = markup.encode("sneaky <#ticket:7> #T1", references={"T1": self.ticket})
        assert markup.referenced_tokens(body) == ["ticket:7"]
        assert "<\u200b#ticket:7>" in body

    def test_codes_need_a_boundary(self):
        assert markup.reference_codes("https://x.test/#T1 a#T2 (#T3) #T4/5 #-x #T3") == ["T3", "T4/5"]

    def test_render_reference_states(self):
        body = "<#ticket:7> <#ticket:8> <#risk:1>"
        html = markup.render(body, {}, {"ticket:7": self.ticket, "ticket:8": None})
        assert '<a class="huddle-reference" href="/t/7/" data-reference="ticket:7">#T1 &lt;Door&gt;</a>' in html
        assert "huddle-reference--unavailable" in html and "#unavailable" in html
        assert "&lt;#risk:1&gt;" in html

    @pytest.mark.parametrize("url", ["", "javascript:alert(1)"])
    def test_render_reference_without_safe_url_is_not_a_link(self, url):
        reference = Reference(kind="ticket", id="7", code="T1", label="#T1", url=url)
        html = markup.render("<#ticket:7>", {}, {"ticket:7": reference})
        assert html == '<span class="huddle-reference" data-reference="ticket:7">#T1</span>'

    def test_to_editable_references(self):
        text = markup.to_editable("<#ticket:7> <#ticket:8> <#risk:1>", {}, {"ticket:7": self.ticket, "ticket:8": None})
        assert text == "#T1 #unavailable-1 <#risk:1>"

    def test_without_resolvers_tokens_render_as_text(self):
        assert "&lt;#ticket:7&gt;" in markup.render("<#ticket:7>", {})
        assert markup.to_editable("<#ticket:7>", {}) == "<#ticket:7>"


class TestRegistry:
    def test_settings_must_name_resolvers(self, settings):
        settings.HUDDLE_REFERENCE_RESOLVERS = ["huddle.models.Channel"]
        with pytest.raises(InvalidReferenceResolverError) as error:
            get_reference_registry()
        assert error.value.context["path"] == "huddle.models.Channel"

    def test_kinds_must_be_unique(self, settings):
        settings.HUDDLE_REFERENCE_RESOLVERS = [TICKETS, "tests.test_references.ConfidentialTicketResolver"]
        with pytest.raises(DuplicateReferenceKindError) as error:
            get_reference_registry()
        assert error.value.context["kind"] == "ticket"

    @pytest.mark.parametrize("kind", ["", "has space", "a:b"])
    def test_kinds_must_fit_in_a_token(self, kind):
        resolver = type("Bad", (ShadowResolver,), {"kind": kind})()
        with pytest.raises(InvalidReferenceKindError):
            ReferenceRegistry([resolver])

    def test_ids_must_fit_in_a_token(self):
        with pytest.raises(UntokenisableReferenceError) as error:
            SpacedIdResolver().describe(SimpleNamespace(pk="x"))
        assert error.value.context == {"kind": "spaced", "object_id": "has space"}

    def test_default_describe(self):
        reference = ShadowResolver().describe(SimpleNamespace(pk="s-T1"))
        assert (reference.code, reference.label, reference.url, reference.token) == ("T1", "#T1", "", "shadow:s-T1")
        assert ReferenceResolver().describe(SimpleNamespace(pk=3, get_absolute_url=lambda: "/3/")).url == "/3/"

    def test_search_fans_out_up_to_the_limit(self, site, alice, ticket):
        second = Ticket.objects.create(project=ticket.project, code="T2", title="Paint")
        registry = ReferenceRegistry([TicketResolver(), ShadowResolver()])
        tokens = [ref.token for ref in registry.search(site, alice, "T", limit=2)]
        assert tokens == [f"ticket:{ticket.pk}", f"ticket:{second.pk}"]
        assert [ref.kind for ref in registry.search(site, alice, "T", limit=5)] == ["ticket", "ticket", "shadow"]

    def test_visible_ignores_malformed_and_unknown_tokens(self, site, alice, ticket):
        registry = get_reference_registry()
        found = registry.visible(site, alice, ["garbage", "ticket:", "risk:1", f"ticket:{ticket.pk}", "ticket:999"])
        assert list(found) == [f"ticket:{ticket.pk}"]


class TestSending:
    def test_free_typed_code_is_stored_by_id(self, site, alice, ticket, references_added):
        message = post(alice, site, "where are we with #t1?")
        assert message.body == f"where are we with <#ticket:{ticket.pk}>?"
        row = MessageReference.objects.get()
        assert (row.kind, row.object_id, row.target, str(row)) == ("ticket", str(ticket.pk), ticket, str(row))
        assert row.content_type == ContentType.objects.get_for_model(Ticket)
        event = references_added[0]
        assert (event["message"], event["reference"], event["target"]) == (message, row, ticket)

    def test_unknown_and_other_projects_codes_stay_text(self, site, alice, make_user):
        other = Project.objects.create(name="Elsewhere")
        Ticket.objects.create(project=other, code="T5", title="Not yours")
        message = post(alice, site, "#T5 and #T404 <b>")
        assert message.body == "#T5 and #T404 <b>"
        assert not MessageReference.objects.exists()
        assert "#T5 and #T404 &lt;b&gt;" in render(message, alice).html

    def test_no_resolvers_keeps_0_1_behaviour(self, settings, site, alice, ticket):
        settings.HUDDLE_REFERENCE_RESOLVERS = []
        message = post(alice, site, "#T1 <#ticket:1>")
        assert markup.referenced_tokens(message.body) == []
        assert not MessageReference.objects.exists()
        assert "#T1 &lt;\u200b#ticket:1&gt;" in render(message, alice).html

    def test_direct_messages_have_no_scope(self, service, alice, bob, ticket):
        dm = service.open_direct(alice, [bob])
        assert post(alice, dm, "#T1").body == "#T1"

    def test_forged_token_does_not_reference(self, site, alice, ticket):
        message = post(alice, site, f"<#ticket:{ticket.pk}>")
        assert not MessageReference.objects.exists()
        assert "huddle-reference" not in render(message, alice).html

    def test_pick_is_exact_and_permission_checked(self, site, alice, ticket):
        other = Ticket.objects.create(project=Project.objects.create(name="Elsewhere"), code="T1", title="Theirs")
        message = post(alice, site, "#T1", references=[f"ticket:{other.pk}", "garbage"])
        assert message.body == f"<#ticket:{ticket.pk}>"
        message = post(alice, site, "nothing typed", references=[f"ticket:{ticket.pk}"])
        assert message.body == "nothing typed"
        assert MessageReference.objects.count() == 1

    def test_unauthorised_sender_cannot_link(self, settings, site, alice, project):
        settings.HUDDLE_REFERENCE_RESOLVERS = ["tests.test_references.ConfidentialTicketResolver"]
        secret = Ticket.objects.create(project=project, code="T9", title="[confidential] Pay")
        message = post(alice, site, "#T9", references=[f"ticket:{secret.pk}"])
        assert message.body == "#T9"

    def test_first_listed_resolver_wins_unless_picked(self, settings, site, alice, ticket):
        settings.HUDDLE_REFERENCE_RESOLVERS = [TICKETS, "tests.test_references.ShadowResolver"]
        assert post(alice, site, "#T1").body == f"<#ticket:{ticket.pk}>"
        assert post(alice, site, "#T1", references=["shadow:s-T1"]).body == "<#shadow:s-T1>"
        settings.HUDDLE_REFERENCE_RESOLVERS = ["tests.test_references.ShadowResolver", TICKETS]
        message = post(alice, site, "#T1")
        assert message.body == "<#shadow:s-T1>"
        assert MessageReference.objects.filter(message=message).get().content_type is None

    def test_free_typed_lookups_are_capped(self, monkeypatch, site, alice, ticket):
        Ticket.objects.create(project=ticket.project, code="T2", title="Paint")
        monkeypatch.setattr("huddle.services.MAX_RESOLVED_CODES", 1)
        assert post(alice, site, "#T1 #T2").body == f"<#ticket:{ticket.pk}> #T2"

    def test_content_type_only_when_the_id_is_the_pk(self, settings, site, alice, ticket):
        settings.HUDDLE_REFERENCE_RESOLVERS = ["tests.test_references.CodeIdTicketResolver"]
        message = post(alice, site, "#T1")
        assert message.body == "<#ticket:T1>"
        row = MessageReference.objects.get()
        assert (row.object_id, row.content_type) == ("T1", None)
        assert not MessageReference.objects.for_target(ticket).exists()
        assert render(message, alice).references[0].code == "T1"

    def test_unusable_picked_id_is_rejected(self, site, alice, ticket):
        with pytest.raises(InvalidReferenceIdError) as error:
            post(alice, site, "#T1", references=["ticket:abc"])
        assert error.value.context == {"kind": "ticket", "object_ids": ["abc"]}
        assert not Message.objects.exists()

    def test_edit_records_only_new_references(self, site, alice, ticket, references_added):
        second = Ticket.objects.create(project=ticket.project, code="T2", title="Paint")
        message = post(alice, site, "#T1")
        ChatService().edit_message(alice, message, "#T1 and #T2")
        assert [event["target"] for event in references_added] == [ticket, second]
        ChatService().edit_message(alice, message, "just #T2")
        assert list(MessageReference.objects.values_list("object_id", flat=True)) == [str(second.pk)]
        assert len(references_added) == 2

    def test_delete_removes_references(self, service, site, alice, ticket):
        message = post(alice, site, "#T1")
        service.delete_message(alice, message)
        assert not MessageReference.objects.exists()

    def test_for_target_and_visible_to(self, site, alice, ticket, make_user):
        post(alice, site, "#T1")
        found = MessageReference.objects.for_target(ticket)
        assert found.count() == 1
        assert found.visible_to(alice).count() == 1
        assert found.visible_to(make_user("Outsider")).count() == 0


def test_example_project_policy(project, ticket, alice, make_user):
    policy = ProjectChatPolicy()
    assert policy.can_create_channel(alice, None) and policy.can_create_channel(alice, project)
    assert not policy.can_create_channel(make_user("Outsider"), project)
    private = ChatService().create_channel(alice, "private", is_private=True)
    assert list(policy.mentionable_users(private)) == [alice]
    assert (str(project), str(ticket)) == ("Tower", "T1")


class TestRendering:
    def test_permission_is_rechecked_for_each_viewer(self, settings, site, alice, bob, project):
        settings.HUDDLE_REFERENCE_RESOLVERS = ["tests.test_references.ConfidentialTicketResolver"]
        alice.is_staff = True
        alice.save()
        secret = Ticket.objects.create(project=project, code="T9", title="[confidential] Pay")
        message = post(alice, site, "#T9")
        assert render(message, alice).references[0].label == "#T9 [confidential] Pay"
        hidden = render(message, bob)
        assert hidden.references == ()
        assert "confidential" not in hidden.html and "#unavailable" in hidden.html
        assert hidden.text == "#unavailable-1"
        assert secret.code not in hidden.html

    def test_token_without_a_recorded_row_is_unavailable(self, site, alice, ticket):
        message = Message.objects.create(channel=site, author=alice, body=f"<#ticket:{ticket.pk}>")
        view = render(message, alice)
        assert "#unavailable" in view.html and view.references == ()

    def test_references_resolve_in_bulk(self, site, alice, ticket):
        def count_queries(size):
            for _ in range(size):
                post(alice, site, "#T1 and #T1")
            messages = list(Message.objects.for_display().order_by("-pk")[:size])
            renderer = ChatRenderer(alice)
            with CaptureQueriesContext(connection) as queries:
                views = renderer.message_views(messages)
            assert all(view.references[0].code == "T1" for view in views)
            return [
                query
                for query in queries.captured_queries
                if "example_ticket" in query["sql"] or "messagereference" in query["sql"]
            ]

        assert len(count_queries(1)) == len(count_queries(5)) == 2

    def test_editable_text(self, site, alice, ticket):
        message = post(alice, site, "#T1")
        assert ChatRenderer(alice).editable_text(message) == "#T1"


class TestViews:
    def test_composer_offers_references_only_when_configured(self, settings, client, site, alice):
        client.force_login(alice)
        url = reverse("huddle:channel", args=[site.pk])
        assert reverse("huddle:references", args=[site.pk]) in client.get(url).content.decode()
        settings.HUDDLE_REFERENCE_RESOLVERS = []
        assert "data-reference-url" not in client.get(url).content.decode()

    def test_reference_search(self, client, site, alice, ticket, make_user):
        client.force_login(alice)
        url = reverse("huddle:references", args=[site.pk])
        content = client.get(url, {"q": "#t"}).content.decode()
        assert f'data-token="ticket:{ticket.pk}"' in content and "#T1 Fix the door" in content
        assert "No matches" in client.get(url, {"q": "zzz"}).content.decode()
        client.force_login(make_user("Outsider"))
        assert client.get(url).status_code == 404

    def test_post_and_edit_with_picked_reference(self, client, site, alice, ticket):
        client.force_login(alice)
        data = {"text": "see #T1", "references": [f"ticket:{ticket.pk}"]}
        response = client.post(reverse("huddle:post", args=[site.pk]), data, **HTMX)
        assert 'class="huddle-reference"' in response.content.decode()
        message = Message.objects.get()
        form = client.get(reverse("huddle:edit", args=[message.pk]), **HTMX).content.decode()
        assert "see #T1" in form and f'value="ticket:{ticket.pk}"' in form
        client.post(reverse("huddle:edit", args=[message.pk]), {"text": "no refs"}, **HTMX)
        assert not MessageReference.objects.exists()

    def test_unusable_picked_id_is_a_bad_request(self, client, site, alice, ticket):
        client.force_login(alice)
        data = {"text": "#T1", "references": ["ticket:abc"]}
        assert client.post(reverse("huddle:post", args=[site.pk]), data, **HTMX).status_code == 400

    def test_reply_with_reference(self, client, site, alice, ticket):
        client.force_login(alice)
        root = post(alice, site, "question")
        client.post(reverse("huddle:reply", args=[root.pk]), {"text": "#T1"}, **HTMX)
        assert MessageReference.objects.get().message.parent == root


class TestApi:
    def test_post_payload_and_search(self, site, alice, bob, ticket):
        api = APIClient()
        api.force_authenticate(alice)
        url = f"/api/chat/channels/{site.pk}/messages/"
        created = api.post(url, {"text": "#T1", "reference_ids": [f"ticket:{ticket.pk}"]}, format="json").json()
        expected = {
            "kind": "ticket",
            "id": str(ticket.pk),
            "code": "T1",
            "label": "#T1 Fix the door",
            "url": ticket.get_absolute_url(),
        }
        assert created["references"] == [expected]
        assert created["text"] == "#T1"
        found = api.get(f"/api/chat/channels/{site.pk}/references/", {"q": "door"}).json()
        assert found == [expected]
        edited = api.patch(f"/api/chat/messages/{created['id']}/", {"text": "none"}, format="json").json()
        assert edited["references"] == []
        bad = api.post(url, {"text": "#T1", "reference_ids": ["ticket:abc"]}, format="json")
        assert bad.status_code == 400

    def test_payload_hides_what_the_viewer_cannot_see(self, settings, site, alice, bob, project):
        settings.HUDDLE_REFERENCE_RESOLVERS = ["tests.test_references.ConfidentialTicketResolver"]
        alice.is_staff = True
        alice.save()
        Ticket.objects.create(project=project, code="T9", title="[confidential] Pay")
        message = post(alice, site, "#T9")
        api = APIClient()
        api.force_authenticate(bob)
        payload = api.get(f"/api/chat/messages/{message.pk}/").json()
        assert payload["references"] == [] and "confidential" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_websocket_json_mode_includes_references(alice, site, ticket):
    communicator = WebsocketCommunicator(URLRouter(websocket_urlpatterns), "/ws/huddle/?format=json")
    communicator.scope["user"] = alice
    await communicator.connect()
    await communicator.send_json_to({"subscribe": site.pk})
    assert await communicator.receive_nothing(timeout=0.5)
    await sync_to_async(post)(alice, site, "#T1")
    events = [json.loads(await communicator.receive_from()) for _ in range(2)]
    created = next(event for event in events if event["type"] == "message.created")
    assert created["message"]["references"][0]["code"] == "T1"
    await communicator.disconnect()
