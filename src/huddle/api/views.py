"""REST API viewsets.

The same rules as the HTML views: everything is looked up through
``Channel.objects.visible_to(user)`` (invisible means 404), every write goes
through :class:`~huddle.services.ChatService`, and responses are built by
:class:`~huddle.payloads.ChatPayloads`.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Prefetch
from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from huddle.api.serializers import ChannelInputSerializer
from huddle.api.serializers import DirectInputSerializer
from huddle.api.serializers import MessageInputSerializer
from huddle.conf import get_policy
from huddle.conf import get_reference_registry
from huddle.conf import get_setting
from huddle.exceptions import ChatPermissionDeniedError
from huddle.exceptions import HuddleError
from huddle.markup import display_name
from huddle.models import Channel
from huddle.models import ChannelMembership
from huddle.models import Message
from huddle.payloads import ChatPayloads
from huddle.querysets import ChannelQuerySet
from huddle.rendering import ChatRenderer
from huddle.services import ChatService

MAX_PAGE_SIZE = 200
MENTION_RESULTS_LIMIT = 8
REFERENCE_RESULTS_LIMIT = 8


class HuddleAPIViewSet(viewsets.ViewSet):
    """Base viewset: shared lookups, payloads and error mapping."""

    permission_classes = [IsAuthenticated]  # noqa: RUF012

    def handle_exception(self, exc: Exception) -> Response:
        """Map service errors: permission failures to 403, rule violations to 400."""
        if isinstance(exc, ChatPermissionDeniedError):
            exc = PermissionDenied(str(exc))
        elif isinstance(exc, HuddleError):
            exc = ValidationError({"detail": str(exc)})
        return super().handle_exception(exc)

    @property
    def chat_user(self) -> Any:
        """The authenticated user."""
        return self.request.user

    @property
    def service(self) -> ChatService:
        """The chat service used for every write."""
        return ChatService()

    @property
    def payloads(self) -> ChatPayloads:
        """Payload builder for the requesting user."""
        return ChatPayloads(self.chat_user)

    def visible_channels(self) -> ChannelQuerySet:
        """Every channel the user may see."""
        return Channel.objects.visible_to(self.chat_user)

    def get_channel(self) -> Channel:
        """The channel in the URL, or 404."""
        channel: Channel = get_object_or_404(self.visible_channels(), pk=self.kwargs["pk"])
        return channel

    def get_message(self) -> Message:
        """The message in the URL, or 404 if its channel is not visible."""
        messages = Message.objects.filter(channel__in=self.visible_channels()).select_related(
            "channel", "author", "parent"
        )
        message: Message = get_object_or_404(messages, pk=self.kwargs["pk"])
        return message

    def channel_payload(self, channel_pk: Any) -> dict[str, Any]:
        """A channel with this user's membership and unread count."""
        channel = (
            self.visible_channels()
            .with_membership_of(self.chat_user)
            .prefetch_related(Prefetch("memberships", queryset=ChannelMembership.objects.select_related("user")))
            .get(pk=channel_pk)
        )
        renderer = ChatRenderer(self.chat_user)
        return self.payloads.channel(renderer.channel_entries([channel])[0])

    def users_by_ids(self, ids: list[str]) -> list[Any]:
        """Users with these primary keys (unknown ids are ignored)."""
        return list(get_user_model()._default_manager.filter(pk__in=ids))

    def page_size(self) -> int:
        """``?limit=`` bounded to 1..200, defaulting to ``HUDDLE_PAGE_SIZE``."""
        try:
            limit = int(self.request.query_params.get("limit", get_setting("HUDDLE_PAGE_SIZE")))
        except ValueError:
            limit = get_setting("HUDDLE_PAGE_SIZE")
        return max(1, min(limit, MAX_PAGE_SIZE))


class ChannelViewSet(HuddleAPIViewSet):
    """Channels and DMs: list, create, join, leave, read, and their messages.

    Hosts that attach channels to their own objects subclass this and override
    :meth:`get_scope`.
    """

    def get_scope(self) -> Any:
        """The object new channels attach to. ``None`` creates unscoped channels."""
        return None

    def list(self, request: Request) -> Response:
        """The user's channels and DMs; ``?browse=true`` lists every visible named channel instead."""
        channels = self.visible_channels().active().with_membership_of(self.chat_user)
        if request.query_params.get("browse") == "true":
            channels = channels.named().filter(Q(is_private=False) | Q(memberships__user=self.chat_user)).distinct()
        else:
            channels = channels.filter(memberships__user=self.chat_user)
        channels = channels.prefetch_related(
            Prefetch("memberships", queryset=ChannelMembership.objects.select_related("user"))
        ).order_by("kind", "name", "pk")
        entries = ChatRenderer(self.chat_user).channel_entries(channels)
        return Response(self.payloads.channels(entries))

    def retrieve(self, request: Request, pk: str) -> Response:
        """One channel."""
        return Response(self.channel_payload(self.get_channel().pk))

    def create(self, request: Request) -> Response:
        """Create a named channel."""
        serializer = ChannelInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        channel = self.service.create_channel(self.chat_user, scope=self.get_scope(), **serializer.validated_data)
        return Response(self.channel_payload(channel.pk), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def direct(self, request: Request) -> Response:
        """Open (or reopen) a direct message with ``user_ids``."""
        serializer = DirectInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        others = self.users_by_ids(serializer.validated_data["user_ids"])
        if len(others) != len(set(serializer.validated_data["user_ids"])):
            raise ValidationError({"user_ids": "Unknown user."})
        channel = self.service.open_direct(self.chat_user, others)
        return Response(self.channel_payload(channel.pk))

    @action(detail=True, methods=["post"])
    def join(self, request: Request, pk: str) -> Response:
        """Join a channel."""
        channel = self.get_channel()
        self.service.join(self.chat_user, channel)
        return Response(self.channel_payload(channel.pk))

    @action(detail=True, methods=["post"])
    def leave(self, request: Request, pk: str) -> Response:
        """Leave a channel."""
        self.service.leave(self.chat_user, self.get_channel())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def read(self, request: Request, pk: str) -> Response:
        """Mark the channel read up to now."""
        self.service.mark_read(self.chat_user, self.get_channel())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get", "post"])
    def messages(self, request: Request, pk: str) -> Response:
        """GET: a page of top-level messages (``?before=<id>&limit=``). POST: post a message or reply."""
        channel = self.get_channel()
        if request.method == "POST":
            return self._post_message(channel)
        before = None
        if request.query_params.get("before"):
            before = get_object_or_404(Message.objects.filter(channel=channel), pk=request.query_params["before"])
        size = self.page_size()
        page = Message.objects.filter(channel=channel).top_level().for_display().latest_page(size, before=before)
        return Response({"results": self.payloads.messages(page), "has_older": len(page) == size})

    @action(detail=True, methods=["get"])
    def mentionable(self, request: Request, pk: str) -> Response:
        """People who may be @mentioned here, filtered by ``?q=``."""
        channel = self.get_channel()
        query = request.query_params.get("q", "").strip().lstrip("@").lower()
        people = get_policy().mentionable_users(channel).exclude(pk=self.chat_user.pk).order_by("pk")[:200]
        matches = [user for user in people if not query or query in display_name(user).lower()]
        return Response([self.payloads.person(user) for user in matches[:MENTION_RESULTS_LIMIT]])

    @action(detail=True, methods=["get"])
    def references(self, request: Request, pk: str) -> Response:
        """Host objects that may be ``#``-referenced here, filtered by ``?q=``."""
        channel = self.get_channel()
        query = request.query_params.get("q", "").strip().lstrip("#")
        found = get_reference_registry().search(channel, self.chat_user, query, limit=REFERENCE_RESULTS_LIMIT)
        return Response([reference.as_dict() for reference in found])

    def _post_message(self, channel: Channel) -> Response:
        serializer = MessageInputSerializer(data=self.request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        parent = None
        if data["parent_id"] is not None:
            parent = get_object_or_404(
                Message.objects.filter(channel__in=self.visible_channels()), pk=data["parent_id"]
            )
        message = self.service.post_message(
            self.chat_user,
            channel,
            data["text"],
            mentioned=self.users_by_ids(data["mention_ids"]),
            references=data["reference_ids"],
            parent=parent,
        )
        return Response(self.payloads.message(message), status=status.HTTP_201_CREATED)


class MessageViewSet(HuddleAPIViewSet):
    """Single messages: read, edit, delete, and their thread."""

    def retrieve(self, request: Request, pk: str) -> Response:
        """One message."""
        return Response(self.payloads.message(self.get_message()))

    def partial_update(self, request: Request, pk: str) -> Response:
        """Edit a message's text (and mentions and references)."""
        message = self.get_message()
        serializer = MessageInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        self.service.edit_message(
            self.chat_user,
            message,
            data["text"],
            mentioned=self.users_by_ids(data["mention_ids"]),
            references=data["reference_ids"],
        )
        return Response(self.payloads.message(message))

    def destroy(self, request: Request, pk: str) -> Response:
        """Soft-delete a message."""
        self.service.delete_message(self.chat_user, self.get_message())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def replies(self, request: Request, pk: str) -> Response:
        """The replies in this message's thread, oldest first."""
        parent = self.get_message()
        replies = list(Message.objects.in_thread(parent).for_display())
        return Response({"parent": self.payloads.message(parent), "results": self.payloads.messages(replies)})

    @action(detail=True, methods=["post"])
    def read(self, request: Request, pk: str) -> Response:
        """Mark this thread read up to now."""
        self.service.mark_thread_read(self.chat_user, self.get_message())
        return Response(status=status.HTTP_204_NO_CONTENT)
