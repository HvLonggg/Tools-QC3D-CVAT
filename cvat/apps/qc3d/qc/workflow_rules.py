# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Review and acceptance workflow of a 3D job, without Django (doc/qc3d/12-nghiem-thu-api.md):

  in_progress -submit-> waiting_review -start_review-> in_review -review_pass-> waiting_acceptance
  -start_acceptance-> in_acceptance -accept-> accepted -sign_off-> signed_off
  review_return / reject -> returned (back to the annotator); reopen (lead) -> in_progress

The status lives in the job's CVAT stage / state, so CVAT's own job views stay right; QC3D keeps the
history and the acceptance sample.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

ANNOTATION, VALIDATION, ACCEPTANCE = "annotation", "validation", "acceptance"
NEW, IN_PROGRESS, COMPLETED, REJECTED = "new", "in progress", "completed", "rejected"

STATUSES = [
    ("in_progress", "Đang gán nhãn"),
    ("returned", "Bị trả lại"),
    ("waiting_review", "Chờ QC"),
    ("in_review", "Đang QC"),
    ("waiting_acceptance", "Chờ nghiệm thu"),
    ("in_acceptance", "Đang nghiệm thu"),
    ("accepted", "Đạt nghiệm thu"),
    ("signed_off", "Đã chốt"),
]
STATUS_TEXT = dict(STATUSES)

LEAD, REVIEWER, QA = "lead", "reviewer", "qa"


@dataclass(frozen=True)
class Action:
    label: str
    sources: frozenset[str]
    target: str
    stage_state: tuple[str, str]  # CVAT job stage / state after the action
    roles: frozenset[str]  # QC roles allowed (the job's annotator is checked separately for submit)
    note_required: bool = False


ACTIONS: dict[str, Action] = {
    "submit": Action("Nộp để QC", frozenset({"in_progress", "returned"}), "waiting_review",
                     (ANNOTATION, COMPLETED), frozenset({LEAD})),
    "start_review": Action("Bắt đầu QC", frozenset({"waiting_review"}), "in_review",
                           (VALIDATION, IN_PROGRESS), frozenset({LEAD, REVIEWER})),
    "review_pass": Action("QC đạt", frozenset({"in_review"}), "waiting_acceptance",
                          (ACCEPTANCE, NEW), frozenset({LEAD, REVIEWER})),
    "review_return": Action("Trả lại người gán nhãn", frozenset({"waiting_review", "in_review"}), "returned",
                            (ANNOTATION, REJECTED), frozenset({LEAD, REVIEWER}), note_required=True),
    "start_acceptance": Action("Bắt đầu nghiệm thu", frozenset({"waiting_acceptance"}), "in_acceptance",
                               (ACCEPTANCE, IN_PROGRESS), frozenset({LEAD, QA})),
    "accept": Action("Đạt nghiệm thu", frozenset({"in_acceptance"}), "accepted",
                     (ACCEPTANCE, COMPLETED), frozenset({LEAD, QA})),
    "reject": Action("Không đạt", frozenset({"in_acceptance"}), "returned",
                     (ANNOTATION, REJECTED), frozenset({LEAD, QA}), note_required=True),
    "sign_off": Action("Chốt job", frozenset({"accepted"}), "signed_off",
                       (ACCEPTANCE, COMPLETED), frozenset({LEAD})),
    "reopen": Action("Mở lại", frozenset({"accepted", "signed_off"}), "in_progress",
                     (ANNOTATION, IN_PROGRESS), frozenset({LEAD}), note_required=True),
}


def job_status(stage: str, state: str, signed_off: bool = False) -> str:
    """Workflow status from the CVAT stage / state (signed_off: the last accept was signed off)."""
    stage, state = str(stage), str(state)
    if state == REJECTED:
        return "returned"
    if stage == ANNOTATION:
        return "waiting_review" if state == COMPLETED else "in_progress"
    if stage == VALIDATION:
        return "waiting_acceptance" if state == COMPLETED else "in_review"
    if state == NEW:
        return "waiting_acceptance"
    if state == IN_PROGRESS:
        return "in_acceptance"
    return "signed_off" if signed_off else "accepted"


@dataclass
class Context:
    """What the conditions of an action need to know about the job (filled by the Django service)."""

    status: str
    roles: set[str]  # QC roles of the user (admin: all)
    is_annotator: bool = False  # the user labels this job
    qc_whole_job: bool = False  # the latest QC run checked every frame
    qc_stale_reason: str | None = "chưa chạy QC"  # None: the latest run describes the current labels
    open_blockers: int = 0
    acceptance: dict | None = None  # evaluate_acceptance() of the open sample
    criteria: dict = field(default_factory=dict)


FORBIDDEN, BAD_REQUEST, CONFLICT = 403, 400, 409


def check(action: str, ctx: Context, *, note: str = "", override: bool = False) -> tuple[int, str]:
    """(0, "") when the action can be done now, else (HTTP status, reason in Vietnamese)."""
    spec = ACTIONS.get(action)
    if spec is None:
        return BAD_REQUEST, f"Hành động không hợp lệ: {action}"
    if ctx.status not in spec.sources:
        return CONFLICT, f"Job đang ở trạng thái '{STATUS_TEXT[ctx.status]}', không thể '{spec.label}'"
    if not may(action, ctx):
        return FORBIDDEN, "Bạn không có quyền: cần vai trò " + ", ".join(sorted(spec.roles)) + (
            " hoặc là người gán nhãn của job" if action == "submit" else "")
    if spec.note_required and not note.strip():
        return BAD_REQUEST, "Cần ghi chú lý do"
    if action == "review_pass":
        if not ctx.qc_whole_job:
            return CONFLICT, "Cần chạy QC cho toàn bộ job (không phải 1 frame) trước khi cho đạt"
        if ctx.qc_stale_reason:
            return CONFLICT, f"Kết quả QC đã cũ ({ctx.qc_stale_reason}): chạy QC lại"
        if ctx.open_blockers:
            return CONFLICT, f"Còn {ctx.open_blockers} lỗi nghiêm trọng chưa xử lý"
    if action == "accept":
        result = ctx.acceptance
        if not result:
            return CONFLICT, "Chưa có mẫu nghiệm thu"
        missing = len(result["frames"]) - result["checked_frames"]
        if missing:
            return CONFLICT, f"Còn {missing} frame mẫu chưa chấm"
        if not result["passes"]:
            if LEAD not in ctx.roles:
                return FORBIDDEN, "Chưa đạt tiêu chí nghiệm thu: chỉ lead được duyệt vượt, kèm lý do"
            if not override or not note.strip():
                return BAD_REQUEST, "Chưa đạt tiêu chí: lead duyệt vượt cần chọn override và ghi lý do"
    return 0, ""


def may(action: str, ctx: Context) -> bool:
    """The user's role (or being the job's annotator, for submit) allows the action at all."""
    spec = ACTIONS[action]
    return bool(spec.roles & ctx.roles) or (action == "submit" and ctx.is_annotator)


def available(ctx: Context) -> list[dict]:
    """Actions from the current status the user may do, with why they are disabled (for the UI)."""
    items = []
    for name, spec in ACTIONS.items():
        if ctx.status not in spec.sources or not may(name, ctx):
            continue
        code, reason = check(name, ctx, note="-", override=True)
        needs_override = False
        if name == "accept" and not code and ctx.acceptance and ctx.acceptance["passes"] is False:
            needs_override = True  # lead only: must tick override and give a reason
        items.append({"action": name, "label": spec.label, "enabled": not code, "reason": reason,
                      "note_required": spec.note_required or needs_override, "needs_override": needs_override})
    return items


def sample_frames(frames: list[int], criteria: dict, seed: int) -> list[int]:
    """max(min_frames, ceil(ratio x frames)) frames, drawn with `seed` (stored, so the sample can be redrawn)."""
    count = max(int(criteria["min_frames"]), math.ceil(float(criteria["sample_ratio"]) * len(frames)))
    count = min(count, len(frames))
    return sorted(random.Random(seed).sample(sorted(frames), count))


def evaluate_acceptance(frames: list[int], results: dict, criteria: dict, open_blockers: int) -> dict:
    """Numbers of an acceptance sample; passes is None until every sample frame is checked."""
    checked = [results[str(f)] for f in frames if str(f) in results]
    boxes = sum(int(r.get("boxes_checked", 0)) for r in checked)
    errors = sum(int(r.get("errors", 0)) for r in checked)
    rate = errors / boxes if boxes else (0.0 if checked else None)
    passes = None
    if len(checked) == len(frames):
        passes = (rate or 0.0) <= float(criteria["max_error_rate"]) and open_blockers <= int(
            criteria["max_open_blockers"])
    return {"frames": list(frames), "checked_frames": len(checked), "boxes_checked": boxes, "errors": errors,
            "error_rate": None if rate is None else round(rate, 4), "passes": passes,
            "open_blockers": open_blockers}


def frame_result(value: dict) -> dict:
    """Validated result of one sample frame (raises ValueError with a Vietnamese message)."""
    try:
        boxes = int(value.get("boxes_checked"))
        errors = int(value.get("errors"))
    except (TypeError, ValueError):
        raise ValueError("boxes_checked và errors phải là số nguyên") from None
    if boxes < 0 or errors < 0 or errors > boxes:
        raise ValueError("Cần 0 ≤ errors ≤ boxes_checked")
    note = str(value.get("note") or "")[:1000]
    return {"boxes_checked": boxes, "errors": errors, "note": note}
