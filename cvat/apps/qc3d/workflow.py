# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Review / acceptance workflow of 3D jobs (doc/qc3d/12-nghiem-thu-api.md): job state, actions, the QA
acceptance sample, the job board and the overview map. The rules themselves are in qc/workflow_rules.py.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict

import numpy as np
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from cvat.apps.dataset_manager.task import get_job_data
from cvat.apps.engine.models import Job, Label

from .models import FixStatus, IssueStatus, QcAcceptance, QcJobAssignment, QcRun, QcWorkflowEvent
from .qc import workflow_rules as W
from .qc.guideline import acceptance_criteria
from .qc.objects import ego_motion

DECISIVE = ("accept", "sign_off", "reopen")  # the last of these tells accepted from signed off
MAX_OBJECTS, MAX_ISSUES = 3000, 2000


class WorkflowError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def _user(user) -> dict | None:
    return {"id": user.id, "username": user.username} if user else None


def _signed_off(job: Job) -> bool:
    last = job.qc3d_workflow_events.filter(action__in=DECISIVE).order_by("-date", "-id").first()
    return bool(last and last.action == "sign_off")


def status_of(job: Job) -> str:
    return W.job_status(job.stage, job.state, _signed_off(job))


def _annotator_id(job: Job) -> int | None:
    """The job's annotator: its last assignee at the annotation stage (the current one while annotating)."""
    if str(job.stage) == W.ANNOTATION and job.assignee_id:
        return job.assignee_id
    row = (QcJobAssignment.objects.filter(job=job, stage=W.ANNOTATION)
           .order_by("-started_date", "-id").values_list("user_id", flat=True).first())
    return row or job.assignee_id


def _qc_summary(job: Job, guideline: dict) -> dict:
    from .services import stale_reason

    run = job.qc3d_runs.first()
    if run is None:
        return {"run_id": None, "run_date": None, "whole_job": False, "stale_reason": "chưa chạy QC",
                "open": {"blocker": 0, "major": 0, "minor": 0}, "fixed": 0, "waived": 0, "total": 0}
    issues = run.issues.all()
    open_counts = Counter(issues.filter(status=IssueStatus.OPEN).values_list("severity", flat=True))
    return {
        "run_id": run.id,
        "run_date": run.created_date,
        "whole_job": run.frames is None,
        "stale_reason": stale_reason(job, run, guideline),
        "open": {severity: open_counts.get(severity, 0) for severity in ("blocker", "major", "minor")},
        "fixed": issues.filter(fix_status=FixStatus.APPLIED).count(),
        "waived": issues.filter(status=IssueStatus.WAIVED).count(),
        "total": issues.count(),
    }


def _open_acceptance(job: Job) -> QcAcceptance | None:
    return job.qc3d_acceptances.filter(closed_date=None).first()


def _acceptance_view(acceptance: QcAcceptance | None, open_blockers: int) -> dict | None:
    if acceptance is None:
        return None
    result = W.evaluate_acceptance(acceptance.frames, acceptance.results, acceptance.criteria, open_blockers)
    return {"id": acceptance.id, "round": acceptance.round, "seed": acceptance.seed, "results": acceptance.results,
            "closed": acceptance.closed_date is not None, **result}


def _context(job: Job, user, roles: set[str], qc: dict, acceptance: dict | None, criteria: dict) -> W.Context:
    return W.Context(
        status=status_of(job),
        roles=set(roles),
        is_annotator=user.id == _annotator_id(job),
        qc_whole_job=qc["whole_job"],
        qc_stale_reason=qc["stale_reason"],
        open_blockers=qc["open"]["blocker"],
        acceptance=acceptance,
        criteria=criteria,
    )


def _event_view(event: QcWorkflowEvent) -> dict:
    return {"id": event.id, "action": event.action, "label": W.ACTIONS[event.action].label
            if event.action in W.ACTIONS else event.action,
            "from_status": event.from_status, "to_status": event.to_status, "user": _user(event.user),
            "date": event.date, "note": event.note, "metrics": event.metrics}


def state(job: Job, user, roles: set[str]) -> dict:
    """GET jobs/{id}/workflow."""
    from .services import resolve_guideline

    guideline = resolve_guideline(job)
    criteria = acceptance_criteria(guideline["content"])
    qc = _qc_summary(job, guideline)
    acceptance = _acceptance_view(
        _open_acceptance(job) or job.qc3d_acceptances.first(), qc["open"]["blocker"])
    ctx = _context(job, user, roles, qc, acceptance if acceptance and not acceptance["closed"] else None, criteria)
    assignee = job.assignee
    return {
        "job": {"id": job.id, "task_id": job.segment.task_id, "task_name": job.segment.task.name,
                "frames": job.segment.stop_frame - job.segment.start_frame + 1,
                "start_frame": job.segment.start_frame, "assignee": _user(assignee)},
        "status": ctx.status,
        "status_text": W.STATUS_TEXT[ctx.status],
        "stage": str(job.stage),
        "state": str(job.state),
        "criteria": criteria,
        "qc": qc,
        "acceptance": acceptance,
        "actions": W.available(ctx),
        "history": [_event_view(e) for e in job.qc3d_workflow_events.select_related("user")[:200]],
    }


@transaction.atomic
def act(job: Job, user, roles: set[str], action: str, note: str = "", override: bool = False) -> dict:
    """POST jobs/{id}/workflow: one workflow step, recorded with the numbers it was decided on."""
    from .services import resolve_guideline

    job = Job.objects.select_for_update().select_related("segment__task").get(id=job.id)
    guideline = resolve_guideline(job)
    criteria = acceptance_criteria(guideline["content"])
    qc = _qc_summary(job, guideline)
    open_acceptance = _open_acceptance(job)
    acceptance = _acceptance_view(open_acceptance, qc["open"]["blocker"])
    ctx = _context(job, user, roles, qc, acceptance, criteria)
    code, reason = W.check(action, ctx, note=note, override=override)
    if code:
        raise WorkflowError(reason, code)

    spec = W.ACTIONS[action]
    metrics = {"run_id": qc["run_id"], "open": qc["open"], "fixed": qc["fixed"], "waived": qc["waived"]}
    if action == "start_acceptance":
        frames = list(range(job.segment.start_frame, job.segment.stop_frame + 1))
        seed = random.SystemRandom().randrange(1, 2**31)
        previous = job.qc3d_acceptances.count()
        open_acceptance = QcAcceptance.objects.create(
            job=job, round=previous + 1, seed=seed, frames=W.sample_frames(frames, criteria, seed),
            results={}, criteria=criteria, created_by=user,
        )
        metrics["acceptance"] = {"id": open_acceptance.id, "frames": open_acceptance.frames}
    if action in ("accept", "reject") and open_acceptance is not None:
        metrics["acceptance"] = {k: acceptance[k] for k in (
            "id", "round", "checked_frames", "boxes_checked", "errors", "error_rate", "passes")}
        metrics["override"] = bool(action == "accept" and acceptance["passes"] is False)
        open_acceptance.closed_date = timezone.now()
        open_acceptance.save(update_fields=["closed_date"])

    job.stage, job.state = spec.stage_state
    job.save(update_fields=["stage", "state", "updated_date"])
    QcWorkflowEvent.objects.create(job=job, action=action, from_status=ctx.status, to_status=spec.target,
                                   user=user, note=note.strip()[:2000], metrics=metrics)
    return state(job, user, roles)


@transaction.atomic
def record_frame(job: Job, user, roles: set[str], frame: int, value: dict) -> dict:
    """PUT jobs/{id}/acceptance/frames/{frame}: the QA's result on one sample frame."""
    job = Job.objects.select_for_update().get(id=job.id)
    if not roles & {W.LEAD, W.QA}:
        raise WorkflowError("Cần vai trò qa hoặc lead để chấm nghiệm thu", W.FORBIDDEN)
    if status_of(job) != "in_acceptance":
        raise WorkflowError("Job không ở trạng thái đang nghiệm thu", W.CONFLICT)
    acceptance = _open_acceptance(job)
    if acceptance is None or frame not in acceptance.frames:
        raise WorkflowError(f"Frame {frame} không thuộc mẫu nghiệm thu", W.CONFLICT)
    try:
        result = W.frame_result(value)
    except ValueError as error:
        raise WorkflowError(str(error), W.BAD_REQUEST) from None
    acceptance.results = {**acceptance.results, str(frame): {
        **result, "by": user.username, "date": timezone.now().isoformat()}}
    acceptance.save(update_fields=["results"])
    return state(job, user, roles)


# ---------------------------------------------------------------------------------------------- board


def board(jobs, *, status: str | None = None, task: int | None = None, assignee: int | None = None,
          search: str = "") -> dict:
    """GET workflow/board: every visible 3D job with its workflow status, QC and acceptance numbers."""
    if task:
        jobs = jobs.filter(segment__task_id=task)
    if assignee:
        jobs = jobs.filter(assignee_id=assignee)
    if search:
        jobs = jobs.filter(Q(segment__task__name__icontains=search) | Q(id__icontains=search))
    jobs = list(jobs.select_related("segment__task", "assignee").order_by("-updated_date")[:1000])
    ids = [job.id for job in jobs]

    decisive, last_action = {}, {}
    for event in QcWorkflowEvent.objects.filter(job_id__in=ids).select_related("user").order_by("job_id", "-date", "-id"):
        last_action.setdefault(event.job_id, event)
        if event.action in DECISIVE:
            decisive.setdefault(event.job_id, event.action)
    runs = {}
    for run in (QcRun.objects.filter(job_id__in=ids).order_by("job_id", "-id")
                .annotate(**{f"open_{s}": Count("issues", filter=Q(issues__status=IssueStatus.OPEN,
                                                                     issues__severity=s))
                             for s in ("blocker", "major", "minor")})):
        runs.setdefault(run.job_id, run)
    acceptances = {}
    for acceptance in QcAcceptance.objects.filter(job_id__in=ids).order_by("job_id", "-id"):
        acceptances.setdefault(acceptance.job_id, acceptance)

    rows = []
    for job in jobs:
        current = W.job_status(job.stage, job.state, decisive.get(job.id) == "sign_off")
        run = runs.get(job.id)
        qc_open = {s: getattr(run, f"open_{s}") for s in ("blocker", "major", "minor")} if run else None
        acceptance = acceptances.get(job.id)
        error_rate = None
        if acceptance is not None:
            error_rate = W.evaluate_acceptance(acceptance.frames, acceptance.results, acceptance.criteria,
                                               qc_open["blocker"] if qc_open else 0)["error_rate"]
        event = last_action.get(job.id)
        rows.append({
            "id": job.id, "task_id": job.segment.task_id, "task_name": job.segment.task.name,
            "frames": job.segment.stop_frame - job.segment.start_frame + 1,
            "status": current, "status_text": W.STATUS_TEXT[current], "assignee": _user(job.assignee),
            "qc_open": qc_open, "error_rate": error_rate,
            "last_action": {"action": event.action, "user": event.user.username if event.user else None,
                            "date": event.date} if event else None,
            "updated_date": job.updated_date,
        })
    counts = Counter(row["status"] for row in rows)
    if status:
        rows = [row for row in rows if row["status"] == status]
    return {"columns": [{"status": key, "text": text, "count": counts.get(key, 0)} for key, text in W.STATUSES],
            "jobs": rows}


# ---------------------------------------------------------------------------------------- overview map


def _world_transforms(by_frame: dict[int, list]) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    """frame -> (A, b) with world (first frame) = A @ p + b, chaining the ego motion between frames."""
    frames = sorted(by_frame)
    transforms = {}
    a, b = np.eye(2), np.zeros(2)
    for index, frame in enumerate(frames):
        if index:
            motion = ego_motion(by_frame[frames[index - 1]], by_frame[frame])
            if motion is not None:
                r, t = motion  # p_current = r @ p_previous + t
                a, b = a @ r.T, b - a @ r.T @ t
        transforms[frame] = (a.copy(), b.copy())
    return transforms


def overview(job: Job) -> dict:
    """GET jobs/{id}/overview: bird's-eye map of the job with the ego path, the objects and the QC issues."""
    from .services import build_cuboids

    data = get_job_data(job.id)
    label_ids = {s["label_id"] for s in data["shapes"]} | {t["label_id"] for t in data["tracks"]}
    labels = dict(Label.objects.filter(id__in=label_ids).values_list("id", "name"))
    cuboids = [c for c in build_cuboids(data, labels) if all(math.isfinite(v) for v in c.position)]
    by_frame = defaultdict(list)
    for box in cuboids:
        by_frame[box.frame].append(box)
    for frame in range(job.segment.start_frame, job.segment.stop_frame + 1):
        by_frame.setdefault(frame, [])
    transforms = _world_transforms(by_frame)

    def world(frame: int, x: float, y: float) -> tuple[float, float]:
        a, b = transforms.get(frame, (np.eye(2), np.zeros(2)))
        p = a @ np.array([x, y]) + b
        return round(float(p[0]), 2), round(float(p[1]), 2)

    trajectory = []
    for frame, (a, b) in sorted(transforms.items()):
        trajectory.append({"frame": frame, "x": round(float(b[0]), 2), "y": round(float(b[1]), 2),
                           "yaw": round(math.atan2(a[1, 0], a[0, 0]), 4)})
    step = max(1, math.ceil(len(cuboids) / MAX_OBJECTS))
    objects = [{"frame": c.frame, "label": c.label, **dict(zip("xy", world(c.frame, *c.position[:2])))}
               for c in cuboids[::step]]

    by_uid = {c.uid: c for c in cuboids}
    issues = []
    run = job.qc3d_runs.first()
    if run is not None:
        for issue in run.issues.order_by("frame", "id")[:MAX_ISSUES]:
            box = by_uid.get(issue.uid)
            if box is not None:
                x, y = box.position[:2]
            elif issue.evidence.get("model_box"):
                x, y = issue.evidence["model_box"][:2]
            else:
                x, y = 0.0, 0.0  # a frame-level issue: at the vehicle
            wx, wy = world(issue.frame, x, y)
            issues.append({"issue_id": issue.id, "uid": issue.uid, "frame": issue.frame, "rule": issue.rule,
                           "severity": issue.severity, "label": issue.label, "status": issue.status,
                           "fix_status": issue.fix_status, "x": wx, "y": wy})
    xs = [p["x"] for p in trajectory + objects + issues]
    ys = [p["y"] for p in trajectory + objects + issues]
    extent = [min(xs) - 5, min(ys) - 5, max(xs) + 5, max(ys) + 5] if xs else [-50, -50, 50, 50]
    return {"method": "estimated", "extent": [round(v, 1) for v in extent], "trajectory": trajectory,
            "objects": objects, "issues": issues, "task_id": job.segment.task_id, "run_id": run.id if run else None}
