"""Input serializers for the REST API.

Responses are built by :class:`~huddle.payloads.ChatPayloads` (shared with the
websocket JSON mode); these serializers only validate what clients send.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from huddle.conf import get_setting
from huddle.forms import ChannelForm


class MessageInputSerializer(serializers.Serializer[Any]):
    """A new or edited message. ``mention_ids`` are the users the client chose to @mention."""

    text = serializers.CharField(trim_whitespace=True)
    mention_ids = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    parent_id = serializers.IntegerField(required=False, allow_null=True, default=None)

    def validate_text(self, value: str) -> str:
        """Enforce ``HUDDLE_MAX_MESSAGE_LENGTH``."""
        limit = get_setting("HUDDLE_MAX_MESSAGE_LENGTH")
        if len(value) > limit:
            raise serializers.ValidationError(f"Ensure this field has no more than {limit} characters.")
        return value


class ChannelInputSerializer(serializers.Serializer[Any]):
    """A new named channel. Names are normalised the same way as in the HTML form."""

    name = serializers.CharField(max_length=80)
    topic = serializers.CharField(max_length=250, required=False, allow_blank=True, default="")
    is_private = serializers.BooleanField(required=False, default=False)

    def validate_name(self, value: str) -> str:
        """Normalise to Slack style via :class:`~huddle.forms.ChannelForm`."""
        form = ChannelForm({"name": value, "topic": ""})
        if not form.is_valid() and "name" in form.errors:
            raise serializers.ValidationError([str(error) for error in form.errors["name"]])
        return str(form.cleaned_data["name"])


class DirectInputSerializer(serializers.Serializer[Any]):
    """The other people in a direct message."""

    user_ids = serializers.ListField(child=serializers.CharField(), min_length=1)
