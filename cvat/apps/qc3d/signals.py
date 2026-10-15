# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""Job assignee / stage history (QcJobAssignment), recorded on every Job save that changes them."""

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from cvat.apps.engine.models import Job

from .models import QcJobAssignment


@receiver(pre_save, sender=Job, dispatch_uid="qc3d_job_assignment_before")
def remember_assignment(sender, instance: Job, **kwargs):
    if instance.pk and not kwargs.get("raw"):
        instance._qc3d_before = Job.objects.filter(pk=instance.pk).values_list("assignee_id", "stage").first()


@receiver(post_save, sender=Job, dispatch_uid="qc3d_job_assignment_after")
def record_assignment(sender, instance: Job, created: bool, **kwargs):
    if kwargs.get("raw"):
        return
    current = (instance.assignee_id, str(instance.stage))
    before = None if created else getattr(instance, "_qc3d_before", None)
    if before is not None and (before[0], str(before[1])) == current:
        return
    now = timezone.now()
    QcJobAssignment.objects.filter(job_id=instance.pk, ended_date=None).update(ended_date=now)
    if instance.assignee_id:
        QcJobAssignment.objects.create(
            job_id=instance.pk, user_id=instance.assignee_id, stage=current[1], started_date=now
        )
