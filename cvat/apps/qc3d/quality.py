# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Work quality of an annotator: the 3D jobs they labeled, where each job is in the review flow, the QC
errors found in their labels, who reviewed them, and an overall quality score.

Attribution: the annotator of a job is its last assignee at the annotation stage (QcJobAssignment),
so a job stays theirs after it moves to a reviewer.
Counting: only errors a reviewer confirmed (or fixed) count against the annotator; waived issues are
QC false alarms; open issues are not decided yet and make the score provisional.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

from cvat.apps.engine.models import DimensionType, Job

from .models import FixStatus, IssueStatus, QcIssue, QcJobAssignment, QcRun
from .qc.guideline import RULE_TITLES

ANNOTATION = "annotation"

# review status of a job, from the annotator's point of view
REVIEW_STATUS = {
    "in_progress": "Đang làm",
    "returned": "Bị trả lại, cần sửa",
    "waiting_review": "Chờ review",
    "in_review": "Đang review",
    "reviewed": "Đã review",
    "in_acceptance": "Chờ nghiệm thu",
    "accepted": "Đã nghiệm thu",
}

# quality score (share of checked objects without a confirmed error) -> rating
RATINGS = [(0.98, "excellent", "Xuất sắc"), (0.95, "good", "Tốt"), (0.90, "pass", "Đạt"), (0.0, "improve", "Cần cải thiện")]


def review_status(stage: str, state: str, has_qc: bool) -> str:
    stage, state = str(stage), str(state)
    if state == "rejected":
        return "returned"
    if stage == "annotation":
        return "waiting_review" if state == "completed" else "in_progress"
    if stage == "validation":
        if state == "completed":
            return "reviewed"
        return "in_review" if has_qc or state == "in progress" else "waiting_review"
    return "accepted" if state == "completed" else "in_acceptance"


def rating(score: float | None) -> dict | None:
    if score is None:
        return None
    for limit, key, text in RATINGS:
        if score >= limit:
            return {"key": key, "text": text}
    return None


def annotated_jobs(user_id: int) -> list[int]:
    """3D jobs whose last annotation-stage assignee is the user."""
    last = {}
    for job_id, holder in (
        QcJobAssignment.objects.filter(stage=ANNOTATION, job__segment__task__dimension=DimensionType.DIM_3D)
        .order_by("job_id", "started_date", "id")
        .values_list("job_id", "user_id")
    ):
        last[job_id] = holder
    return [job_id for job_id, holder in last.items() if holder == user_id]


def annotation_start(job_ids) -> dict[int, object]:
    """
    When the current annotator of each job took it over (start of their last run of annotation-stage
    assignments): QC findings before that belong to the previous annotator.
    """
    start, holder = {}, {}
    for job_id, user_id, started in (
        QcJobAssignment.objects.filter(job_id__in=job_ids, stage=ANNOTATION)
        .order_by("job_id", "started_date", "id")
        .values_list("job_id", "user_id", "started_date")
    ):
        if holder.get(job_id) != user_id:
            holder[job_id], start[job_id] = user_id, started
    return start


def annotator_of(job_id: int) -> int | None:
    row = (
        QcJobAssignment.objects.filter(job_id=job_id, stage=ANNOTATION)
        .order_by("-started_date", "-id")
        .values_list("user_id", flat=True)
        .first()
    )
    return row


def annotators() -> list[dict]:
    """People who labeled at least one 3D job (for the lead / QA picker)."""
    users = {}
    for row in QcJobAssignment.objects.filter(
        stage=ANNOTATION, job__segment__task__dimension=DimensionType.DIM_3D
    ).values("user_id", "user__username", "user__first_name", "user__last_name"):
        name = f"{row['user__first_name']} {row['user__last_name']}".strip()
        users[row["user_id"]] = {"id": row["user_id"], "username": row["user__username"], "full_name": name}
    return sorted(users.values(), key=lambda u: u["username"].lower())


def _person(user) -> dict | None:
    if user is None:
        return None
    return {"id": user.id, "username": user.username, "full_name": f"{user.first_name} {user.last_name}".strip()}


def _issue_key(issue: QcIssue) -> tuple:
    return issue.rule, issue.uid, issue.frame


def _job_issues(job_ids: list[int]) -> dict[int, dict]:
    """
    Per job: the latest run, and every issue across runs deduplicated by (rule, object, frame) with its
    latest state (decisions carry over between runs; a fixed object no longer shows in later runs).
    Only runs made since the current annotator took the job over count.
    """
    since = annotation_start(job_ids)

    def mine(job_id, created) -> bool:
        return job_id not in since or created >= since[job_id]

    latest_runs = {}
    for run in QcRun.objects.filter(job_id__in=job_ids).select_related("created_by").order_by("job_id", "-id"):
        if mine(run.job_id, run.created_date):
            latest_runs.setdefault(run.job_id, run)
    issues = defaultdict(dict)
    for issue in (
        QcIssue.objects.filter(run__job_id__in=job_ids)
        .select_related("run", "run__created_by", "decided_by", "fixed_by")
        .order_by("run_id", "id")
    ):
        if mine(issue.run.job_id, issue.run.created_date):
            issues[issue.run.job_id][_issue_key(issue)] = issue  # later runs overwrite earlier ones
    return {job_id: {"run": latest_runs.get(job_id), "issues": issues.get(job_id, {})} for job_id in job_ids}


def _counts(job_id: int, run: QcRun | None, issues: dict) -> dict:
    latest_run_id = run.id if run else None
    confirmed = [i for i in issues.values() if i.status == IssueStatus.CONFIRMED or i.fix_status == FixStatus.APPLIED]
    waived = [i for i in issues.values() if i.status == IssueStatus.WAIVED]
    # an open issue counts only while the latest run still reports it
    open_ = [i for i in issues.values() if i.status == IssueStatus.OPEN and i.run_id == latest_run_id]
    error_objects = {(i.uid, i.frame) for i in confirmed}
    objects = run.objects_count if run else 0
    score = (1 - len(error_objects) / objects) if run and objects else None
    return {
        "objects": objects,
        "error_objects": len(error_objects),
        "confirmed": len(confirmed),
        "fixed": sum(i.fix_status == FixStatus.APPLIED for i in issues.values()),
        "waived": len(waived),
        "open": len(open_),
        "score": round(max(score, 0.0), 4) if score is not None else None,
        "confirmed_issues": confirmed,
    }


def _reviewers(job: Job, run: QcRun | None, issues: dict, assignments: list[QcJobAssignment]) -> list[dict]:
    """Who reviewed the job and how: ran QC, decided issues, fixed labels, CVAT review / acceptance."""
    roles = defaultdict(set)
    people = {}

    def add(user, role):
        if user is not None:
            people[user.id] = user
            roles[user.id].add(role)

    for item in QcRun.objects.filter(job=job, id__in={i.run_id for i in issues.values()} | (
        {run.id} if run else set()
    )).select_related("created_by"):
        add(item.created_by, "Chạy QC")
    for issue in issues.values():
        if issue.status != IssueStatus.OPEN:
            add(issue.decided_by, "Duyệt lỗi")
        if issue.fix_status == FixStatus.APPLIED:
            add(issue.fixed_by, "Sửa nhãn")
    for assignment in assignments:
        if assignment.stage == "validation":
            add(assignment.user, "Review trên CVAT")
        elif assignment.stage == "acceptance":
            add(assignment.user, "Nghiệm thu")
    return [{**_person(people[uid]), "roles": sorted(roles[uid])} for uid in people]


def quality_overview(user, *, weeks: int = 12) -> dict:
    job_ids = annotated_jobs(user.id)
    jobs = {
        job.id: job
        for job in Job.objects.filter(id__in=job_ids).select_related("segment__task", "segment__task__project")
    }
    data = _job_issues(list(jobs))
    assignments = defaultdict(list)
    for assignment in QcJobAssignment.objects.filter(job_id__in=jobs).select_related("user"):
        assignments[assignment.job_id].append(assignment)

    rows, status_counts = [], Counter()
    by_rule, by_label, by_severity = Counter(), Counter(), Counter()
    total = Counter()
    trend = defaultdict(Counter)
    for job_id, job in jobs.items():
        run, issues = data[job_id]["run"], data[job_id]["issues"]
        counts = _counts(job_id, run, issues)
        status = review_status(job.stage, job.state, run is not None)
        status_counts[status] += 1
        for issue in counts.pop("confirmed_issues"):
            by_rule[issue.rule] += 1
            by_label[issue.label or "—"] += 1
            by_severity[issue.severity] += 1
        if run:
            total["objects"] += counts["objects"]
            total["error_objects"] += counts["error_objects"]
            week = (run.created_date - timedelta(days=run.created_date.weekday())).date().isoformat()
            trend[week]["objects"] += counts["objects"]
            trend[week]["error_objects"] += counts["error_objects"]
            trend[week]["jobs"] += 1
        for key in ("confirmed", "fixed", "waived", "open"):
            total[key] += counts[key]
        task = job.segment.task
        rows.append({
            "id": job.id,
            "task_id": task.id,
            "task_name": task.name,
            "project_name": task.project.name if task.project_id else None,
            "stage": str(job.stage),
            "state": str(job.state),
            "review_status": status,
            "review_status_text": REVIEW_STATUS[status],
            "updated_date": job.updated_date,
            "last_qc_date": run.created_date if run else None,
            "qc_by": _person(run.created_by) if run else None,
            "current_assignee": _person(job.assignee) if job.assignee_id else None,
            "reviewers": _reviewers(job, run, issues, assignments[job_id]),
            **counts,
            "rating": rating(counts["score"]),
        })
    rows.sort(key=lambda r: r["updated_date"], reverse=True)

    score = 1 - total["error_objects"] / total["objects"] if total["objects"] else None
    total_issues = total["confirmed"] + total["waived"]
    return {
        "subject": _person(user),
        "summary": {
            "jobs": len(rows),
            "checked_jobs": sum(r["last_qc_date"] is not None for r in rows),
            "objects": total["objects"],
            "error_objects": total["error_objects"],
            "confirmed": total["confirmed"],
            "fixed": total["fixed"],
            "waived": total["waived"],
            "open": total["open"],
            "score": round(score, 4) if score is not None else None,
            "rating": rating(score),
            # undecided issues remain: the score can still go down
            "provisional": total["open"] > 0,
            # share of QC findings the reviewers agreed were real errors
            "confirm_rate": round(total["confirmed"] / total_issues, 4) if total_issues else None,
        },
        "status_counts": [
            {"key": key, "text": text, "count": status_counts.get(key, 0)} for key, text in REVIEW_STATUS.items()
        ],
        "by_rule": [
            {"rule": rule, "title": RULE_TITLES.get(rule, rule), "count": count} for rule, count in by_rule.most_common()
        ],
        "by_label": [{"label": label, "count": count} for label, count in by_label.most_common()],
        "by_severity": dict(by_severity),
        "trend": [
            {
                "week": week, "jobs": values["jobs"], "objects": values["objects"],
                "error_objects": values["error_objects"],
                "score": round(1 - values["error_objects"] / values["objects"], 4) if values["objects"] else None,
            }
            for week, values in sorted(trend.items())[-weeks:]
        ],
        "jobs": rows,
    }


def job_feedback(job: Job) -> dict:
    """The QC findings on one job as the annotator sees them: what, where, decided by whom and why."""
    data = _job_issues([job.id])[job.id]
    run, issues = data["run"], data["issues"]
    counts = _counts(job.id, run, issues)
    counts.pop("confirmed_issues")
    latest_run_id = run.id if run else None
    items = []
    for issue in sorted(issues.values(), key=lambda i: (i.frame, i.rule, i.id)):
        if issue.status == IssueStatus.OPEN and issue.run_id != latest_run_id:
            continue  # no longer reported
        if issue.fix_status == FixStatus.APPLIED:
            outcome = "fixed"
        elif issue.status == IssueStatus.CONFIRMED:
            outcome = "confirmed"
        elif issue.status == IssueStatus.WAIVED:
            outcome = "waived"
        else:
            outcome = "open"
        items.append({
            "id": issue.id,
            "rule": issue.rule,
            "rule_title": RULE_TITLES.get(issue.rule, issue.rule),
            "severity": issue.severity,
            "frame": issue.frame,
            "label": issue.label,
            "message": issue.message,
            "outcome": outcome,
            "fix_summary": (issue.proposal or {}).get("summary") if outcome == "fixed" else None,
            "decided_by": _person(issue.decided_by),
            "decided_date": issue.decided_date,
            "decision_reason": issue.decision_reason,
            "fixed_by": _person(issue.fixed_by),
            "fixed_date": issue.fixed_date,
            "found_date": issue.run.created_date,
        })
    assignments = list(QcJobAssignment.objects.filter(job=job).select_related("user"))
    return {
        "job": {
            "id": job.id, "task_id": job.segment.task_id, "task_name": job.segment.task.name,
            "stage": str(job.stage), "state": str(job.state),
            "review_status": review_status(job.stage, job.state, run is not None),
            "review_status_text": REVIEW_STATUS[review_status(job.stage, job.state, run is not None)],
        },
        "last_qc_date": run.created_date if run else None,
        "qc_by": _person(run.created_by) if run else None,
        "guideline": (run.guideline or {}).get("name") if run else None,
        "reviewers": _reviewers(job, run, issues, assignments),
        "history": [
            {"user": _person(a.user), "stage": a.stage, "started_date": a.started_date, "ended_date": a.ended_date}
            for a in assignments
        ],
        **counts,
        "rating": rating(counts["score"]),
        "issues": items,
    }


def _day(value) -> str:
    return value.date().isoformat()


def team_analytics(jobs, *, days: int, include_people: bool, include_reviewers: bool) -> dict:
    """
    QC activity over the last `days` days on the given 3D jobs (already filtered to what the caller may see):
    totals, a daily series of findings and decisions, errors by rule / class / severity, review status of
    the jobs, the jobs that need attention and, for QC roles, per-annotator and per-reviewer figures.
    """
    from django.contrib.auth import get_user_model
    from django.utils import timezone

    now = timezone.now()
    since = now - timedelta(days=days)
    jobs = {job.id: job for job in jobs.select_related("segment__task", "assignee")}
    job_ids = list(jobs)

    latest_runs = {}
    runs_by_day = Counter()
    run_authors = Counter()
    for run in QcRun.objects.filter(job_id__in=job_ids).order_by("job_id", "-id"):
        latest_runs.setdefault(run.job_id, run)
        if run.created_date >= since:
            runs_by_day[_day(run.created_date)] += 1
            if run.created_by_id:
                run_authors[run.created_by_id] += 1

    # issues found in the window, deduplicated per object across runs (latest state wins)
    found = defaultdict(dict)
    first_seen = {}
    for issue in (
        QcIssue.objects.filter(run__job_id__in=job_ids, run__created_date__gte=since)
        .select_related("run")
        .order_by("run_id", "id")
    ):
        key = _issue_key(issue)
        found[issue.run.job_id][key] = issue
        first_seen.setdefault((issue.run.job_id, key), issue.run.created_date)

    daily = defaultdict(Counter)
    by_rule = defaultdict(Counter)
    by_label, open_by_severity = Counter(), Counter()
    decision_hours = []
    reviewers = defaultdict(Counter)
    totals = Counter()
    for job_id, issues in found.items():
        latest_id = latest_runs[job_id].id
        for key, issue in issues.items():
            daily[_day(first_seen[(job_id, key)])]["found"] += 1
            fixed = issue.fix_status == FixStatus.APPLIED
            if issue.status == IssueStatus.CONFIRMED or fixed:
                outcome = "confirmed"
                by_label[issue.label or "—"] += 1
            elif issue.status == IssueStatus.WAIVED:
                outcome = "waived"
            elif issue.run_id == latest_id:
                outcome = "open"
                open_by_severity[issue.severity] += 1
            else:
                continue  # no longer reported by the latest run: resolved by a later edit
            totals[outcome] += 1
            by_rule[issue.rule][outcome] += 1
            if outcome != "open" and issue.decided_date and issue.decided_date >= since:
                daily[_day(issue.decided_date)][outcome] += 1
                hours = (issue.decided_date - first_seen[(job_id, key)]).total_seconds() / 3600
                decision_hours.append(max(hours, 0.0))
                if issue.decided_by_id:
                    reviewers[issue.decided_by_id]["decisions"] += 1
                    reviewers[issue.decided_by_id][outcome] += 1
            if fixed and issue.fixed_date and issue.fixed_date >= since:
                daily[_day(issue.fixed_date)]["fixed"] += 1
                totals["fixed"] += 1
                if issue.fixed_by_id:
                    reviewers[issue.fixed_by_id]["fixes"] += 1
    for uid, count in run_authors.items():
        reviewers[uid]["runs"] += count
    reverted = QcIssue.objects.filter(
        run__job_id__in=job_ids, fix_status=FixStatus.REVERTED, fixed_date__gte=since
    ).count()

    annotator_ids = {}
    if include_people:
        for job_id, holder in (
            QcJobAssignment.objects.filter(job_id__in=job_ids, stage=ANNOTATION)
            .order_by("job_id", "started_date", "id")
            .values_list("job_id", "user_id")
        ):
            annotator_ids[job_id] = holder

    objects = error_objects = 0
    status_counts = Counter()
    attention = []
    people = defaultdict(Counter)
    for job_id, job in jobs.items():
        run = latest_runs.get(job_id)
        status_counts[review_status(job.stage, job.state, run is not None)] += 1
        issues = found.get(job_id, {})
        confirmed = [i for i in issues.values() if i.status == IssueStatus.CONFIRMED or i.fix_status == FixStatus.APPLIED]
        errors = {(i.uid, i.frame) for i in confirmed}
        open_count = sum(1 for i in issues.values() if i.status == IssueStatus.OPEN and run and i.run_id == run.id)
        if run and run.created_date >= since:
            objects += run.objects_count
            error_objects += len(errors)
            holder = annotator_ids.get(job_id)
            if holder:
                people[holder]["jobs"] += 1
                people[holder]["objects"] += run.objects_count
                people[holder]["error_objects"] += len(errors)
                people[holder]["confirmed"] += len(confirmed)
                people[holder]["open"] += open_count
        if open_count or run is None:
            attention.append({
                "id": job_id, "task_id": job.segment.task_id, "task_name": job.segment.task.name,
                "stage": str(job.stage), "open": open_count, "checked": run is not None,
                "assignee": job.assignee.username if job.assignee_id else None,
                "last_qc_date": run.created_date if run else None,
            })
    attention.sort(key=lambda j: (-j["open"], j["checked"], j["id"]))

    wanted = (set(people) if include_people else set()) | (set(reviewers) if include_reviewers else set())
    users = {u.id: u for u in get_user_model().objects.filter(id__in=wanted)} if wanted else {}

    def score_of(counts) -> float | None:
        return 1 - counts["error_objects"] / counts["objects"] if counts["objects"] else None

    score = 1 - error_objects / objects if objects else None
    decided = totals["confirmed"] + totals["waived"]
    days_list = [(since + timedelta(days=i + 1)).date().isoformat() for i in range(days)]
    return {
        "days": days,
        "since": since,
        "totals": {
            "jobs": len(jobs),
            "checked_jobs": sum(1 for r in latest_runs.values() if r.created_date >= since),
            "runs": sum(runs_by_day.values()),
            "objects": objects,
            "error_objects": error_objects,
            "score": round(score, 4) if score is not None else None,
            "rating": rating(score),
            "found": sum(len(i) for i in found.values()),
            "confirmed": totals["confirmed"],
            "waived": totals["waived"],
            "open": totals["open"],
            "fixed": totals["fixed"],
            "reverted": reverted,
            "precision": round(totals["confirmed"] / decided, 4) if decided else None,
            "median_decision_hours": (
                round(sorted(decision_hours)[len(decision_hours) // 2], 1) if decision_hours else None
            ),
        },
        "daily": [
            {"day": day, "runs": runs_by_day.get(day, 0),
             **{k: daily[day].get(k, 0) for k in ("found", "confirmed", "waived", "fixed")}}
            for day in days_list
        ],
        "by_rule": sorted(
            (
                {"rule": rule, "title": RULE_TITLES.get(rule, rule),
                 **{k: counts.get(k, 0) for k in ("confirmed", "waived", "open")}}
                for rule, counts in by_rule.items()
            ),
            key=lambda r: -(r["confirmed"] + r["waived"] + r["open"]),
        ),
        "by_label": [{"label": label, "count": count} for label, count in by_label.most_common(12)],
        "open_by_severity": {k: open_by_severity.get(k, 0) for k in ("blocker", "major", "minor")},
        "status_counts": [
            {"key": key, "text": text, "count": status_counts.get(key, 0)} for key, text in REVIEW_STATUS.items()
        ],
        "attention": attention[:8],
        "annotators": sorted(
            (
                {
                    **_person(users[uid]),
                    **{k: counts.get(k, 0) for k in ("jobs", "objects", "error_objects", "confirmed", "open")},
                    "score": round(score_of(counts), 4) if score_of(counts) is not None else None,
                    "rating": rating(score_of(counts)),
                }
                for uid, counts in people.items() if uid in users
            ),
            key=lambda p: (p["score"] is None, -(p["score"] or 0)),
        ) if include_people else None,
        "reviewers": sorted(
            (
                {**_person(users[uid]), **{k: counts.get(k, 0) for k in ("runs", "decisions", "confirmed", "waived", "fixes")}}
                for uid, counts in reviewers.items() if uid in users
            ),
            key=lambda p: -(p["decisions"] + p["fixes"] + p["runs"]),
        ) if include_reviewers else None,
    }
