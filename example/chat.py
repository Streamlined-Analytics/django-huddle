"""How a host wires django-huddle to its own objects: a project-scoped policy and a ``#ticket`` resolver.

Enable with::

    HUDDLE_POLICY = "example.chat.ProjectChatPolicy"
    HUDDLE_REFERENCE_RESOLVERS = ["example.chat.TicketResolver"]
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.db.models import Q
from django.db.models import QuerySet

from example.models import Project
from example.models import Ticket
from huddle.models import Channel
from huddle.policy import ChatPolicy
from huddle.references import ReferenceResolver


class ProjectChatPolicy(ChatPolicy):
    """Channels attached to a project are visible to its members."""

    def scope_q(self, user: Any) -> Q:
        """Unscoped channels, plus channels of projects ``user`` belongs to."""
        projects = Project.objects.filter(members=user).values("pk")
        return Q(scope_content_type__isnull=True) | self.scopes_q(Project, projects)

    def can_create_channel(self, user: Any, scope: Any) -> bool:
        """Project members may create channels in their projects."""
        if scope is None:
            return bool(user.is_authenticated)
        return isinstance(scope, Project) and scope.members.filter(pk=user.pk).exists()

    def mentionable_users(self, channel: Channel) -> QuerySet[AbstractBaseUser]:
        """Project members in public project channels."""
        scope = channel.scope
        if channel.is_private or not isinstance(scope, Project):
            return super().mentionable_users(channel)
        return get_user_model()._default_manager.filter(is_active=True, example_projects=scope)


class TicketResolver(ReferenceResolver):
    """``#T1`` in a project channel links to that project's ticket T1, for project members.

    DMs and unscoped channels have no project, so nothing resolves there.
    """

    kind = "ticket"

    def tickets(self, channel: Channel, user: Any) -> QuerySet[Ticket]:
        """Tickets of the channel's project, if ``user`` is a member."""
        scope = channel.scope
        if not isinstance(scope, Project):
            return Ticket.objects.none()
        return Ticket.objects.filter(project=scope, project__members=user)

    def search(self, channel: Channel, user: Any, query: str, *, limit: int) -> QuerySet[Ticket]:
        """Tickets whose code or title contains ``query``."""
        matches = self.tickets(channel, user).filter(Q(code__icontains=query) | Q(title__icontains=query))
        return matches.order_by("code")[:limit]

    def resolve_code(self, channel: Channel, user: Any, code: str) -> Ticket | None:
        """The ticket with this code (case-insensitive)."""
        return self.tickets(channel, user).filter(code__iexact=code).first()

    def get(self, channel: Channel, user: Any, ids: Collection[str]) -> QuerySet[Ticket]:
        """The visible tickets among ``ids``."""
        return self.tickets(channel, user).filter(pk__in=ids)

    def code(self, obj: Ticket) -> str:
        """Tickets are typed by their code."""
        return obj.code

    def label(self, obj: Ticket) -> str:
        """``#T1 Fix the door``."""
        return f"#{obj.code} {obj.title}"
