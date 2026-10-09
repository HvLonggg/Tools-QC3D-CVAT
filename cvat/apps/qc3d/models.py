# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

from django.conf import settings
from django.db import models


class QcRole(models.TextChoices):
    LEAD = "lead"
    REVIEWER = "reviewer"
    QA = "qa"
    ML_ENGINEER = "ml_engineer"


class QcMembership(models.Model):
    """QC 3D role of a CVAT user. CVAT admins have every role implicitly."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="qc3d_memberships",
    )
    role = models.CharField(max_length=32, choices=QcRole.choices)
    created_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "role"], name="qc3d_unique_user_role"),
        ]

    def __str__(self) -> str:
        return f"{self.user} ({self.role})"
