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
    # guideline applied: {"id", "name", "revision", "source": "task" | "project" | "default" | "builtin"}
    guideline = models.JSONField(default=dict)
    frames = models.JSONField(null=True, blank=True)  # checked frames; None: the whole job

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


class FixStatus(models.TextChoices):
    NONE = "none"
    APPLIED = "applied"  # written to CVAT
    REVERTED = "reverted"  # CVAT restored to the state before the fix


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

    # proposed correction: {"kind": "update", "changes": {...}, "summary": "..."} | {"kind": "delete", ...}
    proposal = models.JSONField(null=True, blank=True)
    # object state when QC ran: {"points": [9 floats], "label": "..."}; guards against stale fixes
    snapshot = models.JSONField(null=True, blank=True)
    fix_status = models.CharField(max_length=16, choices=FixStatus.choices, default=FixStatus.NONE)
    fix_before = models.JSONField(null=True, blank=True)  # full CVAT shape before the fix (for revert)
    fixed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    fixed_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("frame", "rule", "id")
        indexes = [models.Index(fields=["run", "status"])]


class QcGuideline(models.Model):
    """A labeling guideline (classes, sizes, thresholds, rules) that QC applies to the jobs it is bound to."""

    name = models.CharField(max_length=256)
    description = models.TextField(blank=True, default="")
    content = models.JSONField()  # normalized by qc.guideline.normalize
    revision = models.IntegerField(default=1)  # +1 on every content change
    source_name = models.CharField(max_length=256, blank=True, default="")  # imported file
    is_default = models.BooleanField(default=False)  # used by jobs without a task / project binding
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_date",)

    def __str__(self) -> str:
        return f"{self.name} r{self.revision}"


class QcGuidelineBinding(models.Model):
    """Applies a guideline to a whole project or to one task (the task binding wins)."""

    guideline = models.ForeignKey(QcGuideline, on_delete=models.CASCADE, related_name="bindings")
    project = models.OneToOneField(
        "engine.Project", on_delete=models.CASCADE, null=True, blank=True, related_name="qc3d_guideline"
    )
    task = models.OneToOneField(
        "engine.Task", on_delete=models.CASCADE, null=True, blank=True, related_name="qc3d_guideline"
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(project__isnull=False, task__isnull=True)
                | models.Q(project__isnull=True, task__isnull=False),
                name="qc3d_binding_project_xor_task",
            ),
        ]


class QcJobSettings(models.Model):
    """Per-job QC choices made by the reviewer in the annotation view."""

    job = models.OneToOneField("engine.Job", on_delete=models.CASCADE, related_name="qc3d_settings")
    # classes the reviewer does not trust the automatic fix for: auto-fix never touches them
    autofix_excluded_labels = models.JSONField(default=list)
    # whether auto-fix also adds boxes for objects the model sees without a label
    autofix_create = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    updated_date = models.DateTimeField(auto_now=True)


class QcJobAssignment(models.Model):
    """
    Who held a job at which stage. CVAT keeps only the current assignee, which becomes the reviewer when
    the job moves to validation: this history keeps the annotator, so each person can follow the QC of
    the jobs they labeled. Written by signals.py on every assignee / stage change.
    """

    job = models.ForeignKey("engine.Job", on_delete=models.CASCADE, related_name="qc3d_assignments")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    stage = models.CharField(max_length=32)
    started_date = models.DateTimeField()
    ended_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("job_id", "started_date", "id")
        indexes = [models.Index(fields=["user", "stage"]), models.Index(fields=["job", "ended_date"])]


class QcDetection(models.Model):
    """Output of the detection model on one frame of a job (computed once, read by QC rule R12)."""

    job = models.ForeignKey("engine.Job", on_delete=models.CASCADE, related_name="qc3d_detections")
    frame = models.IntegerField()
    model_key = models.CharField(max_length=64)
    # [[x, y, z, length, width, height, yaw, label, score], ...] in the job's LiDAR frame, score >= 0.3
    boxes = models.JSONField(default=list)
    ground_z = models.FloatField()  # road height used to put the cloud in the model's frame
    created_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["job", "frame", "model_key"], name="qc3d_unique_detection"),
        ]


class QcWorkflowEvent(models.Model):
    """One step of a job's review / acceptance workflow: who did what, when, with which numbers."""

    job = models.ForeignKey("engine.Job", on_delete=models.CASCADE, related_name="qc3d_workflow_events")
    action = models.CharField(max_length=32)
    from_status = models.CharField(max_length=32)
    to_status = models.CharField(max_length=32)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    date = models.DateTimeField(auto_now_add=True)
    note = models.TextField(blank=True, default="")
    metrics = models.JSONField(default=dict)  # QC / acceptance numbers at the time of the step

    class Meta:
        ordering = ("-date", "-id")
        indexes = [models.Index(fields=["job", "date"])]


class QcAcceptance(models.Model):
    """Frames a QA checks to accept a job (one sample per acceptance round) and what they found."""

    job = models.ForeignKey("engine.Job", on_delete=models.CASCADE, related_name="qc3d_acceptances")
    round = models.IntegerField(default=1)
    seed = models.IntegerField()
    frames = models.JSONField(default=list)
    # "frame" -> {"boxes_checked", "errors", "note", "by", "date"}
    results = models.JSONField(default=dict)
    criteria = models.JSONField(default=dict)  # guideline acceptance criteria when the sample was drawn
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_date = models.DateTimeField(auto_now_add=True)
    closed_date = models.DateTimeField(null=True, blank=True)  # accepted or rejected

    class Meta:
        ordering = ("-id",)
