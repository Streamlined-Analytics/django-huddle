from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.test import override_settings

from huddle.conf import get_policy
from huddle.markup import MessageMarkup
from huddle.markup import display_name
from huddle.models import Channel
from huddle.policy import ChatPolicy

markup = MessageMarkup()


class TestMarkup:
    def test_encode_prefers_longest_name(self):
        ann = SimpleNamespace(pk=1, get_full_name=lambda: "Ann")
        ann_lee = SimpleNamespace(pk=2, get_full_name=lambda: "Ann Lee")
        assert markup.encode("hi @Ann Lee and @Ann", [ann, ann_lee]) == "hi <@2> and <@1>"

    def test_encode_neutralises_forged_tokens(self):
        body = markup.encode("sneaky <@1>", [])
        assert markup.mentioned_pks(body) == set()

    def test_render_escapes_html_and_links(self):
        html = markup.render("<script>x</script> see https://example.com\nnext", {})
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert 'href="https://example.com"' in html
        assert "<br>" in html

    def test_render_mentions(self):
        bob = SimpleNamespace(pk=7, get_full_name=lambda: "Bob <b>")
        html = markup.render("hi <@7> and <@99>", {"7": bob})
        assert 'data-user="7">@Bob &lt;b&gt;</span>' in html
        assert "@unknown" in html

    def test_render_reserved_reference_as_text(self):
        assert "&lt;#risk:4&gt;" in markup.render("see <#risk:4>", {})

    def test_to_editable(self):
        bob = SimpleNamespace(pk=7, get_full_name=lambda: "Bob")
        assert markup.to_editable("hi <@7> <@8>", {"7": bob}) == "hi @Bob @unknown"

    def test_display_name_falls_back(self):
        assert display_name(SimpleNamespace(get_full_name=lambda: " ", __str__=None)) != ""
        user = get_user_model()(username="nobody")
        assert display_name(user) == "nobody"


@pytest.mark.django_db
class TestDefaultPolicy:
    def test_anonymous_sees_nothing(self, general):
        assert not Channel.objects.visible_to(SimpleNamespace(is_authenticated=False)).exists()

    def test_inactive_sees_nothing(self, general, make_user):
        assert not Channel.objects.visible_to(make_user("Gone", is_active=False)).exists()

    def test_public_visible_private_hidden(self, service, general, alice, bob):
        private = service.create_channel(alice, "private", is_private=True)
        assert set(Channel.objects.visible_to(bob)) == {general}
        assert set(Channel.objects.visible_to(alice)) == {general, private}

    def test_scoped_channels_hidden_by_default(self, alice, bob):
        scoped = Channel(name="scoped", created_by=alice)
        scoped.scope = bob
        scoped.save()
        assert scoped not in Channel.objects.visible_to(alice)
        assert not get_policy().mentionable_users(scoped).exists()

    def test_mentionable_users(self, service, general, alice, bob):
        private = service.create_channel(alice, "private", is_private=True)
        assert set(get_policy().mentionable_users(private)) == {alice}
        assert {alice, bob} <= set(get_policy().mentionable_users(general))

    def test_staff_manage_any_channel(self, general, make_user):
        assert get_policy().can_manage_channel(make_user("Staff", is_staff=True), general)

    def test_nobody_manages_direct(self, service, alice, bob):
        assert not get_policy().can_manage_channel(alice, service.open_direct(alice, [bob]))


class OwnScopePolicy(ChatPolicy):
    """Channels scoped to a user are visible to that user only."""

    def scope_q(self, user):
        users = get_user_model().objects.filter(pk=user.pk).values("pk")
        return Q(scope_content_type__isnull=True) | self.scopes_q(get_user_model(), users)

    def can_create_channel(self, user, scope):
        return scope is None or scope.pk == user.pk


@pytest.mark.django_db
class TestCustomPolicy:
    @override_settings(HUDDLE_POLICY="tests.test_markup_and_policy.OwnScopePolicy")
    def test_scoped_channels_follow_scope_q(self, service, alice, bob):
        from huddle.services import ChatService  # noqa: PLC0415

        channel = ChatService().create_channel(alice, "mine", scope=alice)
        assert channel.scope == alice
        assert channel in Channel.objects.visible_to(alice)
        assert channel not in Channel.objects.visible_to(bob)
        assert list(Channel.objects.for_scope(alice)) == [channel]
        assert channel not in Channel.objects.for_scope(None)

    @override_settings(HUDDLE_POLICY="huddle.models.Channel")
    def test_policy_setting_must_be_a_policy(self):
        with pytest.raises(TypeError):
            get_policy()
