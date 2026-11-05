"""Toy host models for the example project and the tests: projects with members, their tickets and milestones."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class Project(models.Model):
    """Something chat channels can be attached to (a channel's ``scope``)."""

    name = models.CharField(max_length=80)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="example_projects", blank=True)

    def __str__(self) -> str:
        return self.name


class Ticket(models.Model):
    """An item inside a project that chat messages can reference as ``#T1``, ``#T2``…"""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tickets")
    code = models.CharField(max_length=20)
    title = models.CharField(max_length=200)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "code"], name="example_unique_ticket_code")]

    def __str__(self) -> str:
        return self.code

    def get_absolute_url(self) -> str:
        """A made-up item page."""
        return f"/projects/{self.project_id}/tickets/{self.pk}/"


class Milestone(models.Model):
    """A project milestone that chat messages reference with a different trigger: ``!M1``, ``!M2``…"""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="milestones")
    code = models.CharField(max_length=20)
    title = models.CharField(max_length=200)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["project", "code"], name="example_unique_milestone_code")]

    def __str__(self) -> str:
        return self.code

    def get_absolute_url(self) -> str:
        """A made-up milestone page."""
        return f"/projects/{self.project_id}/milestones/{self.pk}/"
