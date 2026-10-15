# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from enum import StrEnum

from django.conf import settings

from cvat.apps.iam.permissions import OpenPolicyAgentPermission


class Qc3dPermission(OpenPolicyAgentPermission):
    class Scopes(StrEnum):
        VIEW_SUMMARY = "view:summary"
        VIEW_REPORT = "view:report"
        RUN_QC = "run:qc"
        DECIDE_ISSUE = "decide:issue"
        FIX_ISSUE = "fix:issue"
        VIEW_GUIDELINE = "view:guideline"
        MANAGE_GUIDELINE = "manage:guideline"
        VIEW_QUALITY = "view:quality"

    @classmethod
    def create(cls, request, view, obj, iam_context):
        return [
            cls.create_base_perm(request, view, scope, iam_context, obj)
            for scope in cls.get_scopes(request, view, obj)
        ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.url = settings.IAM_OPA_DATA_URL + "/qc3d/allow"

    @classmethod
    def _get_scopes(cls, request, view, obj):
        Scopes = cls.Scopes
        if view.action in ("guidelines", "guideline_detail"):
            return [Scopes.VIEW_GUIDELINE if request.method == "GET" else Scopes.MANAGE_GUIDELINE]
        if view.action in ("detect", "check"):
            return [Scopes.VIEW_REPORT if request.method == "GET" else Scopes.RUN_QC]
        if view.action == "job_settings":
            return [Scopes.VIEW_REPORT if request.method == "GET" else Scopes.DECIDE_ISSUE]
        return [
            {
                "summary": Scopes.VIEW_SUMMARY,
                "report": Scopes.VIEW_REPORT,
                "run": Scopes.RUN_QC,
                "decision": Scopes.DECIDE_ISSUE,
                "fix": Scopes.FIX_ISSUE,
                "revert": Scopes.FIX_ISSUE,
                "taxonomy": Scopes.VIEW_SUMMARY,
                "job_guideline": Scopes.VIEW_REPORT,
                "autofix": Scopes.FIX_ISSUE,
                "guideline_parse": Scopes.MANAGE_GUIDELINE,
                "guideline_compile": Scopes.MANAGE_GUIDELINE,
                "quality": Scopes.VIEW_QUALITY,
                "quality_job": Scopes.VIEW_QUALITY,
                "analytics": Scopes.VIEW_SUMMARY,
            }[view.action]
        ]

    def get_resource(self):
        return None
