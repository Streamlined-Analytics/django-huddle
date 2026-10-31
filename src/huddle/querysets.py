"""QuerySets for django-huddle models.

Visibility is never decided here: :meth:`ChannelQuerySet.visible_to` hands the
decision to the configured :class:`~huddle.policy.ChatPolicy`, so a host
project's permission rules apply to every channel lookup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Count
from django.db.models import Exists
from django.db.models import OuterRef
from django.db.models import Q
from django.db.models.functions import Coalesce

if TYPE_CHECKING:
    from huddle.models import Channel  # noqa: F401
    from huddle.models import Message
    from huddle.models import MessageReference  # noqa: F401


class ChannelQuerySet(models.QuerySet["Channel"]):
    """Channel lookups."""

    def visible_to(self, user: Any) -> ChannelQuerySet:
        """Channels ``user`` may see, as decided by the configured chat policy.

        Used by: every huddle view and the websocket consumer.
        """
        from huddle.conf import get_policy  # noqa: PLC0415

        return get_policy().filter_visible(self, user)

    def active(self) -> ChannelQuerySet:
        """Channels that are not archived.

        Used by: huddle.views sidebar.
        """
        return self.filter(archived_at__isnull=True)

    def named(self) -> ChannelQuerySet:
        """Named channels (not direct messages).

        Used by: huddle.views sidebar.
        """
        return self.filter(kind="channel")

    def direct(self) -> ChannelQuerySet:
        """Direct-message conversations.

        Used by: huddle.views sidebar, huddle.services.ChatService.open_direct.
        """
        return self.filter(kind="direct")

    def for_scope(self, scope: models.Model | None) -> ChannelQuerySet:
        """Channels attached to ``scope``, or unscoped channels when ``scope`` is ``None``.

        Used by: huddle.views (scoped channel lists), host projects.
        """
        if scope is None:
            return self.filter(scope_content_type__isnull=True)
        return self.filter(
            scope_content_type=ContentType.objects.get_for_model(scope),
            scope_object_id=str(scope.pk),
        )

    def with_membership_of(self, user: Any) -> ChannelQuerySet:
        """Annotate ``is_member`` and ``unread_count`` (top-level messages since last read) for ``user``.

        Used by: huddle.views sidebar.
        """
        from huddle.models import ChannelMembership  # noqa: PLC0415
        from huddle.models import Message  # noqa: PLC0415

        membership = ChannelMembership.objects.filter(channel=OuterRef("pk"), user=user)
        unread = (
            Message.objects.filter(
                channel=OuterRef("pk"),
                parent__isnull=True,
                deleted_at__isnull=True,
                created_at__gt=OuterRef("_huddle_read_from"),
            )
            .exclude(author=user)
            .order_by()
            .values("channel")
            .annotate(total=Count("pk"))
            .values("total")
        )
        annotated: ChannelQuerySet = self.annotate(
            is_member=Exists(membership),
            _huddle_read_from=Coalesce(
                models.Subquery(membership.values("last_read_at")[:1]),
                models.Subquery(membership.values("joined_at")[:1]),
            ),
        ).annotate(unread_count=Coalesce(models.Subquery(unread, output_field=models.IntegerField()), 0))
        return annotated


class MessageQuerySet(models.QuerySet["Message"]):
    """Message lookups."""

    def top_level(self) -> MessageQuerySet:
        """Messages in the main channel timeline (not thread replies).

        Used by: huddle.views channel timeline.
        """
        return self.filter(parent__isnull=True)

    def in_thread(self, root: Message) -> MessageQuerySet:
        """Replies to the thread started by ``root``, oldest first.

        Used by: huddle.views thread panel.
        """
        return self.filter(parent=root).order_by("created_at", "pk")

    def for_display(self) -> MessageQuerySet:
        """Messages with the relations templates need, avoiding N+1 queries.

        Used by: huddle.views, huddle.consumers.
        """
        return self.select_related("author", "channel")

    def latest_page(self, size: int, before: Message | None = None) -> list[Message]:
        """The ``size`` most recent messages (optionally older than ``before``), oldest first.

        Used by: huddle.views channel timeline and "load older" pagination.
        """
        queryset = self.order_by("-created_at", "-pk")
        if before is not None:
            queryset = queryset.filter(
                Q(created_at__lt=before.created_at) | Q(created_at=before.created_at, pk__lt=before.pk)
            )
        return list(reversed(queryset[:size]))


class MessageReferenceQuerySet(models.QuerySet["MessageReference"]):
    """Lookups of ``#`` references from messages to host objects."""

    def for_target(self, target: models.Model) -> MessageReferenceQuerySet:
        """References to the host object ``target`` (matched by content type and primary key).

        Used by: host projects ("discussed in chat" on an item).
        """
        return self.filter(content_type=ContentType.objects.get_for_model(target), object_id=str(target.pk))

    def visible_to(self, user: Any) -> MessageReferenceQuerySet:
        """References in messages ``user`` may read: visible channels, not deleted.

        Used by: host projects, before listing where an object was discussed.
        """
        from huddle.models import Channel  # noqa: PLC0415

        return self.filter(
            message__channel__in=Channel.objects.visible_to(user),
            message__deleted_at__isnull=True,
        )
