# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.conf import settings
from django.db import models


class QcRole(models.TextChoices):
    LEAD = "lead"
    REVIEWER = "reviewer"
    QA = "qa"
    ML_ENGINEER = "ml_engineer"


class QcMembership(models.Model):
    """QC 3D role of a CVAT user. CVAT admins have every role implicitly."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qc3d_memberships",
    )
    role = models.CharField(max_length=32, choices=QcRole.choices)
    created_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="qc3d_unique_user_role"),
        ]

    def __str__(self) -> str:
        return f"{self.user} ({self.role})"


class QcRun(models.Model):
    """One QC pass over a CVAT job; issues are attached to it."""

    job = models.ForeignKey("engine.Job", on_delete=models.CASCADE, related_name="qc3d_runs")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_date = models.DateTimeField(auto_now_add=True)
    rules_version = models.CharField(max_length=32)
    annotations_version = models.IntegerField(default=0)
    objects_count = models.IntegerField(default=0)
    duration_ms = models.IntegerField(default=0)
    # [{"rule": "R05", "status": "checked", "reason": "", "issues": 3}, ...]
    coverage = models.JSONField(default=list)

    class Meta:
        ordering = ("-id",)


class IssueSeverity(models.TextChoices):
    BLOCKER = "blocker"
    MAJOR = "major"
    MINOR = "minor"


class IssueStatus(models.TextChoices):
    OPEN = "open"
    CONFIRMED = "confirmed"  # a real labeling error, to be fixed in CVAT
    WAIVED = "waived"  # reviewed, label kept as is (reason required)


class QcIssue(models.Model):
    run = models.ForeignKey(QcRun, on_delete=models.CASCADE, related_name="issues")
    rule = models.CharField(max_length=8)
    severity = models.CharField(max_length=16, choices=IssueSeverity.choices)
    frame = models.IntegerField()
    uid = models.CharField(max_length=64)  # "s:<shape_id>" | "t:<track_id>:<frame>"
    label = models.CharField(max_length=64, blank=True, default="")
    message = models.TextField()
    evidence = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=IssueStatus.choices, default=IssueStatus.OPEN)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_date = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("frame", "rule", "id")
        indexes = [models.Index(fields=["run", "status"])]
