"""URLs for the example project."""

from django.contrib import admin
from django.urls import include
from django.urls import path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("chat/", include("huddle.urls")),
    path("api/chat/", include("huddle.api.urls")),
]
