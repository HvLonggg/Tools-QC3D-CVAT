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
        return [
            {
                "summary": Scopes.VIEW_SUMMARY,
                "report": Scopes.VIEW_REPORT,
                "run": Scopes.RUN_QC,
                "decision": Scopes.DECIDE_ISSUE,
            }[view.action]
        ]

    def get_resource(self):
        return None
