# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT


import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('engine', '0107_remove_annotationguide_is_public'),
        ('qc3d', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='QcRun',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_date', models.DateTimeField(auto_now_add=True)),
                ('rules_version', models.CharField(max_length=32)),
                ('annotations_version', models.IntegerField(default=0)),
                ('objects_count', models.IntegerField(default=0)),
                ('duration_ms', models.IntegerField(default=0)),
                ('coverage', models.JSONField(default=list)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='qc3d_runs', to='engine.job')),
            ],
            options={
                'ordering': ('-id',),
            },
        ),
        migrations.CreateModel(
            name='QcIssue',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rule', models.CharField(max_length=8)),
                ('severity', models.CharField(choices=[('blocker', 'Blocker'), ('major', 'Major'), ('minor', 'Minor')], max_length=16)),
                ('frame', models.IntegerField()),
                ('uid', models.CharField(max_length=64)),
                ('label', models.CharField(blank=True, default='', max_length=64)),
                ('message', models.TextField()),
                ('evidence', models.JSONField(default=dict)),
                ('status', models.CharField(choices=[('open', 'Open'), ('confirmed', 'Confirmed'), ('waived', 'Waived')], default='open', max_length=16)),
                ('decided_date', models.DateTimeField(blank=True, null=True)),
                ('decision_reason', models.TextField(blank=True, default='')),
                ('decided_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='issues', to='qc3d.qcrun')),
            ],
            options={
                'ordering': ('frame', 'rule', 'id'),
                'indexes': [models.Index(fields=['run', 'status'], name='qc3d_qcissu_run_id_dafb33_idx')],
            },
        ),
    ]
