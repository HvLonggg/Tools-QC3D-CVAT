# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="QcMembership",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("lead", "Lead"),
                            ("reviewer", "Reviewer"),
                            ("qa", "Qa"),
                            ("ml_engineer", "Ml Engineer"),
                        ],
                        max_length=32,
                    ),
                ),
                ("created_date", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="qc3d_memberships",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("user", "role"), name="qc3d_unique_user_role"
                    )
                ],
            },
        ),
    ]
