# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from cvat.apps.engine.models import DimensionType, Job, Task
from cvat.apps.engine.permissions import JobPermission, TaskPermission
from cvat.apps.engine.types import ExtendedRequest

from .models import IssueStatus, QcIssue, QcMembership, QcRole, QcRun
from .permissions import Qc3dPermission
from .qc.taxonomy import as_dict as taxonomy_as_dict
from .services import FixError, apply_fix, decide_issue, revert_fix, run_job_qc

RECENT_JOBS_LIMIT = 50

# who may run QC / decide on issues (CVAT admins always may)
RUN_ROLES = {QcRole.LEAD, QcRole.REVIEWER, QcRole.QA}
DECIDE_ROLES = {QcRole.LEAD, QcRole.REVIEWER, QcRole.QA}
FIX_ROLES = {QcRole.LEAD, QcRole.REVIEWER}


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
