"""Django app configuration for django-huddle."""

from __future__ import annotations

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class HuddleConfig(AppConfig):
    """App config. Importing ``huddle.conf`` here registers its settings-change receiver."""

    name = "huddle"
    verbose_name = _("Huddle chat")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        from huddle import conf  # noqa: F401, PLC0415
