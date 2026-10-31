"""Views for the chat UI.

Thin wrappers: forms validate, :class:`~huddle.services.ChatService` writes,
:class:`~huddle.rendering.ChatRenderer` draws. Every channel and message is
looked up through ``Channel.objects.visible_to(user)``, so anything a user may
not see is a 404, never a 403 that would confirm it exists.

Every fragment is its own template under ``huddle/components/``, so a full
page and an htmx swap render the same markup, and a host can restyle one
piece by overriding one small file.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import BadRequest
from django.core.exceptions import PermissionDenied
from django.db.models import Prefetch
from django.db.models import Q
from django.http import HttpRequest
from django.http import HttpResponse
from django.http import HttpResponseBase
from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views import View
from django.views.generic import FormView

from huddle.conf import get_policy
from huddle.conf import get_reference_registry
from huddle.conf import get_setting
from huddle.exceptions import ChatPermissionDeniedError
from huddle.exceptions import InvalidReferenceIdError
from huddle.forms import ChannelForm
from huddle.forms import DirectMessageForm
from huddle.forms import MessageForm
from huddle.markup import display_name
from huddle.models import Channel
from huddle.models import ChannelMembership
from huddle.models import Message
from huddle.querysets import ChannelQuerySet
from huddle.rendering import ChatRenderer
from huddle.services import ChatService

COMPONENTS = "huddle/components"
MENTION_RESULTS_LIMIT = 8
REFERENCE_RESULTS_LIMIT = 8


class HuddleViewMixin(LoginRequiredMixin):
    """Shared lookups and helpers for every huddle view."""

    request: HttpRequest
    kwargs: dict[str, Any]

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        """Turn service permission errors into 403s and unusable picked references into 400s.

        Lookups have already 404'd invisible objects.
        """
        try:
            return super().dispatch(request, *args, **kwargs)
        except ChatPermissionDeniedError as error:
            raise PermissionDenied(str(error)) from error
        except InvalidReferenceIdError as error:
            raise BadRequest(str(error)) from error

    @property
    def chat_user(self) -> Any:
        """The signed-in user (``LoginRequiredMixin`` has already rejected anonymous requests)."""
        return self.request.user

    @property
    def service(self) -> ChatService:
        """The chat service used for every write."""
        return ChatService()

    @property
    def renderer(self) -> ChatRenderer:
        """A renderer for the requesting user."""
        return ChatRenderer(self.chat_user)

    def visible_channels(self) -> ChannelQuerySet:
        """Every channel the requesting user may see."""
        return Channel.objects.visible_to(self.chat_user)

    def get_channel(self) -> Channel:
        """The channel named in the URL, or 404."""
        channel: Channel = get_object_or_404(self.visible_channels(), pk=self.kwargs["channel_pk"])
        return channel

    def get_message(self) -> Message:
        """The message named in the URL, or 404 if its channel is not visible."""
        messages = Message.objects.filter(channel__in=self.visible_channels()).select_related(
            "channel", "author", "parent"
        )
        return get_object_or_404(messages, pk=self.kwargs["message_pk"])

    def sidebar_context(self, current: Channel | None = None) -> dict[str, Any]:
        """Sidebar rows for the channels and DMs the user belongs to."""
        user = self.chat_user
        channels = (
            self.visible_channels()
            .active()
            .with_membership_of(user)
            .filter(memberships__user=user)
            .prefetch_related(Prefetch("memberships", queryset=ChannelMembership.objects.select_related("user")))
            .order_by("name", "pk")
        )
        entries = self.renderer.channel_entries(channels)
        return {
            "named_entries": [entry for entry in entries if not entry.channel.is_direct],
            "direct_entries": [entry for entry in entries if entry.channel.is_direct],
            "current_channel": current,
        }

    def chat_response(self, template: str, context: dict[str, Any]) -> TemplateResponse:
        """Render a page (``"page/channel"``) or a fragment of the chat template (``"composer"``)."""
        if template.startswith("page/"):
            name = f"huddle/{template.removeprefix('page/')}_page.html"
        else:
            name = f"{COMPONENTS}/{template}.html"
        return TemplateResponse(self.request, name, {**self.base_context(), **context})

    def base_context(self) -> dict[str, Any]:
        """Context every chat template needs."""
        return {
            "viewer": self.chat_user,
            "base_template": get_setting("HUDDLE_BASE_TEMPLATE"),
            "websocket_url": get_setting("HUDDLE_WEBSOCKET_URL"),
        }

    def message_form(self, channel: Channel, **kwargs: Any) -> MessageForm:
        """A message form bound to the request when it is a POST."""
        data = self.request.POST if self.request.method == "POST" else None
        return MessageForm(data, channel=channel, **kwargs)


class ChatHomeView(HuddleViewMixin, View):
    """Open the most recently active channel, or show the empty state."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Redirect to the latest channel the user belongs to."""
        latest = (
            self.visible_channels()
            .active()
            .filter(memberships__user=self.chat_user)
            .order_by("-last_message_at", "-pk")
            .first()
        )
        if latest is not None:
            return redirect("huddle:channel", channel_pk=latest.pk)
        return self.chat_response("page/home", self.sidebar_context())


class ChannelView(HuddleViewMixin, View):
    """A channel's timeline. With ``?before=<id>`` it returns the next page of older messages."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Render the channel page (or an older page) and mark the channel read."""
        channel = self.get_channel()
        timeline = Message.objects.filter(channel=channel).top_level().for_display()
        before = self._before_message(channel)
        messages = timeline.latest_page(get_setting("HUDDLE_PAGE_SIZE"), before=before)
        context = {
            "channel": channel,
            "channel_label": self.renderer.channel_label(channel),
            "message_views": self.renderer.message_views(messages),
            "has_older": len(messages) == get_setting("HUDDLE_PAGE_SIZE"),
            "oldest": messages[0] if messages else None,
            "is_member": channel.memberships.filter(user_id=self.chat_user.pk).exists(),
            "can_post": get_policy().can_post(self.chat_user, channel),
            "form": self.message_form(channel),
        }
        if before is not None:
            return self.chat_response("timeline_page", context)
        self.service.mark_read(self.chat_user, channel)
        return self.chat_response("page/channel", {**context, **self.sidebar_context(channel)})

    def _before_message(self, channel: Channel) -> Message | None:
        before_pk = self.request.GET.get("before")
        if not before_pk:
            return None
        return get_object_or_404(Message.objects.filter(channel=channel), pk=before_pk)


class PostMessageView(HuddleViewMixin, View):
    """Post a message to a channel, returning a fresh composer plus the message."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Validate and post; on error, return the composer with errors."""
        channel = self.get_channel()
        form = self.message_form(channel)
        if not form.is_valid():
            return self.chat_response("composer", {"channel": channel, "form": form, "can_post": True})
        message = self.service.post_message(
            self.chat_user,
            channel,
            form.cleaned_data["text"],
            mentioned=form.cleaned_data["mentions"],
            references=form.cleaned_data["references"],
        )
        return self.chat_response(
            "posted",
            {
                "channel": channel,
                "form": MessageForm(channel=channel),
                "can_post": True,
                "message_view": self.renderer.message_view(message),
            },
        )


class ThreadView(HuddleViewMixin, View):
    """A thread panel: the parent message, its replies and a reply composer."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Render the thread and mark it read."""
        parent = self.get_message()
        if parent.parent_id is not None:
            return redirect("huddle:thread", message_pk=parent.parent_id)
        self.service.mark_thread_read(self.chat_user, parent)
        return self.chat_response("thread", self.thread_context(parent))

    def thread_context(self, parent: Message, form: MessageForm | None = None) -> dict[str, Any]:
        """Context for the thread panel."""
        replies = Message.objects.in_thread(parent).for_display()
        return {
            "parent_view": self.renderer.message_view(parent),
            "reply_views": self.renderer.message_views(replies),
            "channel": parent.channel,
            "form": form or MessageForm(channel=parent.channel),
            "can_post": get_policy().can_post(self.chat_user, parent.channel),
        }


class ReplyView(ThreadView):
    """Post a reply into a thread."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Validate and post the reply, returning the refreshed reply composer and the reply."""
        parent = self.get_message()
        form = self.message_form(parent.channel)
        if not form.is_valid():
            return self.chat_response("reply_composer", {"parent": parent, "form": form, "can_post": True})
        reply = self.service.post_message(
            self.chat_user,
            parent.channel,
            form.cleaned_data["text"],
            mentioned=form.cleaned_data["mentions"],
            references=form.cleaned_data["references"],
            parent=parent,
        )
        return self.chat_response(
            "replied",
            {
                "parent": parent,
                "form": MessageForm(channel=parent.channel),
                "can_post": True,
                "message_view": self.renderer.message_view(reply),
            },
        )


class EditMessageView(HuddleViewMixin, View):
    """Edit a message in place."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Show the inline edit form."""
        message = self.get_message()
        if not get_policy().can_edit_message(self.chat_user, message):
            raise PermissionDenied
        message_view = self.renderer.message_view(message)
        form = MessageForm(
            channel=message.channel,
            initial={
                "text": message_view.text,
                "mentions": list(message.mentions.values_list("user", flat=True)),
                "references": [reference.token for reference in message_view.references],
            },
        )
        return self.chat_response("edit_form", {"message": message, "form": form})

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Save the edit and return the updated message."""
        message = self.get_message()
        form = self.message_form(message.channel)
        if not form.is_valid():
            return self.chat_response("edit_form", {"message": message, "form": form})
        self.service.edit_message(
            self.chat_user,
            message,
            form.cleaned_data["text"],
            mentioned=form.cleaned_data["mentions"],
            references=form.cleaned_data["references"],
        )
        return self.chat_response("message", {"message_view": self.renderer.message_view(message)})


class CancelEditView(HuddleViewMixin, View):
    """Abandon an edit and show the message again."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Return the message unchanged."""
        return self.chat_response("message", {"message_view": self.renderer.message_view(self.get_message())})


class DeleteMessageView(HuddleViewMixin, View):
    """Soft-delete a message."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Delete and return the placeholder."""
        message = self.get_message()
        self.service.delete_message(self.chat_user, message)
        return self.chat_response("message", {"message_view": self.renderer.message_view(message)})


class MentionSearchView(HuddleViewMixin, View):
    """Autocomplete people who may be @mentioned in a channel."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Match ``q`` against the mentionable users' display names and emails."""
        channel = self.get_channel()
        query = request.GET.get("q", "").strip().lstrip("@").lower()
        candidates = get_policy().mentionable_users(channel).exclude(pk=self.chat_user.pk)
        matches = [user for user in candidates.order_by("pk")[:200] if self._matches(user, query)]
        people = [{"pk": user.pk, "name": display_name(user)} for user in matches[:MENTION_RESULTS_LIMIT]]
        return self.chat_response("mention_results", {"people": people})

    @staticmethod
    def _matches(user: Any, query: str) -> bool:
        if not query:
            return True
        return query in display_name(user).lower() or query in str(getattr(user, "email", "")).lower()


class ReferenceSearchView(HuddleViewMixin, View):
    """Autocomplete host objects that may be ``#``-referenced in a channel."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Ask every configured resolver for matches to ``q``, in settings order."""
        channel = self.get_channel()
        query = request.GET.get("q", "").strip().lstrip("#")
        references = get_reference_registry().search(channel, self.chat_user, query, limit=REFERENCE_RESULTS_LIMIT)
        return self.chat_response("reference_results", {"references": references})


class MarkReadView(HuddleViewMixin, View):
    """Mark a channel read (called when new messages arrive while it is open)."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Record the read and return no content."""
        self.service.mark_read(self.chat_user, self.get_channel())
        return HttpResponse(status=204)


class BrowseChannelsView(HuddleViewMixin, View):
    """Public channels the user can see, to join."""

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """List visible named channels with membership flags."""
        channels = (
            self.visible_channels()
            .active()
            .named()
            .with_membership_of(self.chat_user)
            .filter(Q(is_private=False) | Q(memberships__user=self.chat_user))
        )
        return self.chat_response(
            "page/browse",
            {"browse_entries": self.renderer.channel_entries(channels.distinct()), **self.sidebar_context()},
        )


class JoinChannelView(HuddleViewMixin, View):
    """Join a public channel."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Join, then open the channel."""
        channel = self.get_channel()
        self.service.join(self.chat_user, channel)
        return redirect("huddle:channel", channel_pk=channel.pk)


class LeaveChannelView(HuddleViewMixin, View):
    """Leave a channel."""

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Leave, then go back to the chat home."""
        self.service.leave(self.chat_user, self.get_channel())
        return redirect("huddle:home")


class CreateChannelView(HuddleViewMixin, FormView):  # type: ignore[type-arg]
    """Create an unscoped named channel. Hosts subclass and override :meth:`get_scope` for scoped ones."""

    form_class = ChannelForm

    def get_scope(self) -> Any:
        """The object new channels attach to. ``None`` creates unscoped channels."""
        return None

    template_name = "huddle/create_channel_page.html"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add the sidebar and base template."""
        return {
            **super().get_context_data(**kwargs),
            **self.sidebar_context(),
            **self.base_context(),
        }

    def form_valid(self, form: ChannelForm) -> HttpResponse:
        """Create the channel through the service and open it."""
        channel = self.service.create_channel(
            self.chat_user,
            form.cleaned_data["name"],
            scope=self.get_scope(),
            is_private=form.cleaned_data["is_private"],
            topic=form.cleaned_data["topic"],
        )
        return redirect(reverse("huddle:channel", kwargs={"channel_pk": channel.pk}))


class DirectMessageView(HuddleViewMixin, FormView):  # type: ignore[type-arg]
    """Start or reopen a direct message."""

    form_class = DirectMessageForm

    template_name = "huddle/new_direct_page.html"

    def get_form_kwargs(self) -> dict[str, Any]:
        """Pass the user so the form offers only allowed people."""
        return {**super().get_form_kwargs(), "user": self.chat_user}

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add the sidebar and base template."""
        return {
            **super().get_context_data(**kwargs),
            **self.sidebar_context(),
            **self.base_context(),
        }

    def form_valid(self, form: DirectMessageForm) -> HttpResponse:
        """Open the DM through the service and go to it."""
        channel = self.service.open_direct(self.chat_user, form.cleaned_data["users"])
        return redirect(reverse("huddle:channel", kwargs={"channel_pk": channel.pk}))
