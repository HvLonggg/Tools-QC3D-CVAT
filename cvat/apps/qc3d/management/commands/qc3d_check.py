# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

import os
import traceback

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from cvat.apps.engine.models import Job
from cvat.apps.qc3d import detection
from cvat.apps.qc3d.services import autofix_marked, marked_frames, run_job_qc


class Command(BaseCommand):
    help = (
        "QC check of a job: the detection model on the frames that miss it, then the QC rules; with --marked, "
        "only the frames of the boxes a reviewer marked, then auto-fix of those boxes only"
    )

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, required=True)
        parser.add_argument("--user", type=int, required=True)
        parser.add_argument("--frame", type=int, help="only this frame (default: the whole job)")
        parser.add_argument("--marked", help="comma separated box uids marked as wrong (s:<id>, t:<id>:<frame>)")

    def handle(self, *args, job: int, user: int, frame: int | None, marked: str | None, **options):
        pid = os.getpid()
        frames = {frame} if frame is not None else None
        uids = {uid for uid in (marked or "").split(",") if uid}
        state = {"pid": pid, "frame": frame, "marked": len(uids)}
        try:
            db_job = Job.objects.get(id=job)
            db_user = get_user_model().objects.get(id=user)
            if uids:
                frames = marked_frames(db_job, sorted(uids))
            if detection.model_path().exists():
                def progress(done, total):
                    detection.write_check(job, stage="model", done=done, total=total, **state)

                detection.write_check(job, stage="model", done=0, total=0, **state)
                detection.run(db_job, frames=frames, progress=progress, log=lambda text: print(text, flush=True))
            detection.write_check(job, stage="qc", done=0, total=0, **state)
            run = run_job_qc(db_job, db_user, frames, marked=uids)
            fixed = None
            if uids:
                detection.write_check(job, stage="fix", done=0, total=0, **state)
                fixed = autofix_marked(db_job, db_user, run, uids)
                print(f"marked {len(uids)}: applied {len(fixed['applied'])}, not fixed {len(fixed['failed'])}",
                      flush=True)
            detection.write_check(job, stage="done", run=run.id, issues=run.issues.count(), fixed=fixed, **state)
        except Exception as error:  # reported to the UI, the full trace stays in the log
            traceback.print_exc()
            detection.write_check(job, stage="error", error=str(error)[:300], **state)
