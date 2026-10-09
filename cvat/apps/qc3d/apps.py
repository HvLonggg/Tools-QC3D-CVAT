# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.apps import AppConfig


class Qc3dConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cvat.apps.qc3d"

    def ready(self) -> None:
        from cvat.apps.iam.permissions import load_app_iam_rules

        load_app_iam_rules(self)
