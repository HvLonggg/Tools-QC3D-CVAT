# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import os
import time

from django.db import transaction
from django.utils import timezone

from cvat.apps.dataset_manager.task import get_job_data
from cvat.apps.engine.models import Job, Label

from .models import IssueStatus, QcIssue, QcRun
from .qc.geometry import Cuboid, read_pcd
from .qc.rules import DEFAULT_CONFIG, RULES_VERSION, QcContext, run_rules


def _cuboid(uid, frame, label, points, *, kind, source_id, track_id=None) -> Cuboid:
    values = [float(v) for v in points[:9]]
    return Cuboid(
        uid=uid,
        frame=frame,
        label=label,
        position=tuple(values[0:3]),
        rotation=tuple(values[3:6]),
        scale=tuple(values[6:9]),
        track_id=track_id,
        source_kind=kind,
        source_id=source_id,
    )


def build_cuboids(job_data: dict, labels: dict[int, str]) -> list[Cuboid]:
    """CVAT job annotations -> cuboids. Tracks contribute their keyframes only."""
    cuboids = []
    for shape in job_data["shapes"]:
        if shape["type"] != "cuboid":
            continue
        cuboids.append(
            _cuboid(f"s:{shape['id']}", shape["frame"], labels.get(shape["label_id"], ""),
                    shape["points"], kind="shape", source_id=shape["id"])
        )
    for track in job_data["tracks"]:
        label = labels.get(track["label_id"], "")
        for shape in track["shapes"]:
            if shape["type"] != "cuboid" or shape.get("outside"):
                continue
            cuboids.append(
                _cuboid(f"t:{track['id']}:{shape['frame']}", shape["frame"], label, shape["points"],
                        kind="track", source_id=track["id"], track_id=track["id"])
            )
    return cuboids


def load_point_clouds(job: Job, frames: set[int]) -> dict:
    data = job.segment.task.data
    raw_dir = data.get_raw_data_dirname()
    paths = dict(data.images.filter(frame__in=frames).values_list("frame", "path"))
    point_clouds = {}
    for frame, path in paths.items():
        full_path = os.path.join(raw_dir, path)
        if os.path.isfile(full_path):
            point_clouds[frame] = read_pcd(full_path)
    return point_clouds


def run_job_qc(job: Job, user) -> QcRun:
    started = time.monotonic()
    job_data = get_job_data(job.id)
    label_ids = {s["label_id"] for s in job_data["shapes"]} | {t["label_id"] for t in job_data["tracks"]}
    labels = dict(Label.objects.filter(id__in=label_ids).values_list("id", "name"))
    cuboids = build_cuboids(job_data, labels)

    config = dict(DEFAULT_CONFIG)
    task_labels = list(job.get_labels().values_list("name", flat=True))
    config["labels"] = task_labels or config["labels"]

    ctx = QcContext(
        cuboids=cuboids,
        point_clouds=load_point_clouds(job, {c.frame for c in cuboids}),
        config=config,
    )
    results = run_rules(ctx)
    by_uid = {c.uid: c for c in cuboids}

    # decisions made on the previous run carry over when the same rule flags the same object again
    previous = {
        (i.rule, i.uid, i.frame): i
        for i in QcIssue.objects.filter(run__job=job).exclude(status=IssueStatus.OPEN).order_by("id")
    }

    with transaction.atomic():
        run = QcRun.objects.create(
            job=job,
            created_by=user,
            rules_version=RULES_VERSION,
            annotations_version=job_data.get("version", 0),
            objects_count=len(cuboids),
            coverage=[
                {"rule": r.rule, "status": r.status, "reason": r.reason, "issues": len(r.issues)}
                for r in results
            ],
        )
        issues = []
        for result in results:
            for issue in result.issues:
                kept = previous.get((issue.rule, issue.uid, issue.frame))
                box = by_uid.get(issue.uid)
                issues.append(
                    QcIssue(
                        run=run,
                        rule=issue.rule,
                        severity=issue.severity,
                        frame=issue.frame,
                        uid=issue.uid,
                        label=box.label if box else "",
                        message=issue.message,
                        evidence=issue.evidence,
                        status=kept.status if kept else IssueStatus.OPEN,
                        decided_by=kept.decided_by if kept else None,
                        decided_date=kept.decided_date if kept else None,
                        decision_reason=kept.decision_reason if kept else "",
                    )
                )
        QcIssue.objects.bulk_create(issues)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.save(update_fields=["duration_ms"])
    return run


def decide_issue(issue: QcIssue, user, status: str, reason: str) -> QcIssue:
    issue.status = status
    issue.decided_by = user if status != IssueStatus.OPEN else None
    issue.decided_date = timezone.now() if status != IssueStatus.OPEN else None
    issue.decision_reason = reason
    issue.save(update_fields=["status", "decided_by", "decided_date", "decision_reason"])
    return issue
