"""Signals for host projects, sent after the database transaction commits.

``message_posted(sender=Message, message, channel, author)``
    Any new message, top-level or reply.
``user_mentioned(sender=Message, message, user)``
    Once per user newly @mentioned in a message (posted or edited).
``thread_replied(sender=Message, message, thread, recipients)``
    A reply was posted; ``recipients`` are the thread's followers except the author.
``reference_added(sender=Message, message, reference, target)``
    Once per host object newly ``#``-referenced in a message (posted or edited).
    ``reference`` is the :class:`~huddle.models.MessageReference` row and
    ``target`` the object the resolver returned.
"""

from __future__ import annotations

from django.dispatch import Signal

message_posted = Signal()
user_mentioned = Signal()
thread_replied = Signal()
reference_added = Signal()
