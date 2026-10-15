# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""Pure selection of auto-fix proposals, preserving review decisions across matching issues."""

import json
from collections.abc import Iterable

from .guideline import AUTOFIX_KINDS


def plan_autofix(
    issues: Iterable,
    excluded: set[str],
    *,
    applied_status: str,
    waived_status: str,
    allowed_kinds: set[str] | None = None,
) -> tuple[list, list[dict]]:
    """
    Auto-fix writes a proposal only when it was verified (qc/verify.py: the corrected box passes the
    guideline checks and lands on no other object), changes the existing object in place, touches no
    excluded class and makes only kinds of change the guideline allows.
    """
    allowed = set(AUTOFIX_KINDS) if allowed_kinds is None else set(allowed_kinds)
    groups = {}
    for issue in issues:
        if not issue.proposal:
            continue
        key = (issue.uid, json.dumps(issue.proposal, sort_keys=True))
        groups.setdefault(key, []).append(issue)

    targets, skipped = [], []
    for group in groups.values():
        issue = group[0]
        proposal = issue.proposal
        if any(other.fix_status == applied_status for other in group):
            continue
        new_label = (proposal.get("changes") or {}).get("label")
        created = {shape["label"] for shape in proposal.get("shapes", [])}
        kinds = proposal.get("kinds") or []
        reason = None
        if any(other.status == waived_status for other in group):
            reason = "QC đã chọn giữ nguyên"
        elif created and created <= excluded:
            reason = f"class {', '.join(sorted(created))} không nằm trong phạm vi autofix"
        elif not created and (issue.label in excluded or new_label in excluded):
            reason = f"class {new_label if new_label in excluded else issue.label} không nằm trong phạm vi autofix"
        elif proposal["kind"] not in ("update", "create"):
            reason = "xoá box: QC cần xem trước khi xoá"
        elif "verified" not in proposal:
            reason = "đề xuất từ lần QC cũ chưa được kiểm chứng: chạy lại QC"
        elif not proposal["verified"]:
            reason = "bản sửa chưa qua kiểm chứng"
        elif "class" in kinds and issue.uid.startswith("t:"):
            reason = "đổi class của cả track: QC cần xem các frame khác"
        elif set(kinds) - allowed:
            reason = "guideline không cho autofix: " + ", ".join(
                AUTOFIX_KINDS[k].lower() for k in kinds if k not in allowed
            )
        if reason:
            skipped.append({
                "issue": issue.id, "uid": issue.uid, "frame": issue.frame, "reason": reason,
                "summary": proposal.get("summary", ""), "checks": proposal.get("checks") or [],
            })
        else:
            targets.append(issue)
    return targets, skipped
