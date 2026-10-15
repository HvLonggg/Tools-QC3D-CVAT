# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Run the detection model on the frames of a job and keep the result (QcDetection), for QC rule R12.

The model (CenterPoint-Pillar, numpy, qc/detector.py) takes ~5 s per frame on a laptop CPU, so it runs in a
background process (`manage.py qc3d_detect --job <id>`) started from the API; QC then only reads the stored
boxes. Weights: model_weights/<model key>.npz in this app (not in git); QC3D_MODEL_KEY picks a model.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from django.conf import settings
from django.db import transaction

from cvat.apps.engine.models import Job

from .models import QcDetection

# newest first: the first model whose weights file exists is used
MODELS = [
    {
        "key": "vn-cp-pillar-v5",
        "name": "CenterPoint-Pillar v5 · 4 nhóm · đường cao thấp khác nhau",
        "trained_on": "PHENIKAA (10 scene, 4 lần quét + nhãn xa đề xuất) + nuScenes-mini; học tiếp từ v3",
        # held-out PHENIKAA scenes, eval_predictions.py --source phx --groups (AP 2 m; v3 in brackets)
        "trusted": "Car, xe 2 bánh trong 30 m (đúng 90 % / 88 %); AP Car 75,9 (69,1), xe lớn 73,3 (62,9), "
                   "xe 2 bánh 85,6 (82,4); xe ở nền thấp bắt được 46 % (25 %)",
        "config": "v2",
    },
    {
        "key": "vn-cp-pillar-v2",
        "name": "CenterPoint-Pillar v3 · 4 nhóm (Truck/Bus, Rider/Motorbike tách theo guideline)",
        "trained_on": "PHENIKAA (10 scene, 4 lần quét + nhãn xa đề xuất) + nuScenes-mini",
        # held-out PHENIKAA scenes, eval_predictions.py --source phx --groups (AP 2 m; v1 in brackets)
        "trusted": "Car, xe 2 bánh trong 30 m (đúng 94 % / 89 %); AP Car 69,1 (68,4), xe 2 bánh 82,4 (78,4)",
        "config": "v2",
    },
    {
        "key": "vn-cp-pillar-e20",
        "name": "CenterPoint-Pillar · class VN · epoch 20",
        "trained_on": "PHENIKAA (10 scene) + nuScenes-mini",
        # measured on the held-out PHENIKAA scenes (utils/qc3d/eval_predictions.py --source phx)
        "trusted": "Car, Rider trong 40 m (đúng 85-96 %)",
        "config": "v1",
    },
]
APP_DIR = Path(__file__).resolve().parent
MIN_SCORE = 0.3  # boxes kept per frame
GROUND_RADIUS = 30.0


def weights_path(model: dict) -> Path:
    return APP_DIR / "model_weights" / f"{model['key']}.npz"


def active_model() -> dict:
    """QC3D_MODEL_KEY if set, else the newest model whose weights are present."""
    wanted = os.environ.get("QC3D_MODEL_KEY")
    for model in MODELS:
        if (wanted and model["key"] == wanted) or (not wanted and weights_path(model).exists()):
            return model
    return MODELS[-1]


def model_path() -> Path:
    return weights_path(active_model())


def _info(model: dict) -> dict:
    return {k: v for k, v in model.items() if k != "config"}


def state_dir() -> Path:
    path = Path(getattr(settings, "TMP_FILES_ROOT", "/tmp")) / "qc3d_detect"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_ground(job: Job, cuboids, point_clouds: dict) -> float:
    """
    Road height of the job in its LiDAR frame, as the training data was normalized: median bottom of the
    labeled boxes within 30 m (robust to a few wrong labels); from the points when there are few labels.
    """
    bottoms = [
        c.position[2] - c.scale[2] / 2 for c in cuboids if np.hypot(c.position[0], c.position[1]) < GROUND_RADIUS
    ]
    if len(bottoms) >= 10:
        return float(np.median(bottoms))
    lows = []
    for points in point_clouds.values():
        r = np.hypot(points[:, 0], points[:, 1])
        near = points[(r > 3) & (r < GROUND_RADIUS)]
        if len(near):
            keys = np.floor(near[:, :2] / 2.0).astype(np.int64)
            order = np.lexsort((keys[:, 1], keys[:, 0]))
            keys, z = keys[order], near[order, 2]
            split = np.flatnonzero(np.any(np.diff(keys, axis=0) != 0, axis=1)) + 1
            cells = [np.percentile(c, 10) for c in np.split(z, split) if len(c) >= 5]
            if cells:
                lows.append(np.percentile(cells, 25))
    return float(np.median(lows)) if lows else -1.8


def detections_for(job: Job, frames=None) -> dict[int, list] | None:
    """frame -> [(box7, label, score)] for R12; None when the model has not run on this job."""
    rows = QcDetection.objects.filter(job=job, model_key=active_model()["key"])
    if frames is not None:
        rows = rows.filter(frame__in=frames)
    result = {row.frame: [(b[:7], b[7], b[8]) for b in row.boxes] for row in rows}
    return result or None


def status(job: Job) -> dict:
    total = job.segment.task.data.images.filter(
        frame__gte=job.segment.start_frame, frame__lte=job.segment.stop_frame
    ).count()
    done = QcDetection.objects.filter(job=job, model_key=active_model()["key"]).count()
    pid_file = state_dir() / f"job_{job.id}.pid"
    running = False
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), 0)
            running = True
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)
    log_file = state_dir() / f"job_{job.id}.log"
    last = log_file.read_text(errors="replace").strip().splitlines()[-1:] if log_file.exists() else []
    return {
        "model": {**_info(active_model()), "available": model_path().exists()},
        "frames_total": total,
        "frames_done": done,
        "running": running,
        "last_message": last[0] if last else "",
    }


def start(job: Job) -> dict:
    """Start `manage.py qc3d_detect` for the job in the background (one run per job at a time)."""
    if not model_path().exists():
        raise FileNotFoundError(f"Chưa có trọng số model ({model_path().name})")
    current = status(job)
    if current["running"]:
        return current
    log = open(state_dir() / f"job_{job.id}.log", "w")
    process = subprocess.Popen(
        [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "qc3d_detect", "--job", str(job.id)],
        stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(settings.BASE_DIR),
    )
    (state_dir() / f"job_{job.id}.pid").write_text(str(process.pid))
    return {**current, "running": True}


def run(job: Job, *, force: bool = False, log=print, frames=None, progress=None) -> int:
    """
    Detect on the frames of the job not done yet (all, or `frames`); returns the number of frames processed.
    `progress(done, total)` is called after each frame.
    """
    from cvat.apps.engine.models import Label

    from .qc.detector import CONFIG, CONFIG_V2, Detector, split_group
    from .qc.guideline import qc_config
    from .services import build_cuboids, get_job_data, load_point_clouds, resolve_guideline

    model = active_model()
    detector = Detector.from_file(model_path(), CONFIG_V2 if model["config"] == "v2" else CONFIG)
    classes = qc_config(resolve_guideline(job)["content"])["classes"]
    job_data = get_job_data(job.id)
    label_ids = {s["label_id"] for s in job_data["shapes"]} | {t["label_id"] for t in job_data["tracks"]}
    labels = dict(Label.objects.filter(id__in=label_ids).values_list("id", "name"))
    cuboids = build_cuboids(job_data, labels)
    all_frames = list(range(job.segment.start_frame, job.segment.stop_frame + 1))
    frames = all_frames if frames is None else [f for f in all_frames if f in set(frames)]
    done = set() if force else set(
        QcDetection.objects.filter(job=job, model_key=model["key"]).values_list("frame", flat=True)
    )
    todo = [f for f in frames if f not in done]
    if not todo:
        return 0
    sample = load_point_clouds(job, set(all_frames[:: max(1, len(all_frames) // 10)]), with_intensity=True)
    ground = job_ground(job, cuboids, sample)
    log(f"job {job.id}: model {model['key']}, {len(todo)}/{len(frames)} frame cần chạy, mặt đường z = {ground:.2f} m")
    for count, frame in enumerate(todo, 1):
        clouds = load_point_clouds(job, {frame}, with_intensity=True)
        if frame not in clouds:
            continue
        detections = detector.predict(clouds[frame], ground_z=ground)
        boxes = [
            [*(round(float(v), 4) for v in d.box), split_group(d.label, d.box, classes), round(d.score, 4)]
            for d in detections if d.score >= MIN_SCORE
        ]
        with transaction.atomic():
            QcDetection.objects.update_or_create(
                job=job, frame=frame, model_key=model["key"], defaults={"boxes": boxes, "ground_z": ground}
            )
        log(f"{count}/{len(todo)} frame {frame}: {len(boxes)} box")
        if progress:
            progress(count, len(todo))
    return len(todo)


# ----------------------------------------------------------------------------------- QC check pipeline


def _check_file(job_id: int) -> Path:
    return state_dir() / f"check_{job_id}.json"


def write_check(job_id: int, **state) -> None:
    import json

    _check_file(job_id).write_text(json.dumps(state, ensure_ascii=False, default=str))


def check_status(job: Job) -> dict:
    import json

    path = _check_file(job.id)
    if not path.exists():
        return {"stage": "idle"}
    state = json.loads(path.read_text())
    if state.get("stage") in ("model", "qc", "fix"):
        try:
            os.kill(int(state.get("pid", 0)), 0)
        except (OSError, ValueError):
            state = {**state, "stage": "error", "error": "Tiến trình kiểm tra đã dừng giữa chừng"}
    return state


def start_check(job: Job, user, frame: int | None, *, marked: list[str] | None = None) -> dict:
    """
    Model on the frames that miss it, then QC, in a background process (one per job). With `marked`
    (boxes a reviewer marked as wrong): only their frames, then auto-fix of those boxes only.
    """
    current = check_status(job)
    if current.get("stage") in ("model", "qc", "fix"):
        return current
    log = open(state_dir() / f"check_{job.id}.log", "w")
    command = [sys.executable, str(Path(settings.BASE_DIR) / "manage.py"), "qc3d_check",
               "--job", str(job.id), "--user", str(user.id)]
    if frame is not None:
        command += ["--frame", str(frame)]
    if marked:
        command += ["--marked", ",".join(marked)]
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                               cwd=str(settings.BASE_DIR))
    write_check(job.id, stage="model", done=0, total=0, pid=process.pid, frame=frame, marked=len(marked or []))
    return check_status(job)
