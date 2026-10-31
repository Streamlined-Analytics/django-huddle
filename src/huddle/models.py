"""Database models for django-huddle.

A :class:`Channel` is any conversation: a named channel or a direct message.
Channels may hang off a host object (a project, a team) through a generic
*scope*; the host's :class:`~huddle.policy.ChatPolicy` decides who can see a
scope. :class:`Message` rows form Slack-style threads: a top-level message has
``parent=None`` and replies point at it, never at another reply.
"""

from __future__ import annotations

from typing import ClassVar

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from huddle.querysets import ChannelQuerySet
from huddle.querysets import MessageQuerySet
from huddle.querysets import MessageReferenceQuerySet


class Channel(models.Model):
    """A conversation: a named channel, or a direct message between users."""

    class Kind(models.TextChoices):
        CHANNEL = "channel", _("Channel")
        DIRECT = "direct", _("Direct message")

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CHANNEL)
    name = models.CharField(max_length=80, blank=True)
    topic = models.CharField(max_length=250, blank=True)
    is_private = models.BooleanField(
        default=False,
        help_text=_("Private channels are visible to their members only. Direct messages are always private."),
    )
    scope_content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    scope_object_id = models.CharField(max_length=64, blank=True)
    scope = GenericForeignKey("scope_content_type", "scope_object_id")
    direct_key = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        editable=False,
        help_text=_("Sorted member ids of a direct message, so each group of people has one DM."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
    )
    created_at = models.DateTimeField(default=timezone.now)
    archived_at = models.DateTimeField(null=True, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)

    objects: ClassVar[ChannelQuerySet] = ChannelQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        ordering = ["name", "pk"]
        indexes = [
            models.Index(fields=["scope_content_type", "scope_object_id"], name="huddle_channel_scope"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind="direct") | Q(is_private=True),
                name="huddle_direct_is_private",
            ),
            models.CheckConstraint(
                condition=Q(kind="direct") | ~Q(name=""),
                name="huddle_channel_has_name",
            ),
        ]

    def __str__(self) -> str:
        if self.is_direct:
            return f"DM {self.direct_key}"
        return f"#{self.name}"

    @property
    def is_direct(self) -> bool:
        """Whether this is a direct message rather than a named channel."""
        return self.kind == self.Kind.DIRECT

    @property
    def is_archived(self) -> bool:
        """Whether the channel has been archived (read-only)."""
        return self.archived_at is not None


class ChannelMembership(models.Model):
    """A user's membership of a channel, and how far they have read."""

    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="huddle_memberships")
    joined_at = models.DateTimeField(default=timezone.now)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["channel", "user"], name="huddle_unique_membership"),
        ]

    def __str__(self) -> str:
        return f"{self.user} in {self.channel}"


class Message(models.Model):
    """A chat message. Replies set ``parent`` to the thread's top-level message."""

    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="huddle_messages",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="replies",
    )
    body = models.TextField(
        help_text=_("Raw text. Mentions are stored as <@user-pk> tokens and references as <#kind:id> tokens."),
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    reply_count = models.PositiveIntegerField(default=0)
    last_reply_at = models.DateTimeField(null=True, blank=True)

    objects: ClassVar[MessageQuerySet] = MessageQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        ordering = ["created_at", "pk"]
        indexes = [
            models.Index(fields=["channel", "parent", "created_at"], name="huddle_message_timeline"),
        ]

    def __str__(self) -> str:
        return f"Message {self.pk} in {self.channel_id}"

    @property
    def is_deleted(self) -> bool:
        """Whether the message has been soft-deleted."""
        return self.deleted_at is not None

    @property
    def is_reply(self) -> bool:
        """Whether this message is a reply inside a thread."""
        return self.parent_id is not None


class Mention(models.Model):
    """A user @mentioned in a message."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="mentions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="huddle_mentions")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["message", "user"], name="huddle_unique_mention"),
        ]

    def __str__(self) -> str:
        return f"@{self.user} in {self.message_id}"


class MessageReference(models.Model):
    """A host object referenced from a message with ``#code``, stored as a ``<#kind:id>`` token.

    Rows mirror the tokens the service wrote into the body, so hosts can list
    where an object was discussed (``MessageReference.objects.for_target(obj)``).
    Only tokens with a row render as links, which keeps references typed into
    bodies before reference support existed from becoming live. ``content_type``
    is set when the resolver returned a model instance.
    """

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="references")
    kind = models.CharField(max_length=64)
    object_id = models.CharField(max_length=64)
    content_type = models.ForeignKey(
        ContentType,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    target = GenericForeignKey("content_type", "object_id")
    created_at = models.DateTimeField(default=timezone.now)

    objects: ClassVar[MessageReferenceQuerySet] = MessageReferenceQuerySet.as_manager()  # type: ignore[assignment]

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["message", "kind", "object_id"], name="huddle_unique_reference"),
        ]
        indexes = [
            models.Index(fields=["kind", "object_id"], name="huddle_reference_kind"),
            models.Index(fields=["content_type", "object_id"], name="huddle_reference_target"),
        ]

    def __str__(self) -> str:
        return f"#{self.kind}:{self.object_id} in {self.message_id}"

    @property
    def token(self) -> str:
        """The ``kind:id`` text of this reference's token."""
        return f"{self.kind}:{self.object_id}"


class ThreadParticipant(models.Model):
    """A user following a thread: its author, repliers and anyone mentioned in it."""

    thread = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="huddle_threads")
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["thread", "user"], name="huddle_unique_thread_participant"),
        ]

    def __str__(self) -> str:
        return f"{self.user} following {self.thread_id}"
