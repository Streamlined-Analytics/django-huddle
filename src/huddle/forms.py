"""Forms for posting messages and creating channels and DMs.

Forms own the POST keys and validation; views stay thin wrappers that hand
cleaned data to :class:`~huddle.services.ChatService`.
"""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.validators import MaxLengthValidator
from django.utils.translation import gettext_lazy as _

from huddle.conf import get_policy
from huddle.conf import get_setting
from huddle.markup import display_name
from huddle.models import Channel


class MessageForm(forms.Form):
    """Compose or edit a message. ``mentions`` holds the ids the autocomplete picked."""

    text = forms.CharField(
        label=_("Message"),
        strip=True,
        widget=forms.Textarea(
            attrs={
                "rows": 1,
                "class": "huddle-composer__input",
                "placeholder": _("Write a message…"),
                "hx-on:keydown": "huddleComposerKeydown(event, this)",
                "hx-on:input": "huddleMentionInput(this)",
                "autocomplete": "off",
            }
        ),
    )
    mentions: forms.ModelMultipleChoiceField[Any] = forms.ModelMultipleChoiceField(
        queryset=None,
        required=False,
        widget=forms.MultipleHiddenInput,
    )

    def __init__(self, *args: Any, channel: Channel, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.channel = channel
        max_length = get_setting("HUDDLE_MAX_MESSAGE_LENGTH")
        text_field = self.fields["text"]
        text_field.validators.append(MaxLengthValidator(max_length))
        text_field.widget.attrs["maxlength"] = str(max_length)
        mentions_field = self.fields["mentions"]
        assert isinstance(mentions_field, forms.ModelMultipleChoiceField)  # noqa: S101
        mentions_field.queryset = get_policy().mentionable_users(channel)


class ChannelForm(forms.ModelForm):  # type: ignore[type-arg]
    """Create a named channel."""

    class Meta:
        model = Channel
        fields = ["name", "topic", "is_private"]

    def clean_name(self) -> str:
        """Normalise channel names to Slack style: lower case, hyphens, no leading ``#``."""
        name = str(self.cleaned_data["name"]).strip().lstrip("#").lower()
        name = "-".join(name.split())
        if not name:
            raise forms.ValidationError(_("Give the channel a name."))
        return name


class PersonChoiceField(forms.ModelMultipleChoiceField):  # type: ignore[type-arg]
    """Choose people, labelled by display name rather than username."""

    def label_from_instance(self, obj: Any) -> str:
        """Show each person's display name."""
        return display_name(obj)


class DirectMessageForm(forms.Form):
    """Pick the people to start (or reopen) a direct message with."""

    users = PersonChoiceField(queryset=None, label=_("People"))

    def __init__(self, *args: Any, user: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        users_field = self.fields["users"]
        assert isinstance(users_field, forms.ModelMultipleChoiceField)  # noqa: S101
        users_field.queryset = get_policy().direct_message_candidates(user)
