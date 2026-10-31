"""REST API routes. Include with ``path("api/chat/", include("huddle.api.urls"))``."""

from __future__ import annotations

from rest_framework.routers import SimpleRouter

from huddle.api.views import ChannelViewSet
from huddle.api.views import MessageViewSet

app_name = "huddle-api"

router = SimpleRouter()
router.register("channels", ChannelViewSet, basename="channel")
router.register("messages", MessageViewSet, basename="message")

urlpatterns = router.urls
