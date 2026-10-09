# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import copy
import json
import os
import time

from django.db import transaction
from django.utils import timezone

from cvat.apps.dataset_manager.task import PatchAction, get_job_data, patch_job_data
from cvat.apps.engine.models import Job, Label
from cvat.apps.engine.serializers import LabeledDataSerializer

from .models import FixStatus, IssueStatus, QcIssue, QcRun
from .qc.geometry import Cuboid, read_pcd
from .qc.rules import DEFAULT_CONFIG, RULES_VERSION, QcContext, run_rules


def _jsonable(value):
    """numpy scalars/arrays -> plain JSON types."""
    return json.loads(json.dumps(value, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o)))


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
                        evidence=_jsonable(issue.evidence),
                        status=kept.status if kept else IssueStatus.OPEN,
                        decided_by=kept.decided_by if kept else None,
                        decided_date=kept.decided_date if kept else None,
                        decision_reason=kept.decision_reason if kept else "",
                        proposal=_jsonable(issue.proposal),
                        snapshot=_jsonable(
                            {"points": [*box.position, *box.rotation, *box.scale], "label": box.label}
                            if box else None
                        ),
                    )
                )
        QcIssue.objects.bulk_create(issues)
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.save(update_fields=["duration_ms"])
    return run


class FixError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _patch(job: Job, action: PatchAction, *, shapes=(), tracks=()) -> dict:
    serializer = LabeledDataSerializer(
        data={"version": 0, "tags": [], "shapes": list(shapes), "tracks": list(tracks)},
        context={"annotation_action": action},
    )
    serializer.is_valid(raise_exception=True)
    return patch_job_data(job.id, serializer.validated_data, action)


def _same(a: list[float], b: list[float], eps: float = 1e-3) -> bool:
    return len(a) >= 9 and len(b) >= 9 and all(abs(float(x) - float(y)) <= eps for x, y in zip(a[:9], b[:9]))


def _locate(job: Job, uid: str) -> tuple[str, dict | None, dict | None]:
    """uid -> (kind, CVAT object (shape or whole track), the cuboid shape QC looked at)."""
    kind, _, rest = uid.partition(":")
    data = get_job_data(job.id)
    if kind == "s":
        shape = next((s for s in data["shapes"] if s["id"] == int(rest)), None)
        return "shape", shape, shape
    track_id, _, frame = rest.partition(":")
    track = next((t for t in data["tracks"] if t["id"] == int(track_id)), None)
    keyframe = next((s for s in track["shapes"] if s["frame"] == int(frame)), None) if track else None
    return "track", track, keyframe


def _siblings(issue: QcIssue):
    """Issues of the same run and object that carry the same proposal: one fix resolves them all."""
    return [
        other for other in QcIssue.objects.filter(run=issue.run, uid=issue.uid)
        if other.proposal == issue.proposal
    ]


@transaction.atomic
def apply_fix(issue: QcIssue, user) -> QcIssue:
    """Write the proposed correction to the CVAT job, keeping the previous state for revert."""
    if not issue.proposal:
        raise FixError("Vấn đề này không có đề xuất sửa", 422)
    if issue.fix_status == FixStatus.APPLIED:
        raise FixError("Đề xuất đã được áp dụng", 409)

    job = issue.run.job
    kind, obj, target = _locate(job, issue.uid)
    if obj is None or target is None:
        raise FixError("Đối tượng không còn trên CVAT; chạy lại QC", 409)
    labels = dict(job.get_labels().values_list("id", "name"))
    if issue.snapshot and (
        not _same(target["points"], issue.snapshot["points"])
        or labels.get(obj["label_id"]) != issue.snapshot["label"]
    ):
        raise FixError("Nhãn đã bị thay đổi sau lần QC; chạy lại QC trước khi sửa", 409)

    before = copy.deepcopy(obj)
    if issue.proposal["kind"] == "delete":
        if kind == "track":
            raise FixError("Không tự xoá track; xoá keyframe trên CVAT", 422)
        _patch(job, PatchAction.DELETE, shapes=[obj])
    else:
        changes = issue.proposal["changes"]
        points = list(target["points"])
        for name, offset in (("position", 0), ("rotation", 3), ("scale", 6)):
            if name in changes:
                points[offset:offset + 3] = changes[name]
        target["points"] = points
        if "label" in changes:
            label_ids = {name: label_id for label_id, name in labels.items()}
            if changes["label"] not in label_ids:
                raise FixError(f"Schema chưa có class {changes['label']}; thêm label vào project trước", 422)
            obj["label_id"] = label_ids[changes["label"]]  # for a track: the class of the whole track
        if kind == "track":
            _patch(job, PatchAction.UPDATE, tracks=[obj])
        else:
            _patch(job, PatchAction.UPDATE, shapes=[obj])

    now = timezone.now()
    for other in _siblings(issue):
        other.fix_status = FixStatus.APPLIED
        other.fix_before = before
        other.fixed_by = user
        other.fixed_date = now
        other.status = IssueStatus.CONFIRMED
        other.decided_by = other.decided_by or user
        other.decided_date = other.decided_date or now
        other.save()
    issue.refresh_from_db()
    return issue


@transaction.atomic
def revert_fix(issue: QcIssue, user) -> QcIssue:
    """Restore the CVAT object exactly as it was before the fix."""
    if issue.fix_status != FixStatus.APPLIED or not issue.fix_before:
        raise FixError("Chưa có bản sửa nào để hoàn tác", 409)
    job = issue.run.job
    before = copy.deepcopy(issue.fix_before)
    kind, obj, _ = _locate(job, issue.uid)
    if issue.proposal and issue.proposal["kind"] == "delete":
        before.pop("id", None)  # the shape was deleted: re-create it
        _patch(job, PatchAction.CREATE, shapes=[before])
    else:
        if obj is None:
            raise FixError("Đối tượng đã bị xoá trên CVAT; không hoàn tác được", 409)
        if kind == "track":
            _patch(job, PatchAction.UPDATE, tracks=[before])
        else:
            _patch(job, PatchAction.UPDATE, shapes=[before])

    now = timezone.now()
    for other in _siblings(issue):
        other.fix_status = FixStatus.REVERTED
        other.fixed_by = user
        other.fixed_date = now
        other.save(update_fields=["fix_status", "fixed_by", "fixed_date"])
    issue.refresh_from_db()
    return issue


def decide_issue(issue: QcIssue, user, status: str, reason: str) -> QcIssue:
    issue.status = status
    issue.decided_by = user if status != IssueStatus.OPEN else None
    issue.decided_date = timezone.now() if status != IssueStatus.OPEN else None
    issue.decision_reason = reason
    issue.save(update_fields=["status", "decided_by", "decided_date", "decision_reason"])
    return issue
