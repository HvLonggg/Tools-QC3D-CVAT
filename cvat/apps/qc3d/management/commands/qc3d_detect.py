# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import os

from django.core.management.base import BaseCommand

from cvat.apps.engine.models import Job
from cvat.apps.qc3d import detection


class Command(BaseCommand):
    help = "Run the QC 3D detection model on every frame of a job (results are read by QC rule R12)"

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, required=True)
        parser.add_argument("--force", action="store_true", help="recompute frames already done")

    def handle(self, *args, job: int, force: bool, **options):
        pid_file = detection.state_dir() / f"job_{job}.pid"
        pid_file.write_text(str(os.getpid()))
        try:
            detection.run(Job.objects.get(id=job), force=force, log=lambda text: print(text, flush=True))
            print("xong", flush=True)
        finally:
            pid_file.unlink(missing_ok=True)
