from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from example.chat import MilestoneResolver
from example.chat import TicketResolver
from example.models import Milestone
from example.models import Project
from example.models import Ticket
from huddle.conf import get_reference_registry
from huddle.exceptions import InvalidReferenceTriggerError
from huddle.exceptions import ReservedReferenceTriggerError
from huddle.markup import MessageMarkup
from huddle.models import Message
from huddle.models import MessageReference
from huddle.references import ReferenceRegistry
from huddle.references import ReferenceResolver
from huddle.rendering import ChatRenderer
from huddle.services import ChatService

pytestmark = pytest.mark.django_db(transaction=True)

HTMX = {"HTTP_HX_REQUEST": "true"}
TICKETS = "example.chat.TicketResolver"
MILESTONES = "example.chat.MilestoneResolver"


class StaffMilestoneResolver(MilestoneResolver):
    """Milestones visible to staff only, to test placeholders with the ``!`` trigger."""

    def milestones(self, channel, user):
        milestones = super().milestones(channel, user)
        return milestones if user.is_staff else milestones.none()


class PercentMilestoneResolver(MilestoneResolver):
    """The same kind as :class:`MilestoneResolver`, with its trigger changed to ``%``."""

    trigger = "%"


class AmpersandResolver(ReferenceResolver):
    kind = "amp"
    trigger = "&"


def resolver_with(trigger):
    return type("Custom", (ReferenceResolver,), {"kind": "custom", "trigger": trigger})()


@pytest.fixture(autouse=True)
def project_chat(settings):
    settings.HUDDLE_POLICY = "example.chat.ProjectChatPolicy"
    settings.HUDDLE_REFERENCE_RESOLVERS = [TICKETS, MILESTONES]


@pytest.fixture
def project(alice, bob):
    project = Project.objects.create(name="Tower")
    project.members.add(alice, bob)
    return project


@pytest.fixture
def ticket(project):
    return Ticket.objects.create(project=project, code="T1", title="Fix the door")


@pytest.fixture
def milestone(project):
    return Milestone.objects.create(project=project, code="M1", title="Handover")


@pytest.fixture
def site(project, alice):
    return ChatService().create_channel(alice, "site", scope=project)


def post(user, channel, text, **kwargs):
    return ChatService().post_message(user, channel, text, **kwargs)


def render(message, viewer):
    return ChatRenderer(viewer).message_view(Message.objects.for_display().get(pk=message.pk))


class TestDefaults:
    def test_resolvers_default_to_hash(self):
        resolver = ReferenceResolver()
        assert resolver.trigger == "#"
        reference = TicketResolver().describe(SimpleNamespace(pk=1, code="T1", title="Door", get_absolute_url=str))
        assert (reference.trigger, reference.typed) == ("#", "#T1")
        assert ReferenceRegistry([TicketResolver()]).triggers == ("#",)

    def test_markup_without_a_registry_reads_hash_only(self):
        assert MessageMarkup().typed_references("#T1 !M1 %X") == ["#T1"]

    def test_label_defaults_to_trigger_and_code(self):
        reference = MilestoneResolver().describe(SimpleNamespace(pk=4, code="M1", get_absolute_url=lambda: "/m/4/"))
        assert (reference.label, reference.typed, reference.token) == ("!M1", "!M1", "milestone:4")
        assert reference.as_dict()["trigger"] == "!"


class TestValidation:
    @pytest.mark.parametrize("trigger", ["", "!!", "a", "7", "_", " ", "\n", None])
    def test_trigger_must_be_one_non_word_non_space_character(self, trigger):
        with pytest.raises(InvalidReferenceTriggerError) as error:
            ReferenceRegistry([resolver_with(trigger)])
        assert error.value.context["trigger"] == repr(trigger)
        assert error.value.context["resolver"].endswith("Custom")

    @pytest.mark.parametrize(
        ("trigger", "reserved_for"), [("@", "mentions"), ("<", "stored tokens"), (">", "stored tokens")]
    )
    def test_reserved_triggers_are_rejected(self, trigger, reserved_for):
        with pytest.raises(ReservedReferenceTriggerError) as error:
            ReferenceRegistry([resolver_with(trigger)])
        assert (error.value.context["trigger"], error.value.context["reserved_for"]) == (trigger, reserved_for)

    def test_settings_are_validated_when_the_registry_is_built(self, settings):
        settings.HUDDLE_REFERENCE_RESOLVERS = [TICKETS, "tests.test_reference_triggers.BadTriggerResolver"]
        with pytest.raises(InvalidReferenceTriggerError):
            get_reference_registry()

    def test_resolvers_may_share_a_trigger(self):
        registry = ReferenceRegistry([TicketResolver(), MilestoneResolver(), resolver_with("#")])
        assert registry.triggers == ("#", "!")
        assert (registry.trigger_of("milestone"), registry.trigger_of("custom"), registry.trigger_of("nope")) == (
            "!",
            "#",
            "#",
        )


class BadTriggerResolver(TicketResolver):
    kind = "bad"
    trigger = "ab"


class TestSending:
    def test_each_trigger_resolves_its_own_kind(self, site, alice, ticket, milestone):
        message = post(alice, site, "#T1 is blocking !M1")
        assert message.body == f"<#ticket:{ticket.pk}> is blocking <#milestone:{milestone.pk}>"
        assert set(MessageReference.objects.values_list("kind", flat=True)) == {"ticket", "milestone"}

    def test_wrong_trigger_stays_text(self, site, alice, ticket, milestone):
        assert post(alice, site, "#M1 and !T1").body == "#M1 and !T1"
        assert not MessageReference.objects.exists()

    def test_same_code_under_two_triggers(self, site, alice, ticket, project):
        same_code = Milestone.objects.create(project=project, code="T1", title="Also T1")
        assert post(alice, site, "#T1 !T1").body == f"<#ticket:{ticket.pk}> <#milestone:{same_code.pk}>"

    def test_picked_reference_with_custom_trigger(self, site, alice, milestone, project):
        other = Milestone.objects.create(project=Project.objects.create(name="Else"), code="M1", title="Theirs")
        message = post(alice, site, "see !M1", references=[f"milestone:{other.pk}", f"milestone:{milestone.pk}"])
        assert message.body == f"see <#milestone:{milestone.pk}>"

    @pytest.mark.parametrize(
        "text",
        [
            "done!",
            "!!!",
            "really?!",
            "wow!M1",
            "! M1",
            "Hi!M1 there",
            "great work!!",
            "a!b!c",
            "(see below)!",
        ],
    )
    def test_exclamations_in_prose_are_never_looked_up(self, monkeypatch, site, alice, milestone, text):
        asked = []
        monkeypatch.setattr(MilestoneResolver, "resolve_code", lambda *args: asked.append(args[-1]))
        assert post(alice, site, text).body == text
        assert asked == []

    def test_unknown_bang_words_stay_text(self, site, alice, milestone):
        message = post(alice, site, "!important: ship (!M1) today!")
        assert message.body == f"!important: ship (<#milestone:{milestone.pk}>) today!"

    def test_forged_tokens_are_still_neutralised(self, site, alice, milestone):
        message = post(alice, site, f"<#milestone:{milestone.pk}> <!milestone:{milestone.pk}>")
        assert not MessageReference.objects.exists()
        html = render(message, alice).html
        assert "huddle-reference" not in html
        assert f"&lt;!milestone:{milestone.pk}&gt;" in html


class TestRendering:
    def test_chip_uses_the_resolvers_trigger(self, site, alice, milestone):
        view = render(post(alice, site, "!M1"), alice)
        assert f'href="{milestone.get_absolute_url()}"' in view.html and ">!M1</a>" in view.html
        assert view.text == "!M1"

    def test_placeholder_uses_the_kinds_trigger(self, site, alice, ticket, milestone):
        body = f"<#ticket:{ticket.pk}> <#milestone:{milestone.pk}>"
        view = render(Message.objects.create(channel=site, author=alice, body=body), alice)
        assert ">#unavailable</span>" in view.html and ">!unavailable</span>" in view.html
        assert view.text == "#unavailable-1 !unavailable-2"

    def test_edit_keeps_an_unavailable_custom_trigger_reference(self, settings, site, alice, milestone):
        settings.HUDDLE_REFERENCE_RESOLVERS = ["tests.test_reference_triggers.StaffMilestoneResolver"]
        alice.is_staff = True
        alice.save()
        message = post(alice, site, "!M1")
        alice.is_staff = False
        alice.save()
        assert ChatRenderer(alice).editable_text(message) == "!unavailable-1"
        ChatService().edit_message(alice, message, "!unavailable-1 is late")
        assert message.body == f"<#milestone:{milestone.pk}> is late"

    def test_changing_a_trigger_keeps_old_messages(self, settings, site, alice, milestone):
        message = post(alice, site, "!M1")
        settings.HUDDLE_REFERENCE_RESOLVERS = [TICKETS, "tests.test_reference_triggers.PercentMilestoneResolver"]
        view = render(message, alice)
        assert ">%M1</a>" in view.html and view.text == "%M1"

    def test_triggers_are_escaped_in_html(self):
        markup = MessageMarkup(ReferenceRegistry([AmpersandResolver()]))
        html = markup.render("<#amp:1>", {}, {"amp:1": None})
        assert html == '<span class="huddle-reference huddle-reference--unavailable">&amp;unavailable</span>'


class TestComposer:
    def test_composer_lists_the_triggers(self, client, site, alice):
        client.force_login(alice)
        content = client.get(reverse("huddle:channel", args=[site.pk])).content.decode()
        assert 'data-reference-triggers="#!"' in content

    def test_search_asks_only_resolvers_with_the_trigger(self, client, site, alice, ticket, project):
        milestone = Milestone.objects.create(project=project, code="T2", title="Topping out")
        client.force_login(alice)
        url = reverse("huddle:references", args=[site.pk])
        bang = client.get(url, {"q": "!t", "trigger": "!"}).content.decode()
        assert f'data-token="milestone:{milestone.pk}"' in bang and 'data-trigger="!"' in bang
        assert "ticket:" not in bang
        hashed = client.get(url, {"q": "t", "trigger": "#"}).content.decode()
        assert f'data-token="ticket:{ticket.pk}"' in hashed and "milestone:" not in hashed
        everything = client.get(url, {"q": "t"}).content.decode()
        assert "ticket:" in everything and "milestone:" in everything
        assert "No matches" in client.get(url, {"q": "t", "trigger": "%"}).content.decode()

    def test_api_search_by_trigger(self, site, alice, ticket, milestone):
        api = APIClient()
        api.force_authenticate(alice)
        found = api.get(f"/api/chat/channels/{site.pk}/references/", {"q": "m", "trigger": "!"}).json()
        assert [(item["kind"], item["trigger"], item["label"]) for item in found] == [("milestone", "!", "!M1")]
