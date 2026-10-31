from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from example.models import Project
from example.models import Ticket
from huddle import signals
from huddle.markup import MessageMarkup
from huddle.models import Mention
from huddle.models import MessageReference
from huddle.rendering import ChatRenderer
from huddle.services import ChatService

pytestmark = pytest.mark.django_db(transaction=True)

markup = MessageMarkup()
HTMX = {"HTTP_HX_REQUEST": "true"}
CONFIDENTIAL = "tests.test_references.ConfidentialTicketResolver"


def make_person(username, first, last=""):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        first_name=first,
        last_name=last,
        password="pw",  # noqa: S106
    )


class TestTypedNamesMarkup:
    ann = SimpleNamespace(pk=1)
    ann_lee = SimpleNamespace(pk=2)

    def test_longest_full_name_wins(self):
        typed = {"ann": self.ann, "ann lee": self.ann_lee}
        assert markup.encode("@ann lee, @Ann and @ANN LEE", handles=typed) == "<@2>, <@1> and <@2>"

    def test_name_must_end_at_a_word_boundary(self):
        assert markup.encode("@Ann Leeds", handles={"ann lee": self.ann_lee}) == "@Ann Leeds"
        assert markup.encode("@Ann Leeds", handles={"ann": self.ann, "ann lee": self.ann_lee}) == "<@1> Leeds"

    def test_ambiguous_name_does_not_fall_back_to_a_shorter_one(self):
        assert markup.encode("@Ann Lee", handles={"ann": self.ann, "ann lee": None}) == "@Ann Lee"

    def test_unavailable_references_are_numbered(self):
        forms = markup.editable_forms("<#t:1> <#t:2> <#t:1> <#t:3> <@9>", {}, {"t:1": None, "t:2": None, "t:3": None})
        assert forms == {
            "<#t:1>": "#unavailable-1",
            "<#t:2>": "#unavailable-2",
            "<#t:3>": "#unavailable-3",
            "<@9>": "@unknown",
        }


class TestTypedNames:
    def test_typed_full_name_mentions(self, service, general, alice, bob, received_signals):
        message = service.post_message(alice, general, "thanks @bob jones!")
        assert message.body == f"thanks <@{bob.pk}>!"
        assert received_signals["mentioned"][0]["user"] == bob

    def test_longest_mentionable_name_wins(self, service, general, alice):
        ann = make_person("ann", "Ann")
        ann_lee = make_person("annlee", "Ann", "Lee")
        message = service.post_message(alice, general, "@Ann Lee and @Ann, not @Ann Leeds")
        assert message.body == f"<@{ann_lee.pk}> and <@{ann.pk}>, not <@{ann.pk}> Leeds"

    def test_shared_full_name_mentions_nobody(self, service, general, alice):
        make_person("ann", "Ann")
        make_person("annlee1", "Ann", "Lee")
        make_person("annlee2", "Ann", "Lee")
        assert service.post_message(alice, general, "@Ann Lee hi").body == "@Ann Lee hi"
        assert not Mention.objects.exists()

    def test_only_mentionable_names_match(self, service, alice, bob):
        private = service.create_channel(alice, "private", is_private=True)
        assert service.post_message(alice, private, "@Bob Jones").body == "@Bob Jones"

    def test_setting_turns_names_off(self, settings, service, general, alice, bob):
        settings.HUDDLE_FREE_TYPED_MENTIONS = False
        assert service.post_message(alice, general, "@Bob Jones").body == "@Bob Jones"


def test_mention_search_accepts_a_partial_full_name(client, general, alice, bob, carol):
    client.force_login(alice)
    url = reverse("huddle:mentions", args=[general.pk])
    content = client.get(url, {"q": "Bob J"}).content.decode()
    assert "Bob Jones" in content and "Carol White" not in content
    assert "No matches" not in client.get(url, {"q": "bob says hi"}).content.decode()
    assert "No matches" in client.get(url, {"q": "zzz"}).content.decode()


@pytest.fixture
def project(alice, bob):
    project = Project.objects.create(name="Tower")
    project.members.add(alice, bob)
    return project


@pytest.fixture
def site(settings, project, alice):
    settings.HUDDLE_POLICY = "example.chat.ProjectChatPolicy"
    settings.HUDDLE_REFERENCE_RESOLVERS = [CONFIDENTIAL]
    alice.is_staff = True
    alice.save()
    return ChatService().create_channel(alice, "site", scope=project)


@pytest.fixture
def secrets(project):
    return [
        Ticket.objects.create(project=project, code=f"T{number}", title=f"[confidential] {number}") for number in (8, 9)
    ]


@pytest.fixture
def announced():
    received: list[dict[str, object]] = []

    def on_signal(**kwargs):
        received.append(kwargs)

    signals.reference_added.connect(on_signal)
    signals.user_mentioned.connect(on_signal)
    yield received
    signals.reference_added.disconnect(on_signal)
    signals.user_mentioned.disconnect(on_signal)


def lose_staff(user):
    user.is_staff = False
    user.save()


class TestEditKeepsRemainingLinks:
    def test_unavailable_references_are_kept_or_dropped_by_their_placeholder(self, site, alice, secrets, announced):
        eight, nine = secrets
        message = ChatService().post_message(alice, site, "#T8 then #T9")
        announced.clear()
        lose_staff(alice)
        assert ChatRenderer(alice).editable_text(message) == "#unavailable-1 then #unavailable-2"
        ChatService().edit_message(alice, message, "only #unavailable-2 now")
        assert message.body == f"only <#ticket:{nine.pk}> now"
        assert list(MessageReference.objects.values_list("object_id", flat=True)) == [str(nine.pk)]
        assert announced == []

    def test_mention_kept_when_no_longer_mentionable(self, service, alice, bob, announced):
        private = service.create_channel(alice, "private", is_private=True)
        service.add_members(alice, private, [bob])
        message = service.post_message(alice, private, "hi @Bob Jones", mentioned=[bob])
        private.memberships.filter(user=bob).delete()
        announced.clear()
        service.edit_message(alice, message, "hi again @Bob Jones")
        assert message.body == f"hi again <@{bob.pk}>"
        assert announced == []
        service.edit_message(alice, message, "hi again Bob")
        assert message.body == "hi again Bob"

    def test_new_text_still_follows_the_rules(self, site, alice, bob, secrets):
        message = ChatService().post_message(alice, site, "#T8")
        lose_staff(alice)
        ChatService().edit_message(alice, message, f"#unavailable-1 #T9 #unavailable-2 <#ticket:{secrets[1].pk}>")
        assert message.body.startswith(f"<#ticket:{secrets[0].pk}> #T9 #unavailable-2 <\u200b#ticket:")
        assert MessageReference.objects.count() == 1

    def test_placeholders_are_per_message(self, site, alice, secrets):
        ChatService().post_message(alice, site, "#T8")
        other = ChatService().post_message(alice, site, "nothing")
        lose_staff(alice)
        ChatService().edit_message(alice, other, "#unavailable-1")
        assert other.body == "#unavailable-1"

    def test_ambiguous_forms_and_deleted_users_are_not_kept(self, service, general, alice, carol):
        twin_one = make_person("ann1", "Ann", "Lee")
        twin_two = make_person("ann2", "Ann", "Lee")
        message = service.post_message(alice, general, "@Ann Lee and @Ann Lee", mentioned=[twin_one])
        message.body = f"<@{twin_one.pk}> and <@{twin_two.pk}> and <@{carol.pk}>"
        message.save()
        carol.delete()
        kept = service.kept_links(alice, message)
        assert kept.mentions == {}

    def test_html_edit_round_trip_keeps_links(self, client, site, alice, secrets):
        message = ChatService().post_message(alice, site, "#T8 and #T9")
        lose_staff(alice)
        client.force_login(alice)
        form = client.get(reverse("huddle:edit", args=[message.pk]), **HTMX).content.decode()
        assert "#unavailable-1 and #unavailable-2" in form
        client.post(reverse("huddle:edit", args=[message.pk]), {"text": "#unavailable-1 and #unavailable-2!"}, **HTMX)
        message.refresh_from_db()
        assert message.body == f"<#ticket:{secrets[0].pk}> and <#ticket:{secrets[1].pk}>!"
        assert MessageReference.objects.count() == 2

    def test_rest_patch_round_trip_keeps_links(self, site, alice, secrets):
        message = ChatService().post_message(alice, site, "#T8 and #T9")
        lose_staff(alice)
        api = APIClient()
        api.force_authenticate(alice)
        url = f"/api/chat/messages/{message.pk}/"
        text = api.get(url).json()["text"]
        assert text == "#unavailable-1 and #unavailable-2"
        api.patch(url, {"text": text.replace(" and #unavailable-2", "")}, format="json")
        message.refresh_from_db()
        assert message.body == f"<#ticket:{secrets[0].pk}>"
        assert MessageReference.objects.count() == 1


def test_html_edit_keeps_mention_of_someone_no_longer_mentionable(client, service, alice, bob):
    private = service.create_channel(alice, "private", is_private=True)
    service.add_members(alice, private, [bob])
    message = service.post_message(alice, private, "hi @Bob Jones", mentioned=[bob])
    private.memberships.filter(user=bob).delete()
    client.force_login(alice)
    form = client.get(reverse("huddle:edit", args=[message.pk]), **HTMX).content.decode()
    assert f'name="mentions" value="{bob.pk}"' not in form
    client.post(reverse("huddle:edit", args=[message.pk]), {"text": "hi @Bob Jones!"}, **HTMX)
    message.refresh_from_db()
    assert message.body == f"hi <@{bob.pk}>!"


def test_full_name_matching_survives_case_changing_lengths():
    ann_lee = SimpleNamespace(pk=2)
    assert markup.encode("İİ @Ann Lee ok", handles={"ann lee": ann_lee}) == "İİ <@2> ok"


def test_full_names_with_regex_characters_match_literally():
    odd = SimpleNamespace(pk=3)
    assert markup.encode("@a.b (c) and @axb (c)", handles={"a.b (c)": odd}) == "<@3> and @axb (c)"
