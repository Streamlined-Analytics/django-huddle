from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from huddle.markup import MessageMarkup
from huddle.markup import mention_handles
from huddle.models import Mention
from huddle.models import ThreadParticipant

pytestmark = pytest.mark.django_db(transaction=True)

markup = MessageMarkup()


def make_person(username, first, last, email):
    return get_user_model().objects.create_user(
        username=username,
        email=email,
        first_name=first,
        last_name=last,
        password="pw",  # noqa: S106
    )


class TestMarkup:
    def test_handles_are_found_after_a_boundary(self):
        assert markup.handles("hi @BenAtkinson, cc (@ann.lee) mail@x.com <@3>") == {"benatkinson", "ann.lee"}

    def test_picked_names_are_not_handles(self):
        ann = SimpleNamespace(pk=1, get_full_name=lambda: "Ann Lee")
        assert markup.handles("@Ann Lee and @bob", [ann]) == {"bob"}

    def test_encode_handles(self):
        bob = SimpleNamespace(pk=7)
        assert markup.encode("@BOBJONES @bobjonesjr @nobody.", handles={"bobjones": bob}) == "<@7> @bobjonesjr @nobody."

    def test_mention_handles(self):
        user = SimpleNamespace(get_full_name=lambda: "Ben  Atkinson", email="b.atkinson@example.com")
        assert mention_handles(user) == {"benatkinson", "b.atkinson"}
        assert mention_handles(get_user_model()(username="nobody")) == {"nobody"}


class TestService:
    def test_free_typed_handle_mentions(self, service, general, alice, bob, received_signals):
        message = service.post_message(alice, general, "ping @BobJones")
        assert message.body == f"ping <@{bob.pk}>"
        assert list(Mention.objects.values_list("user", flat=True)) == [bob.pk]
        assert received_signals["mentioned"][0]["user"] == bob

    def test_email_local_part(self, service, general, alice):
        dan = make_person("dan", "Dan", "Brown", "d.brown@example.com")
        assert service.post_message(alice, general, "@d.brown please").body == f"<@{dan.pk}> please"

    def test_ambiguous_handle_links_nobody(self, service, general, alice, bob, received_signals):
        make_person("bob2", "Bob", "Jones", "other@example.com")
        message = service.post_message(alice, general, "@bobjones?")
        assert message.body == "@bobjones?"
        assert not Mention.objects.exists()
        assert received_signals["mentioned"] == []

    def test_handle_must_be_mentionable(self, service, alice, bob):
        private = service.create_channel(alice, "private", is_private=True)
        assert service.post_message(alice, private, "@bobjones").body == "@bobjones"

    def test_setting_turns_handles_off(self, settings, service, general, alice, bob):
        settings.HUDDLE_FREE_TYPED_MENTIONS = False
        assert service.post_message(alice, general, "@bobjones").body == "@bobjones"

    def test_forged_tokens_stay_neutral(self, service, general, alice, bob):
        message = service.post_message(alice, general, f"<@{bob.pk}> @bobjones")
        assert message.body == f"<\u200b@{bob.pk}> <@{bob.pk}>"
        assert markup.mentioned_pks(message.body) == {str(bob.pk)}

    def test_picked_and_typed_are_one_mention(self, service, general, alice, bob, received_signals):
        message = service.post_message(alice, general, "@Bob Jones and @bobjones", mentioned=[bob])
        assert message.body == f"<@{bob.pk}> and <@{bob.pk}>"
        assert Mention.objects.count() == 1
        assert len(received_signals["mentioned"]) == 1

    def test_edit_and_thread(self, service, general, alice, bob, carol, received_signals):
        root = service.post_message(alice, general, "question")
        reply = service.post_message(bob, general, "answer", parent=root)
        service.edit_message(bob, reply, "answer, cc @carolwhite")
        assert ThreadParticipant.objects.filter(thread=root, user=carol).exists()
        assert received_signals["mentioned"][-1]["user"] == carol


def test_composer_post_with_typed_handle(client, general, alice, bob):
    client.force_login(alice)
    client.post(reverse("huddle:post", args=[general.pk]), {"text": "hi @bobjones"}, HTTP_HX_REQUEST="true")
    assert Mention.objects.get().user == bob
