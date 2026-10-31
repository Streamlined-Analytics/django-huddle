"""Who may see and do what in chat.

Every permission decision in django-huddle goes through one
:class:`ChatPolicy`. Host projects subclass it and point the
``HUDDLE_POLICY`` setting at their subclass. The default policy knows nothing
about scopes, so it hides every scoped channel: a host that attaches channels
to its own objects must say who can see them by overriding :meth:`scope_q`.

Example — channels attached to projects, visible to project members::

    class ProjectChatPolicy(ChatPolicy):
        def scope_q(self, user):
            projects = Project.objects.visible_to(user).values("pk")
            return Q(scope_content_type__isnull=True) | self.scopes_q(Project, projects)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import CharField
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models.functions import Cast

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser
    from django.db.models import QuerySet

    from huddle.models import Channel
    from huddle.models import Message
    from huddle.querysets import ChannelQuerySet


class ChatPolicy:
    """The default chat policy: unscoped channels for every signed-in user.

    - Public unscoped channels are visible to every active, signed-in user.
    - Private channels and direct messages are visible to their members only.
    - Scoped channels are hidden until a subclass overrides :meth:`scope_q`.
    """

    def scope_q(self, user: Any) -> Q:
        """Filter on ``Channel`` limiting results to scopes ``user`` may access.

        The default allows only unscoped channels. Override to add your own
        scopes, usually with :meth:`scopes_q`.
        """
        return Q(scope_content_type__isnull=True)

    @staticmethod
    def scopes_q(model: type[models.Model], pks: QuerySet[Any, Any]) -> Q:
        """Filter on ``Channel`` matching channels scoped to any ``model`` row whose pk is in ``pks``.

        ``pks`` is a ``values("pk")`` queryset; it runs as a subquery.
        """
        return Q(
            scope_content_type=ContentType.objects.get_for_model(model),
            scope_object_id__in=pks.annotate(_huddle_pk=Cast("pk", CharField())).values("_huddle_pk"),
        )

    def filter_visible(self, channels: ChannelQuerySet, user: Any) -> ChannelQuerySet:
        """Narrow ``channels`` to those ``user`` may see."""
        if not getattr(user, "is_authenticated", False) or not user.is_active:
            return channels.none()
        from huddle.models import ChannelMembership  # noqa: PLC0415

        is_member = Exists(ChannelMembership.objects.filter(channel=OuterRef("pk"), user=user))
        return channels.filter(self.scope_q(user)).filter(Q(is_private=False) | is_member)

    def can_view(self, user: Any, channel: Channel) -> bool:
        """Whether ``user`` may read ``channel``."""
        from huddle.models import Channel  # noqa: PLC0415

        return self.filter_visible(Channel.objects.filter(pk=channel.pk), user).exists()

    def can_post(self, user: Any, channel: Channel) -> bool:
        """Whether ``user`` may post in ``channel``. Archived channels are read-only."""
        return not channel.is_archived and self.can_view(user, channel)

    def can_create_channel(self, user: Any, scope: models.Model | None) -> bool:
        """Whether ``user`` may create a named channel in ``scope`` (``None`` means unscoped)."""
        return scope is None and getattr(user, "is_authenticated", False)

    def can_manage_channel(self, user: Any, channel: Channel) -> bool:
        """Whether ``user`` may rename, archive or change the members of ``channel``."""
        if channel.is_direct:
            return False
        return bool(user.is_staff or channel.created_by_id == user.pk) and self.can_view(user, channel)

    def can_edit_message(self, user: Any, message: Message) -> bool:
        """Whether ``user`` may edit ``message``. Only its author can."""
        return not message.is_deleted and message.author_id == user.pk and self.can_post(user, message.channel)

    def can_delete_message(self, user: Any, message: Message) -> bool:
        """Whether ``user`` may delete ``message``: its author, or someone who manages the channel."""
        if message.is_deleted:
            return False
        return message.author_id == user.pk or self.can_manage_channel(user, message.channel)

    def mentionable_users(self, channel: Channel) -> QuerySet[AbstractBaseUser]:
        """Users who may be @mentioned in ``channel``: the people who can read it.

        Private channels and DMs offer their members. Public unscoped channels
        offer every active user. Override for scoped public channels.
        """
        users = get_user_model()._default_manager.filter(is_active=True)
        if channel.is_private:
            return users.filter(huddle_memberships__channel=channel)
        if channel.scope_content_type_id is None:
            return users
        return users.none()

    def direct_message_candidates(self, user: Any) -> QuerySet[AbstractBaseUser]:
        """Users ``user`` may start a direct message with. Default: every other active user."""
        return get_user_model()._default_manager.filter(is_active=True).exclude(pk=user.pk)
