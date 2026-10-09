# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.db.models import Count
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from cvat.apps.engine.models import DimensionType, Job, Task
from cvat.apps.engine.permissions import JobPermission, TaskPermission
from cvat.apps.engine.types import ExtendedRequest

from .models import QcMembership, QcRole
from .permissions import Qc3dPermission

RECENT_JOBS_LIMIT = 50


@extend_schema(tags=["qc3d"])
class Qc3dViewSet(viewsets.ViewSet):
    iam_permission_class = Qc3dPermission

    @extend_schema(
        summary="Get QC 3D overview for the current user",
        responses={"200": OpenApiResponse(description="Roles, 3D task/job counters, recent 3D jobs")},
    )
    @action(detail=False, methods=["GET"])
    def summary(self, request: ExtendedRequest):
        is_admin = request.iam_context["privilege"] == "admin"
        if is_admin:
            roles = list(QcRole.values)
        else:
            roles = list(
                QcMembership.objects.filter(user=request.user).values_list("role", flat=True)
            )

        tasks = TaskPermission.create_scope_list(request).filter(
            Task.objects.filter(dimension=DimensionType.DIM_3D)
        )
        jobs = JobPermission.create_scope_list(request).filter(
            Job.objects.filter(segment__task__dimension=DimensionType.DIM_3D)
        )

        jobs_by_stage = {
            row["stage"]: row["count"]
            for row in jobs.values("stage").annotate(count=Count("id")).order_by()
        }

        recent_jobs = [
            {
                "id": job.id,
                "task_id": job.segment.task_id,
                "task_name": job.segment.task.name,
                "stage": job.stage,
                "state": job.state,
                "assignee": job.assignee.username if job.assignee else None,
                "updated_date": job.updated_date,
            }
            for job in jobs.select_related("segment__task", "assignee").order_by("-updated_date")[
                :RECENT_JOBS_LIMIT
            ]
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
