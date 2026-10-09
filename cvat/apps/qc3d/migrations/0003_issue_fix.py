# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('qc3d', '0002_qcrun_qcissue'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='qcissue',
            name='fix_before',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='qcissue',
            name='fix_status',
            field=models.CharField(choices=[('none', 'None'), ('applied', 'Applied'), ('reverted', 'Reverted')], default='none', max_length=16),
        ),
        migrations.AddField(
            model_name='qcissue',
            name='fixed_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='qcissue',
            name='fixed_date',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='qcissue',
            name='proposal',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='qcissue',
            name='snapshot',
            field=models.JSONField(blank=True, null=True),
        ),
    ]
