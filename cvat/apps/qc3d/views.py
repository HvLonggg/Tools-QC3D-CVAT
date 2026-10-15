# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import base64
import binascii
import re

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from cvat.apps.engine.models import AnnotationGuide, DimensionType, Job, Project, Task
from cvat.apps.engine.permissions import JobPermission, ProjectPermission, TaskPermission
from cvat.apps.engine.types import ExtendedRequest

from .models import (
    IssueStatus,
    QcGuideline,
    QcGuidelineBinding,
    QcIssue,
    QcJobSettings,
    QcMembership,
    QcRole,
    QcRun,
)
from .permissions import Qc3dPermission
from .quality import annotator_of, annotators, job_feedback, quality_overview, team_analytics
from .qc.guideline import (
    AUTOFIX_KINDS,
    MAX_NOTES_CHARS,
    RULE_TITLES,
    THRESHOLD_LIMITS,
    GuidelineError,
    builtin_content,
    compile_into,
    normalize,
    parse_file,
)
from .qc.rules import THRESHOLDS
from .qc.taxonomy import as_dict as taxonomy_as_dict
from .services import (
    FixError,
    apply_fix,
    autofix_job,
    decide_issue,
    resolve_guideline,
    revert_fix,
    run_job_qc,
)

RECENT_JOBS_LIMIT = 50

# who may run QC / decide on issues (CVAT admins always may)
RUN_ROLES = {QcRole.LEAD, QcRole.REVIEWER, QcRole.QA}
DECIDE_ROLES = {QcRole.LEAD, QcRole.REVIEWER, QcRole.QA}
FIX_ROLES = {QcRole.LEAD, QcRole.REVIEWER}
GUIDELINE_ROLES = {QcRole.LEAD, QcRole.QA}
# who may see the work quality of other people (everyone sees their own)
TEAM_ROLES = {QcRole.LEAD, QcRole.QA}


def _roles(request: ExtendedRequest) -> tuple[bool, list[str]]:
    if request.iam_context["privilege"] == "admin":
        return True, list(QcRole.values)
    return False, list(QcMembership.objects.filter(user=request.user).values_list("role", flat=True))


def _require_role(request: ExtendedRequest, allowed: set[str]) -> None:
    _, roles = _roles(request)
    if not allowed.intersection(roles):
        raise PermissionDenied(f"Cần một trong các vai trò QC: {', '.join(sorted(allowed))}")


def _visible_3d_jobs(request: ExtendedRequest):
    return JobPermission.create_scope_list(request).filter(
        Job.objects.filter(segment__task__dimension=DimensionType.DIM_3D)
    )


def _get_job(request: ExtendedRequest, job_id: int) -> Job:
    return get_object_or_404(_visible_3d_jobs(request).select_related("segment__task"), id=job_id)


def _visible_3d_tasks(request: ExtendedRequest):
    return TaskPermission.create_scope_list(request).filter(Task.objects.filter(dimension=DimensionType.DIM_3D))


def _visible_3d_projects(request: ExtendedRequest):
    return ProjectPermission.create_scope_list(request).filter(
        Project.objects.filter(task__dimension=DimensionType.DIM_3D).distinct()
    )


def _serialize_guideline(guideline: QcGuideline, *, projects: dict, tasks: dict, full: bool) -> dict:
    """projects / tasks: the targets visible to the user (id -> name); other bindings are only counted."""
    bindings = list(guideline.bindings.all())
    data = {
        "id": guideline.id,
        "name": guideline.name,
        "description": guideline.description,
        "revision": guideline.revision,
        "source_name": guideline.source_name,
        "is_default": guideline.is_default,
        "classes": [c["name"] for c in guideline.content.get("classes", [])],
        "projects": [{"id": b.project_id, "name": projects[b.project_id]} for b in bindings
                     if b.project_id in projects],
        "tasks": [{"id": b.task_id, "name": tasks[b.task_id]} for b in bindings if b.task_id in tasks],
        "hidden_bindings": sum(1 for b in bindings if b.project_id not in projects and b.task_id not in tasks),
        "created_by": guideline.created_by.username if guideline.created_by else None,
        "updated_by": guideline.updated_by.username if guideline.updated_by else None,
        "updated_date": guideline.updated_date,
    }
    if full:
        data["content"] = guideline.content
    return data


def _serialize_issue(issue: QcIssue) -> dict:
    return {
        "id": issue.id,
        "rule": issue.rule,
        "severity": issue.severity,
        "frame": issue.frame,
        "uid": issue.uid,
        "label": issue.label,
        "message": issue.message,
        "evidence": issue.evidence,
        "status": issue.status,
        "decided_by": issue.decided_by.username if issue.decided_by else None,
        "decided_date": issue.decided_date,
        "decision_reason": issue.decision_reason,
        "proposal": issue.proposal,
        "fix_status": issue.fix_status,
        "fixed_by": issue.fixed_by.username if issue.fixed_by else None,
        "fixed_date": issue.fixed_date,
    }


def _serialize_run(run: QcRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id,
        "created_date": run.created_date,
        "created_by": run.created_by.username if run.created_by else None,
        "rules_version": run.rules_version,
        "annotations_version": run.annotations_version,
        "objects_count": run.objects_count,
        "duration_ms": run.duration_ms,
        "coverage": run.coverage,
        "guideline": run.guideline,
        "frames": run.frames,
    }


@extend_schema(tags=["qc3d"])
class Qc3dViewSet(viewsets.ViewSet):
    iam_permission_class = Qc3dPermission

    @extend_schema(
        summary="Get QC 3D overview for the current user",
        responses={"200": OpenApiResponse(description="Roles, 3D task/job counters, recent 3D jobs")},
    )
    @action(detail=False, methods=["GET"])
    def summary(self, request: ExtendedRequest):
        is_admin, roles = _roles(request)

        tasks = TaskPermission.create_scope_list(request).filter(
            Task.objects.filter(dimension=DimensionType.DIM_3D)
        )
        jobs = _visible_3d_jobs(request)

        jobs_by_stage = {
            row["stage"]: row["count"]
            for row in jobs.values("stage").annotate(count=Count("id")).order_by()
        }

        recent = list(
            jobs.select_related("segment__task", "assignee").order_by("-updated_date")[:RECENT_JOBS_LIMIT]
        )
        last_runs = {}
        for run in (
            QcRun.objects.filter(job__in=recent)
            .annotate(open_issues=Count("issues", filter=Q(issues__status=IssueStatus.OPEN)))
            .order_by("job_id", "-id")
        ):
            last_runs.setdefault(run.job_id, run)

        recent_jobs = [
            {
                "id": job.id,
                "task_id": job.segment.task_id,
                "task_name": job.segment.task.name,
                "stage": job.stage,
                "state": job.state,
                "assignee": job.assignee.username if job.assignee else None,
                "updated_date": job.updated_date,
                "last_qc_date": last_runs[job.id].created_date if job.id in last_runs else None,
                "open_issues": last_runs[job.id].open_issues if job.id in last_runs else None,
            }
            for job in recent
        ]

        return Response(
            {
                "is_admin": is_admin,
                "roles": roles,
                "tasks_3d": tasks.count(),
                "jobs_3d": jobs.count(),
                "jobs_3d_by_stage": jobs_by_stage,
                "recent_jobs": recent_jobs,
            }
        )

    @extend_schema(summary="Run QC rules on a 3D job", responses={"201": OpenApiResponse()})
    @action(detail=False, methods=["POST"], url_path=r"jobs/(?P<job_id>\d+)/run")
    def run(self, request: ExtendedRequest, job_id: str):
        _require_role(request, RUN_ROLES)
        job = _get_job(request, int(job_id))
        qc_run = run_job_qc(job, request.user)
        return Response(self._report(job, qc_run), status=status.HTTP_201_CREATED)

    @extend_schema(summary="Get the latest QC report of a 3D job", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["GET"], url_path=r"jobs/(?P<job_id>\d+)/report")
    def report(self, request: ExtendedRequest, job_id: str):
        job = _get_job(request, int(job_id))
        return Response(self._report(job, job.qc3d_runs.first()))

    @extend_schema(summary="Confirm, waive or reopen a QC issue", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["POST"], url_path=r"issues/(?P<issue_id>\d+)/decision")
    def decision(self, request: ExtendedRequest, issue_id: str):
        _require_role(request, DECIDE_ROLES)
        issue = get_object_or_404(QcIssue.objects.select_related("run__job"), id=int(issue_id))
        _get_job(request, issue.run.job_id)  # the user must see the job

        new_status = request.data.get("status")
        reason = (request.data.get("reason") or "").strip()
        if new_status not in IssueStatus.values:
            raise ValidationError({"status": f"Một trong: {', '.join(IssueStatus.values)}"})
        if new_status == IssueStatus.WAIVED and not reason:
            raise ValidationError({"reason": "Giữ nguyên nhãn bắt buộc ghi lý do"})
        return Response(_serialize_issue(decide_issue(issue, request.user, new_status, reason)))

    @extend_schema(summary="Write the proposed correction of an issue to CVAT", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["POST"], url_path=r"issues/(?P<issue_id>\d+)/fix")
    def fix(self, request: ExtendedRequest, issue_id: str):
        return self._fix_action(request, issue_id, apply_fix)

    @extend_schema(summary="Restore the CVAT object as it was before the fix", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["POST"], url_path=r"issues/(?P<issue_id>\d+)/revert")
    def revert(self, request: ExtendedRequest, issue_id: str):
        return self._fix_action(request, issue_id, revert_fix)

    @extend_schema(summary="Vietnamese traffic class taxonomy and size guideline", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["GET"])
    def taxonomy(self, request: ExtendedRequest):
        return Response(taxonomy_as_dict())

    def _quality_subject(self, request: ExtendedRequest):
        """The person whose work is shown: the caller, or (lead / QA / admin) the `user` asked for."""
        _, roles = _roles(request)
        can_view_team = bool(TEAM_ROLES.intersection(roles))
        requested = request.query_params.get("user")
        if requested in (None, "", str(request.user.id)):
            return request.user, can_view_team
        if not can_view_team:
            raise PermissionDenied("Chỉ lead / QA xem được chất lượng làm việc của người khác")
        try:
            return get_object_or_404(get_user_model(), id=int(requested)), can_view_team
        except ValueError as e:
            raise ValidationError({"user": "Cần id người dùng"}) from e

    @extend_schema(
        summary="Work quality of an annotator: their 3D jobs, review status, QC errors, reviewers, score",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["GET"])
    def quality(self, request: ExtendedRequest):
        subject, can_view_team = self._quality_subject(request)
        result = quality_overview(subject)
        result["viewer"] = {"id": request.user.id, "username": request.user.username, "can_view_team": can_view_team}
        result["people"] = annotators() if can_view_team else []
        return Response(result)

    @extend_schema(
        summary="QC findings on one job the annotator labeled, with reviewers and decisions",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["GET"], url_path=r"quality/jobs/(?P<job_id>\d+)")
    def quality_job(self, request: ExtendedRequest, job_id: str):
        subject, _ = self._quality_subject(request)
        job = get_object_or_404(Job.objects.select_related("segment__task"), id=int(job_id))
        if annotator_of(job.id) != subject.id:
            # the same answer whether the job exists or not: no hint about other people's jobs
            raise NotFound("Không có job này trong danh sách việc đã làm")
        return Response(job_feedback(job))

    @extend_schema(
        summary="QC analytics of the 3D jobs the caller can see (people figures for QC roles only)",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["GET"])
    def analytics(self, request: ExtendedRequest):
        try:
            days = int(request.query_params.get("days", 30))
        except ValueError as e:
            raise ValidationError({"days": "Cần số ngày"}) from e
        if days not in (7, 30, 90, 365):
            raise ValidationError({"days": "Chọn 7, 30, 90 hoặc 365 ngày"})
        jobs = _visible_3d_jobs(request)
        for key, field in (("project_id", "segment__task__project_id"), ("task_id", "segment__task_id")):
            value = request.query_params.get(key)
            if value:
                jobs = jobs.filter(**{field: int(value)}) if value.isdigit() else jobs.none()
        _, roles = _roles(request)
        result = team_analytics(
            jobs, days=days,
            # per-annotator quality: lead / QA only (as on the quality page); reviewer activity: any QC role
            include_people=bool(TEAM_ROLES.intersection(roles)),
            include_reviewers=bool(roles),
        )
        result["filters"] = {
            "projects": [
                {"id": k, "name": v} for k, v in sorted(_visible_3d_projects(request).values_list("id", "name"))
            ],
            "tasks": [
                {"id": t.id, "name": t.name, "project_id": t.project_id}
                for t in _visible_3d_tasks(request).only("id", "name", "project_id").order_by("-id")[:500]
            ],
        }
        return Response(result)

    def _workflow_roles(self, request: ExtendedRequest) -> set[str]:
        is_admin, roles = _roles(request)
        return set(QcRole.values) if is_admin else set(roles)

    @extend_schema(
        summary="Review / acceptance workflow of a 3D job: state (GET) or one step (POST)",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["GET", "POST"], url_path=r"jobs/(?P<job_id>\d+)/workflow")
    def workflow(self, request: ExtendedRequest, job_id: str):
        from . import workflow

        job = _get_job(request, int(job_id))
        roles = self._workflow_roles(request)
        if request.method == "GET":
            return Response(workflow.state(job, request.user, roles))
        action_name = request.data.get("action")
        note = request.data.get("note") or ""
        if not isinstance(action_name, str) or not isinstance(note, str):
            raise ValidationError({"action": "Cần action (chuỗi) và note (chuỗi)"})
        try:
            return Response(workflow.act(job, request.user, roles, action_name, note,
                                         override=request.data.get("override") is True))
        except workflow.WorkflowError as e:
            return Response({"detail": str(e)}, status=e.status_code)

    @extend_schema(summary="Result of the QA on one acceptance sample frame", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["PUT"], url_path=r"jobs/(?P<job_id>\d+)/acceptance/frames/(?P<frame>\d+)")
    def acceptance_frame(self, request: ExtendedRequest, job_id: str, frame: str):
        from . import workflow

        job = _get_job(request, int(job_id))
        try:
            return Response(workflow.record_frame(job, request.user, self._workflow_roles(request), int(frame),
                                                  request.data if isinstance(request.data, dict) else {}))
        except workflow.WorkflowError as e:
            return Response({"detail": str(e)}, status=e.status_code)

    @extend_schema(summary="Workflow status of every visible 3D job", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["GET"], url_path="workflow/board")
    def workflow_board(self, request: ExtendedRequest):
        from . import workflow

        def number(name: str) -> int | None:
            value = request.query_params.get(name)
            return int(value) if value and value.isdigit() else None

        status_filter = request.query_params.get("status") or None
        if status_filter and status_filter not in dict(workflow.W.STATUSES):
            raise ValidationError({"status": "Trạng thái không hợp lệ"})
        return Response(workflow.board(
            _visible_3d_jobs(request), status=status_filter, task=number("task"), assignee=number("assignee"),
            search=(request.query_params.get("search") or "").strip()[:100],
        ))

    @extend_schema(summary="Bird's-eye overview map of a 3D job with its QC issues", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["GET"], url_path=r"jobs/(?P<job_id>\d+)/overview")
    def overview(self, request: ExtendedRequest, job_id: str):
        from . import workflow

        return Response(workflow.overview(_get_job(request, int(job_id))))

    @extend_schema(
        summary="QC check of a job in the background (model on missing frames, then QC): status or start",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["GET", "POST"], url_path=r"jobs/(?P<job_id>\d+)/check")
    def check(self, request: ExtendedRequest, job_id: str):
        from . import detection

        job = _get_job(request, int(job_id))
        if request.method == "POST":
            _require_role(request, RUN_ROLES)
            frame = request.data.get("frame")
            if frame is not None and (not isinstance(frame, int) or not (
                job.segment.start_frame <= frame <= job.segment.stop_frame
            )):
                raise ValidationError({"frame": "Frame không thuộc job"})
            # boxes a reviewer marked as wrong: QC on their frames, then auto-fix of those boxes only
            marked = request.data.get("marked")
            if marked is not None:
                _require_role(request, FIX_ROLES)
                if not isinstance(marked, list) or not marked or len(marked) > 500 or not all(
                    isinstance(uid, str) and re.fullmatch(r"s:\d+|t:\d+:\d+", uid) for uid in marked
                ):
                    raise ValidationError({"marked": "Danh sách nhãn đã chọn không hợp lệ"})
            return Response(detection.start_check(job, request.user, frame, marked=marked))
        return Response(detection.check_status(job))

    @extend_schema(
        summary="Detection model on a job (R12): status, or start it in the background",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["GET", "POST"], url_path=r"jobs/(?P<job_id>\d+)/detect")
    def detect(self, request: ExtendedRequest, job_id: str):
        from . import detection

        job = _get_job(request, int(job_id))
        if request.method == "POST":
            _require_role(request, RUN_ROLES)
            try:
                return Response(detection.start(job))
            except FileNotFoundError as e:
                return Response({"detail": str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(detection.status(job))

    @extend_schema(summary="Get or change the per-job QC settings", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["GET", "PUT"], url_path=r"jobs/(?P<job_id>\d+)/settings")
    def job_settings(self, request: ExtendedRequest, job_id: str):
        job = _get_job(request, int(job_id))
        settings_obj = QcJobSettings.objects.filter(job=job).first()
        if request.method == "PUT":
            _require_role(request, DECIDE_ROLES)
            excluded = request.data.get(
                "autofix_excluded_labels", settings_obj.autofix_excluded_labels if settings_obj else []
            )
            if not isinstance(excluded, list) or not all(isinstance(x, str) for x in excluded):
                raise ValidationError({"autofix_excluded_labels": "Cần danh sách tên class"})
            known = set(job.get_labels().values_list("name", flat=True))
            unknown = sorted(set(excluded) - known)
            if unknown:
                raise ValidationError({"autofix_excluded_labels": f"Class không có trong job: {', '.join(unknown)}"})
            create = request.data.get("autofix_create", settings_obj.autofix_create if settings_obj else True)
            if not isinstance(create, bool):
                raise ValidationError({"autofix_create": "Cần true hoặc false"})
            settings_obj, _ = QcJobSettings.objects.update_or_create(
                job=job, defaults={
                    "autofix_excluded_labels": sorted(set(excluded)), "autofix_create": create,
                    "updated_by": request.user,
                },
            )
        _, roles = _roles(request)
        return Response({
            "autofix_excluded_labels": settings_obj.autofix_excluded_labels if settings_obj else [],
            "autofix_create": settings_obj.autofix_create if settings_obj else True,
            "labels": sorted(job.get_labels().values_list("name", flat=True)),
            "can_decide": bool(DECIDE_ROLES.intersection(roles)),
            "can_fix": bool(FIX_ROLES.intersection(roles)),
            "can_run": bool(RUN_ROLES.intersection(roles)),
        })

    @extend_schema(
        summary="Write every eligible proposal of the latest QC run (classes excluded by the reviewer are kept)",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["POST"], url_path=r"jobs/(?P<job_id>\d+)/autofix")
    def autofix(self, request: ExtendedRequest, job_id: str):
        _require_role(request, FIX_ROLES)
        job = _get_job(request, int(job_id))
        settings_obj = QcJobSettings.objects.filter(job=job).first()
        excluded = set(settings_obj.autofix_excluded_labels if settings_obj else [])
        try:
            result = autofix_job(
                job, request.user, excluded, dry_run=bool(request.data.get("dry_run")),
                create=settings_obj.autofix_create if settings_obj else True,
            )
        except FixError as e:
            return Response({"detail": str(e)}, status=e.status_code)
        result["excluded_labels"] = sorted(excluded)
        return Response(result)

    @extend_schema(summary="Guideline applied to the QC of a 3D job", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["GET"], url_path=r"jobs/(?P<job_id>\d+)/guideline")
    def job_guideline(self, request: ExtendedRequest, job_id: str):
        job = _get_job(request, int(job_id))
        resolved = resolve_guideline(job)
        content = resolved["content"] or builtin_content()
        classes = {c["name"] for c in content["classes"]}
        task_labels = set(job.get_labels().values_list("name", flat=True))
        last_run = job.qc3d_runs.first()
        return Response({
            **resolved,
            "content": content,
            "labels_without_class": sorted(task_labels - classes),
            "classes_without_label": sorted(classes - task_labels),
            "last_run_key": last_run.guideline.get("key") if last_run else None,
        })

    @extend_schema(summary="List or create QC guidelines", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["GET", "POST"])
    def guidelines(self, request: ExtendedRequest):
        projects = dict(_visible_3d_projects(request).values_list("id", "name"))
        tasks = dict(_visible_3d_tasks(request).values_list("id", "name"))
        if request.method == "POST":
            _require_role(request, GUIDELINE_ROLES)
            guideline = self._save_guideline(request, QcGuideline(created_by=request.user), projects, tasks)
            return Response(
                _serialize_guideline(guideline, projects=projects, tasks=tasks, full=True),
                status=status.HTTP_201_CREATED,
            )

        _, roles = _roles(request)
        guidelines = QcGuideline.objects.select_related("created_by", "updated_by").prefetch_related("bindings")
        return Response({
            "can_manage": bool(GUIDELINE_ROLES.intersection(roles)),
            "results": [_serialize_guideline(g, projects=projects, tasks=tasks, full=False) for g in guidelines],
            "targets": {
                "projects": [{"id": k, "name": v} for k, v in sorted(projects.items())],
                "tasks": [{"id": k, "name": v} for k, v in sorted(tasks.items())],
            },
            "template": builtin_content(),
            "thresholds": [
                {"key": key, "label": label, "min": lo, "max": hi, "default": THRESHOLDS[key]}
                for key, (label, lo, hi) in THRESHOLD_LIMITS.items()
            ],
            "rules": [{"code": code, "title": title} for code, title in RULE_TITLES.items()],
            "autofix_kinds": [{"key": key, "title": title} for key, title in AUTOFIX_KINDS.items()],
        })

    @extend_schema(summary="Get, update or delete a QC guideline", responses={"200": OpenApiResponse()})
    @action(detail=False, methods=["GET", "PUT", "DELETE"], url_path=r"guidelines/(?P<guideline_id>\d+)")
    def guideline_detail(self, request: ExtendedRequest, guideline_id: str):
        guideline = get_object_or_404(QcGuideline, id=int(guideline_id))
        projects = dict(_visible_3d_projects(request).values_list("id", "name"))
        tasks = dict(_visible_3d_tasks(request).values_list("id", "name"))
        if request.method == "DELETE":
            _require_role(request, GUIDELINE_ROLES)
            guideline.delete()  # QC runs keep their own snapshot of the guideline
            return Response(status=status.HTTP_204_NO_CONTENT)
        if request.method == "PUT":
            _require_role(request, GUIDELINE_ROLES)
            guideline = self._save_guideline(request, guideline, projects, tasks)
        return Response(_serialize_guideline(guideline, projects=projects, tasks=tasks, full=True))

    @extend_schema(
        summary="Read a guideline from a file or from the CVAT guide of a task / project (not saved)",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["POST"], url_path="guidelines/parse")
    def guideline_parse(self, request: ExtendedRequest):
        _require_role(request, GUIDELINE_ROLES)
        if request.data.get("task_id") or request.data.get("project_id"):
            kind = "task" if request.data.get("task_id") else "project"
            visible = _visible_3d_tasks(request) if kind == "task" else _visible_3d_projects(request)
            target = get_object_or_404(visible, id=int(request.data.get(f"{kind}_id")))
            guide = AnnotationGuide.objects.filter(**{kind: target}).first()
            if guide is None or not guide.markdown.strip():
                raise ValidationError({"detail": f"{kind} #{target.id} chưa có guide trên CVAT"})
            filename, data = f"guide-{kind}-{target.id}.md", guide.markdown.encode("utf-8")
        else:
            filename = str(request.data.get("filename") or "")
            try:
                data = base64.b64decode(str(request.data.get("content_base64") or ""), validate=True)
            except (binascii.Error, ValueError) as e:
                raise ValidationError({"detail": "Nội dung file không hợp lệ (base64)"}) from e
        compiled: list[str] = []
        try:
            content, warnings = parse_file(filename, data, compiled)
        except GuidelineError as e:
            raise ValidationError({"detail": e.errors})
        return Response({"source_name": filename, "content": content, "warnings": warnings, "compiled": compiled})

    @extend_schema(
        summary="Apply the plain-language sentences of a guide to a guideline (not saved)",
        responses={"200": OpenApiResponse()},
    )
    @action(detail=False, methods=["POST"], url_path="guidelines/compile")
    def guideline_compile(self, request: ExtendedRequest):
        _require_role(request, GUIDELINE_ROLES)
        text = request.data.get("text")
        content = request.data.get("content")
        if not isinstance(text, str) or len(text) > MAX_NOTES_CHARS:
            raise ValidationError({"detail": f"text cần là chuỗi tối đa {MAX_NOTES_CHARS} ký tự"})
        try:
            content, warnings = normalize(content)
            compiled: list[str] = []
            compile_into(content, text, warnings, compiled)
            content, more = normalize(content)
        except GuidelineError as e:
            raise ValidationError({"detail": e.errors})
        return Response({"content": content, "warnings": warnings + more, "compiled": compiled})

    @staticmethod
    def _save_guideline(request: ExtendedRequest, guideline: QcGuideline, projects: dict, tasks: dict):
        name = str(request.data.get("name") or "").strip()
        if not name:
            raise ValidationError({"name": "Cần tên guideline"})
        try:
            content, _ = normalize(request.data.get("content"))
        except GuidelineError as e:
            raise ValidationError({"content": e.errors})
        try:
            project_ids = {int(i) for i in request.data.get("projects", [])}
            task_ids = {int(i) for i in request.data.get("tasks", [])}
        except (TypeError, ValueError) as e:
            raise ValidationError({"detail": "projects / tasks phải là danh sách id"}) from e
        if not project_ids <= set(projects) or not task_ids <= set(tasks):
            raise ValidationError({"detail": "Chỉ gán được cho project / task 3D mà bạn xem được"})

        with transaction.atomic():
            if guideline.pk and content != guideline.content:
                guideline.revision += 1
            guideline.name = name[:256]
            guideline.description = str(request.data.get("description") or "")
            guideline.content = content
            guideline.source_name = str(request.data.get("source_name") or guideline.source_name)[:256]
            guideline.is_default = bool(request.data.get("is_default"))
            guideline.updated_by = request.user
            guideline.save()
            if guideline.is_default:
                QcGuideline.objects.exclude(id=guideline.id).filter(is_default=True).update(is_default=False)

            # bindings of visible targets are replaced; a target bound to another guideline moves here
            guideline.bindings.filter(Q(project_id__in=projects) | Q(task_id__in=tasks)).exclude(
                Q(project_id__in=project_ids) | Q(task_id__in=task_ids)
            ).delete()
            for project_id in project_ids:
                QcGuidelineBinding.objects.update_or_create(project_id=project_id, defaults={"guideline": guideline})
            for task_id in task_ids:
                QcGuidelineBinding.objects.update_or_create(task_id=task_id, defaults={"guideline": guideline})
        return guideline

    def _fix_action(self, request: ExtendedRequest, issue_id: str, operation):
        _require_role(request, FIX_ROLES)
        issue = get_object_or_404(QcIssue.objects.select_related("run__job"), id=int(issue_id))
        _get_job(request, issue.run.job_id)
        try:
            issue = operation(issue, request.user)
        except FixError as e:
            return Response({"detail": str(e)}, status=e.status_code)
        return Response(_serialize_issue(issue))

    @staticmethod
    def _report(job: Job, run: QcRun | None) -> dict:
        task = job.segment.task
        issues = list(run.issues.select_related("decided_by", "fixed_by")) if run else []
        return {
            "job": {
                "id": job.id,
                "task_id": task.id,
                "task_name": task.name,
                "stage": job.stage,
                "state": job.state,
                "start_frame": job.segment.start_frame,
                "stop_frame": job.segment.stop_frame,
            },
            "run": _serialize_run(run),
            "issues": [_serialize_issue(i) for i in issues],
        }
