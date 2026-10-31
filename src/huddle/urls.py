"""URLs for the chat UI. Include with ``path("chat/", include("huddle.urls"))``."""

from __future__ import annotations

from django.urls import path

from huddle import views

app_name = "huddle"

urlpatterns = [
    path("", views.ChatHomeView.as_view(), name="home"),
    path("browse/", views.BrowseChannelsView.as_view(), name="browse"),
    path("new/", views.CreateChannelView.as_view(), name="create_channel"),
    path("dm/", views.DirectMessageView.as_view(), name="new_direct"),
    path("c/<int:channel_pk>/", views.ChannelView.as_view(), name="channel"),
    path("c/<int:channel_pk>/post/", views.PostMessageView.as_view(), name="post"),
    path("c/<int:channel_pk>/read/", views.MarkReadView.as_view(), name="mark_read"),
    path("c/<int:channel_pk>/join/", views.JoinChannelView.as_view(), name="join"),
    path("c/<int:channel_pk>/leave/", views.LeaveChannelView.as_view(), name="leave"),
    path("c/<int:channel_pk>/mentions/", views.MentionSearchView.as_view(), name="mentions"),
    path("c/<int:channel_pk>/references/", views.ReferenceSearchView.as_view(), name="references"),
    path("m/<int:message_pk>/thread/", views.ThreadView.as_view(), name="thread"),
    path("m/<int:message_pk>/reply/", views.ReplyView.as_view(), name="reply"),
    path("m/<int:message_pk>/edit/", views.EditMessageView.as_view(), name="edit"),
    path("m/<int:message_pk>/cancel-edit/", views.CancelEditView.as_view(), name="cancel_edit"),
    path("m/<int:message_pk>/delete/", views.DeleteMessageView.as_view(), name="delete"),
]
