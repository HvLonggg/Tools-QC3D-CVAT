# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Propose labels for the objects PHENIKAA left unlabeled (mostly far ones), for people to verify in CVAT.

  1. the detection model runs on every frame of a scene (or its predictions are read from a result.pkl),
  2. a detection is a candidate when no label lies within 2 m, it is beyond --min-range, confident and of a
     class the model is reliable for (Pedestrian is left out),
  3. a candidate is kept only if the same object is seen in at least --min-support of the 4 neighbouring
     frames (+-2, positions compared in the scene frame with the ego poses, allowing for its motion):
     single-frame false alarms are dropped,
  4. output, per scene: label_proposed/<stem>.txt (PHENIKAA format: x y z l w h yaw class, LiDAR frame) and
     label_review/<stem>.txt = original labels + proposals, to import into CVAT (phenikaa_to_cvat.py
     --label-dir label_review) and verify; verified labels go to label_verified/ (cvat_to_phenikaa.py),
     which build_train_set.py prefers over the original labels.

  python propose_labels.py --phenikaa D:/dataset/phenikaa_mini --scenes "COMPLEX CITY" \\
      --model cvat/apps/qc3d/model_weights/vn-cp-pillar-e20.npz
  python propose_labels.py --phenikaa D:/dataset/phenikaa_mini --detections result.pkl   # no model run
"""

from __future__ import annotations

import argparse
import json
import math
import pickle
import sys
from collections import Counter
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
from build_train_set import phenikaa_pose, phenikaa_time, read_laz  # noqa: E402

PROPOSED_CLASSES = {"Car", "Truck", "Bus", "Rider", "Motorbike", "LargeVehicle", "TwoWheeler"}
MAX_SPEED = 15.0  # m/s: how far an object may move between neighbouring frames


def phenikaa_class(name: str, box) -> str:
    """PHENIKAA label classes: Car (any car / truck / bus), Rider, Pedestrian."""
    if name in ("Truck", "Bus", "LargeVehicle", "Car"):
        return "Car"
    if name in ("TwoWheeler", "Motorbike", "Rider"):
        return "Rider"
    return name


def read_labels(path: Path) -> list[list]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 8:
            rows.append([*(float(v) for v in parts[:7]), parts[7]])
    return rows


def scene_ground(scene: Path) -> float:
    """Road height of the scene in its LiDAR frame: median bottom of the labeled boxes within 30 m."""
    bottoms = [
        z - h / 2 for f in sorted((scene / "label").glob("*.txt"))[::5]
        for x, y, z, _, _, h, _, _ in read_labels(f) if math.hypot(x, y) < 30
    ]
    return float(np.median(bottoms)) if bottoms else 0.0


def detections_from_model(scene: Path, stems: list[str], model: Path, config: str, log) -> dict:
    from cvat.apps.qc3d.qc.detector import CONFIG, CONFIG_V2, Detector

    detector = Detector.from_file(model, CONFIG_V2 if config == "v2" else CONFIG)
    ground = scene_ground(scene)
    result = {}
    for n, stem in enumerate(stems, 1):
        points = read_laz(scene / "lidar" / f"{stem}.laz")[:, :4]
        result[stem] = [(d.box.tolist(), d.label, d.score) for d in detector.predict(points, ground_z=ground)]
        log(f"  {scene.name}: model {n}/{len(stems)}")
    return result


def detections_from_pickle(scene: Path, stems: list[str], path: Path) -> dict:
    """result.pkl of OpenPCDet on the training set: boxes are in the shifted frame (road at -1.8 m)."""
    annos = {a["frame_id"]: a for a in pickle.loads(path.read_bytes())}
    prefix = "phx_" + scene.name.lower().replace(" ", "_") + "_"
    shift = -1.8 - scene_ground(scene)
    result = {}
    for stem in stems:
        anno = annos.get(prefix + stem.replace("-", "_"))
        if anno is None:
            continue
        boxes = np.asarray(anno["boxes_lidar"], dtype=np.float64).reshape(-1, 7).copy()
        boxes[:, 2] -= shift
        result[stem] = [(b.tolist(), str(n), float(s)) for b, n, s in zip(boxes, anno["name"], anno["score"])]
    return result


def to_scene(pose: np.ndarray, xy) -> np.ndarray:
    return pose[:2, :2] @ np.asarray(xy[:2]) + pose[:2, 3]


def propose_scene(scene: Path, detections: dict, args, report: Counter, labels_of=None, write: bool = True) -> dict:
    """stem -> [(box, class, score)] proposals; written to label_proposed/ and label_review/ when `write`."""
    labels_of = labels_of or (lambda stem: read_labels(scene / "label" / f"{stem}.txt"))
    stems = sorted(detections, key=phenikaa_time)
    poses = {s: phenikaa_pose(scene, s) for s in stems}
    candidates = {}
    for stem in stems:
        labels = labels_of(stem)
        centres = np.array([l[:2] for l in labels]) if labels else np.zeros((0, 2))
        kept = []
        for box, name, score in detections[stem]:
            if name not in PROPOSED_CLASSES or score < args.score or math.hypot(*box[:2]) < args.min_range:
                continue
            if len(centres) and np.hypot(*(centres - np.asarray(box[:2])).T).min() < 2.0:
                continue  # already labeled
            kept.append((box, name, score))
        candidates[stem] = kept

    result = {}
    for index, stem in enumerate(stems):
        pose = poses[stem]
        proposals = []
        for box, name, score in candidates[stem]:
            support = 0
            if pose is not None:
                here = to_scene(pose, box)
                for other in stems[max(0, index - 2):index + 3]:
                    if other == stem or poses[other] is None:
                        continue
                    reach = 1.0 + MAX_SPEED * abs(phenikaa_time(other) - phenikaa_time(stem))
                    seen = [b for b, _, s in detections[other] if s >= args.score * 0.6] + [
                        l[:3] for l in labels_of(other)
                    ]
                    if any(np.hypot(*(to_scene(poses[other], b) - here)) <= reach for b in seen):
                        support += 1
            if support < args.min_support:
                report["dropped: not seen in neighbouring frames"] += 1
                continue
            proposals.append((box, phenikaa_class(name, box), score))
            report[f"proposed {phenikaa_class(name, box)}"] += 1
        if not proposals:
            continue
        result[stem] = proposals
        if not write:
            continue
        lines = [" ".join(f"{v:.4f}" for v in box[:6]) + f" {box[6]:.6f} {name}" for box, name, _ in proposals]
        for folder in ("label_proposed", "label_review"):
            (scene / folder).mkdir(exist_ok=True)
        (scene / "label_proposed" / f"{stem}.txt").write_text("\n".join(lines) + "\n")
        original = (scene / "label" / f"{stem}.txt").read_text().rstrip("\n")
        (scene / "label_review" / f"{stem}.txt").write_text((original + "\n" if original else "") + "\n".join(lines) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phenikaa", type=Path, required=True)
    parser.add_argument("--scenes", nargs="*", help="scene names (default: every scene with LiDAR)")
    parser.add_argument("--model", type=Path, help="detector weights (.npz / .pth)")
    parser.add_argument("--model-config", choices=["v1", "v2"], default="v1")
    parser.add_argument("--detections", type=Path, help="OpenPCDet result.pkl instead of running the model")
    parser.add_argument("--min-range", type=float, default=30.0, help="propose only objects beyond this (m)")
    parser.add_argument("--score", type=float, default=0.5)
    parser.add_argument("--min-support", type=int, default=2, help="neighbouring frames (of 4) that must see it")
    args = parser.parse_args()
    if not (args.model or args.detections):
        raise SystemExit("Give --model or --detections")

    report = Counter()
    scenes = [args.phenikaa / s for s in args.scenes] if args.scenes else sorted(
        p for p in args.phenikaa.iterdir() if p.is_dir() and any((p / "lidar").glob("*.laz"))
    )
    for scene in scenes:
        stems = sorted({p.stem for p in (scene / "lidar").glob("*.laz")} & {p.stem for p in (scene / "label").glob("*.txt")},
                       key=phenikaa_time)
        detections = (detections_from_pickle(scene, stems, args.detections) if args.detections
                      else detections_from_model(scene, stems, args.model, args.model_config, print))
        if not detections:
            continue
        written = sum(len(v) for v in propose_scene(scene, detections, args, report).values())
        print(f"{scene.name}: {written} nhãn đề xuất trên {len(detections)} frame")
    print(json.dumps(dict(report), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
