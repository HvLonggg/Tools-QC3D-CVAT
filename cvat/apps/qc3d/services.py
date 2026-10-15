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
from cvat.apps.engine.models import AnnotationGuide, Job, Label
from cvat.apps.engine.serializers import LabeledDataSerializer

from .models import FixStatus, IssueStatus, QcGuideline, QcGuidelineBinding, QcIssue, QcRun
from .qc.autofix import plan_autofix
from .qc.geometry import Cuboid, read_pcd
from .qc.guideline import AUTOFIX_KINDS, GuidelineError, default_autofix, parse_file, qc_config
from .qc.objects import reference_sizes
from .qc.rules import RULES_VERSION, QcContext, run_rules
from .qc.taxonomy import TAXONOMY_VERSION

GUIDELINE_SOURCES = {
    "task": "Guideline QC gán cho task",
    "cvat_guide_task": "Guide CVAT của task",
    "project": "Guideline QC gán cho project",
    "cvat_guide_project": "Guide CVAT của project",
    "default": "Guideline QC mặc định",
    "builtin": "Bộ class VN dựng sẵn",
}


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


def load_point_clouds(job: Job, frames: set[int], *, with_intensity: bool = False) -> dict:
    data = job.segment.task.data
    raw_dir = data.get_raw_data_dirname()
    paths = dict(data.images.filter(frame__in=frames).values_list("frame", "path"))
    point_clouds = {}
    for frame, path in paths.items():
        full_path = os.path.join(raw_dir, path)
        if os.path.isfile(full_path):
            point_clouds[frame] = read_pcd(full_path, with_intensity=with_intensity)
    return point_clouds


def _stored(guideline: QcGuideline, source: str) -> dict:
    return {
        "source": source, "id": guideline.id, "name": guideline.name, "revision": guideline.revision,
        "content": guideline.content, "warnings": [],
    }


def _cvat_guide(target, kind: str, skipped: list[str]) -> dict | None:
    """A CVAT annotation guide (markdown written on the task / project page) that contains a class table."""
    guide = AnnotationGuide.objects.filter(**{kind: target}).first()
    if guide is None or not guide.markdown.strip():
        return None
    try:
        content, warnings = parse_file("guide.md", guide.markdown.encode("utf-8"))
    except GuidelineError as e:
        skipped.append(f"{GUIDELINE_SOURCES['cvat_guide_' + kind]}: {e.errors[0]}")
        return None
    return {
        "source": f"cvat_guide_{kind}", "id": guide.id,
        "name": f"{GUIDELINE_SOURCES['cvat_guide_' + kind]} #{target.id}",
        "revision": guide.updated_date.isoformat(timespec="seconds"),
        "content": content, "warnings": warnings,
    }


def resolve_guideline(job: Job) -> dict:
    """
    Guideline applied to a job, most specific first:
    task binding > task CVAT guide > project binding > project CVAT guide > default > built-in taxonomy.
    """
    task = job.segment.task
    skipped: list[str] = []
    resolved = None
    binding = QcGuidelineBinding.objects.select_related("guideline").filter(task=task).first()
    if binding:
        resolved = _stored(binding.guideline, "task")
    if resolved is None:
        resolved = _cvat_guide(task, "task", skipped)
    if resolved is None and task.project_id:
        binding = QcGuidelineBinding.objects.select_related("guideline").filter(project_id=task.project_id).first()
        resolved = _stored(binding.guideline, "project") if binding else _cvat_guide(task.project, "project", skipped)
    if resolved is None:
        default = QcGuideline.objects.filter(is_default=True).first()
        if default:
            resolved = _stored(default, "default")
    if resolved is None:
        resolved = {
            "source": "builtin", "id": None, "name": GUIDELINE_SOURCES["builtin"], "revision": TAXONOMY_VERSION,
            "content": None, "warnings": [],
        }
    resolved["source_text"] = GUIDELINE_SOURCES[resolved["source"]]
    resolved["key"] = f"{resolved['source']}:{resolved['id']}:{resolved['revision']}"
    resolved["skipped"] = skipped
    return resolved


def guideline_snapshot(resolved: dict) -> dict:
    """What a QC run records about its guideline (enough to reproduce the run, without the guide text)."""
    content = resolved["content"] or {}
    return {
        **{k: resolved[k] for k in ("source", "id", "name", "revision", "key")},
        "classes": content.get("classes"),
        "thresholds": content.get("thresholds", {}),
        "rules": content.get("rules", {}),
        "autofix": content.get("autofix") or default_autofix(),
    }


def run_job_qc(job: Job, user, frames: set[int] | None = None, marked: set[str] | None = None) -> QcRun:
    """
    QC of the job, or of some of its frames (`frames`); `marked`: boxes a reviewer marked as wrong, each
    gets a complete correction (rule R14).
    """
    started = time.monotonic()
    job_data = get_job_data(job.id)
    label_ids = {s["label_id"] for s in job_data["shapes"]} | {t["label_id"] for t in job_data["tracks"]}
    labels = dict(Label.objects.filter(id__in=label_ids).values_list("id", "name"))
    all_cuboids = build_cuboids(job_data, labels)
    cuboids = all_cuboids if frames is None else [c for c in all_cuboids if c.frame in frames]

    guideline = resolve_guideline(job)
    config = qc_config(guideline["content"])
    task_labels = list(job.get_labels().values_list("name", flat=True))
    config["labels"] = task_labels or config["labels"]

    from .detection import detections_for

    detections = detections_for(job, frames)
    # frames the model found objects on are needed too, to check new boxes against the points
    cloud_frames = {c.frame for c in cuboids} | set(detections or {})
    ctx = QcContext(
        cuboids=cuboids,
        point_clouds=load_point_clouds(job, cloud_frames),
        config=config,
        detections=detections,
        # the size of an object comes from all the frames it is seen in, also when only some are checked
        reference=reference_sizes([c for c in all_cuboids if c.label], config["classes"]),
        marked=set(marked or ()),
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
            guideline=_jsonable(guideline_snapshot(guideline)),
            frames=sorted(frames) if frames is not None else None,
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
                        # an object the model saw without any label (R12): the model's class
                        label=box.label if box else str(issue.evidence.get("model_class", "")),
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


def _create_boxes(job: Job, proposal: dict, excluded: set[str]) -> dict:
    """Add the new cuboids of a create proposal; returns what revert needs (the ids of the new shapes)."""
    label_ids = {name: label_id for label_id, name in job.get_labels().values_list("id", "name")}
    shapes = [shape for shape in proposal["shapes"] if shape["label"] not in excluded]
    if not shapes:
        raise FixError("Không còn box nào thuộc phạm vi class được chọn", 422)
    missing = sorted({shape["label"] for shape in shapes} - set(label_ids))
    if missing:
        raise FixError(f"Schema chưa có class {', '.join(missing)}; thêm label vào project trước", 422)
    data = _patch(job, PatchAction.CREATE, shapes=[
        {
            "type": "cuboid", "frame": shape["frame"], "label_id": label_ids[shape["label"]],
            "points": [*shape["points"], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            "occluded": False, "outside": False, "z_order": 0, "rotation": 0.0, "group": 0,
            "source": "auto", "attributes": [],
        }
        for shape in shapes
    ])
    return {"created": [shape["id"] for shape in data["shapes"]], "labels": [s["label"] for s in shapes]}


@transaction.atomic
def apply_fix(issue: QcIssue, user, *, excluded: set[str] = frozenset()) -> QcIssue:
    """
    Write the proposed correction to the CVAT job, keeping the previous state for revert.
    A create proposal adds new boxes (classes in `excluded` are left out).
    """
    if not issue.proposal:
        raise FixError("Vấn đề này không có đề xuất sửa", 422)
    if issue.fix_status == FixStatus.APPLIED:
        raise FixError("Đề xuất đã được áp dụng", 409)

    job = issue.run.job
    if issue.proposal["kind"] == "create":
        before = _create_boxes(job, issue.proposal, set(excluded))
        _mark_applied(issue, user, before)
        issue.refresh_from_db()
        return issue
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

    _mark_applied(issue, user, before)
    issue.refresh_from_db()
    return issue


def _mark_applied(issue: QcIssue, user, before) -> None:
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


@transaction.atomic
def revert_fix(issue: QcIssue, user) -> QcIssue:
    """Restore the CVAT object exactly as it was before the fix."""
    if issue.fix_status != FixStatus.APPLIED or not issue.fix_before:
        raise FixError("Chưa có bản sửa nào để hoàn tác", 409)
    job = issue.run.job
    before = copy.deepcopy(issue.fix_before)
    if issue.proposal and issue.proposal["kind"] == "create":
        created = set(before.get("created", []))
        shapes = [s for s in get_job_data(job.id)["shapes"] if s["id"] in created]
        if shapes:
            _patch(job, PatchAction.DELETE, shapes=shapes)
        kind = obj = None
    else:
        kind, obj, _ = _locate(job, issue.uid)
    if issue.proposal and issue.proposal["kind"] == "create":
        pass  # the added boxes are deleted above
    elif issue.proposal and issue.proposal["kind"] == "delete":
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


def _autofix_plan(run: QcRun, excluded: set[str], allowed_kinds: set[str]) -> tuple[list[QcIssue], list[dict]]:
    """One issue per object proposal that auto-fix may write; and what it leaves to the reviewer."""
    return plan_autofix(
        run.issues.exclude(proposal=None).order_by("frame", "id"), excluded,
        applied_status=FixStatus.APPLIED, waived_status=IssueStatus.WAIVED, allowed_kinds=allowed_kinds,
    )


def stale_reason(job: Job, run: QcRun, guideline: dict) -> str | None:
    """Why the proposals of `run` no longer describe the job (None: still current)."""
    if run.rules_version != RULES_VERSION:
        return f"bộ rule đã đổi ({run.rules_version} → {RULES_VERSION})"
    if (run.guideline or {}).get("key") != guideline["key"]:
        return "guideline áp dụng cho job đã thay đổi"
    if run.annotations_version != get_job_data(job.id).get("version", 0):
        return "nhãn đã thay đổi sau lần QC"
    return None


def marked_frames(job: Job, uids: list[str]) -> set[int]:
    """Frames of the boxes a reviewer marked ("s:<shape>" or "t:<track>:<frame>")."""
    data = get_job_data(job.id)
    shape_frames = {s["id"]: s["frame"] for s in data["shapes"]}
    frames = set()
    for uid in uids:
        kind, _, rest = uid.partition(":")
        if kind == "s" and rest.isdigit() and int(rest) in shape_frames:
            frames.add(shape_frames[int(rest)])
        elif kind == "t" and rest.count(":") == 1:
            frames.add(int(rest.split(":")[1]))
    return frames


def autofix_marked(job: Job, user, run: QcRun, uids: set[str]) -> dict:
    """
    Write the verified corrections of the marked boxes only (run made with `marked`), following the
    kinds of change the guideline allows; every other box is left as it is.
    """
    rules = (run.guideline or {}).get("autofix") or default_autofix()
    allowed = {kind for kind in AUTOFIX_KINDS if rules.get("fix", {}).get(kind, True)} - {"create"}
    issues = run.issues.filter(uid__in=uids).exclude(proposal=None).order_by("frame", "id")
    targets, skipped = plan_autofix(issues, set(), applied_status=FixStatus.APPLIED,
                                    waived_status=IssueStatus.WAIVED, allowed_kinds=allowed)
    applied, failed = [], []
    for issue in targets:
        try:
            apply_fix(issue, user)
            applied.append({"uid": issue.uid, "frame": issue.frame, "summary": issue.proposal["summary"]})
        except FixError as e:
            failed.append({"uid": issue.uid, "frame": issue.frame, "reason": str(e)})
    fixed = {item["uid"] for item in applied}
    for uid in sorted(uids - fixed - {item["uid"] for item in failed}):
        issue = run.issues.filter(uid=uid, rule="R14").first()
        reason = next((item["reason"] + (": " + "; ".join(item["checks"][:2]) if item.get("checks") else "")
                       for item in skipped if item.get("uid") == uid), "")
        if not reason and issue is not None:
            proposal = issue.proposal or {}
            reason = "; ".join(proposal.get("checks", [])) or issue.evidence.get("no_fix", "") or (
                "box đã đúng, không cần sửa" if not proposal else "")
        failed.append({"uid": uid, "frame": issue.frame if issue else None, "reason": reason or "không có đề xuất sửa"})
    return {"run": run.id, "applied": applied, "failed": failed}


def autofix_job(job: Job, user, excluded: set[str], *, dry_run: bool = False, create: bool = True) -> dict:
    """
    Write every verified proposal of a current QC run to CVAT, following the job's guideline:
    the run is redone first when the labels, the guideline or the rules changed since; classes excluded
    by the reviewer or by the guideline, and kinds of change the guideline turns off, are never written.
    """
    run = job.qc3d_runs.first()
    if run is None:
        raise FixError("Job chưa chạy QC", 409)
    guideline = resolve_guideline(job)
    # a QC run takes up to a minute on a large job: it is redone in the background (QC check), not here
    stale = stale_reason(job, run, guideline)
    rules = (run.guideline or {}).get("autofix") or default_autofix()
    guideline_excluded = set(rules.get("exclude_classes", []))
    allowed = {kind for kind in AUTOFIX_KINDS if rules.get("fix", {}).get(kind, True)}
    if not create:
        allowed.discard("create")  # turned off for this job
    targets, skipped = _autofix_plan(run, excluded | guideline_excluded, allowed)
    result = {
        "run": run.id,
        "stale_reason": stale,
        "frames": run.frames,
        "guideline": {
            "name": (run.guideline or {}).get("name", ""),
            "excluded_labels": sorted(guideline_excluded),
            "allowed_kinds": [AUTOFIX_KINDS[k] for k in AUTOFIX_KINDS if k in allowed],
            "blocked_kinds": [AUTOFIX_KINDS[k] for k in AUTOFIX_KINDS if k not in allowed],
        },
        "planned": [
            {"issue": i.id, "uid": i.uid, "frame": i.frame, "label": i.label, "summary": i.proposal["summary"],
             "kinds": i.proposal.get("kinds", []),
             "new_boxes": [s["label"] for s in i.proposal.get("shapes", []) if s["label"] not in excluded | guideline_excluded]}
            for i in targets
        ],
        "skipped": skipped,
        "applied": [],
        "failed": [],
    }
    if stale:
        result["planned"], result["skipped"] = [], []
        if not dry_run:
            raise FixError(f"Kết quả QC đã cũ ({stale}): chạy QC lại trước", 409)
    if dry_run:
        return result
    for issue in targets:
        try:
            apply_fix(issue, user, excluded=excluded | guideline_excluded)
            result["applied"].append(issue.id)
        except FixError as e:
            result["failed"].append({"issue": issue.id, "uid": issue.uid, "frame": issue.frame, "reason": str(e)})
    return result
