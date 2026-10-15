# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Build one 3D detection training set (OpenPCDet "custom" format) from PHENIKAA and nuScenes,
with the Vietnamese traffic classes of the QC 3D taxonomy.

  <out>/points/<frame>.npy       float32 (N, 5): x, y, z, intensity (0-255), time lag (0: one sweep)
  <out>/labels/<frame>.txt       x y z dx dy dz heading class   (box centre, LiDAR frame)
  <out>/ImageSets/train.txt      frame ids for training
  <out>/ImageSets/val.txt        frame ids for validation (held-out scenes)
  <out>/report.json              boxes per class / source / split, mapping decisions, skipped data

Both sources are put in the same geometry so one model learns from both:
  - the ground is moved to z = GROUND_Z (sensors are mounted at different heights): one constant
    offset per source, from the median bottom of its labeled boxes within 30 m (a per-frame ground
    estimate from the points was off by ~0.35 m on nuScenes because of walls, cars and vegetation),
  - intensity is on the nuScenes scale 0-255 (PHENIKAA LAZ up to 65535 is rescaled) and a 5th
    'time lag' column (0) is added, so models pre-trained on nuScenes can be fine-tuned as is,
  - points beyond --max-range are dropped (smaller files, same range for both).

Multi-sweep (--sweeps N > 1): the N-1 previous LiDAR sweeps are moved into the current frame with the ego
poses and added with their time lag (5th column, seconds), so far objects get more points (a car has
~3000 points at 0-10 m but ~36 at 40-54 m). Only points beyond --sweep-min-range are added from past
sweeps: near objects are dense already and the files stay ~1.7x a single sweep instead of N x.
The QC detector applies the same rule at inference (qc/sweeps.py).

Label clean-up (counted in report.json):
  - boxes with a non-positive size or non-finite values are dropped (PHENIKAA has a few),
  - PHENIKAA has no Truck / Bus classes and labels them "Car": a "Car" whose size fits Truck or Bus much
    better (QC rule R11 criterion: size log-likelihood margin >= 4 under the VN taxonomy) is relabeled,
    so both sources teach the same classes (about 8.6% Truck, 2% Bus of PHENIKAA "Car").

nuScenes is read from its JSON tables (no devkit needed): only LiDAR keyframes, boxes with
at least one LiDAR point. Official mini split: scenes 0103 and 0916 are validation.

Usage:
  python build_train_set.py --out D:/dataset/train_vn \
      --phenikaa "G:/My Drive/Mini Dataset" --phenikaa-val "RESIDENTIAL AREA" \
      --nuscenes D:/dataset/nuscenes --nuscenes-version v1.0-mini
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cvat.apps.qc3d.qc.sweeps import merge_sweeps  # noqa: E402
from cvat.apps.qc3d.qc.taxonomy import CLASSES as VN_CLASSES  # noqa: E402
from cvat.apps.qc3d.qc.taxonomy import best_class_by_size, size_log_likelihood  # noqa: E402

# Motorbike: a parked two-wheeler without rider (everywhere on VN streets; Rider has a person on it)
CLASSES = ["Car", "Truck", "Bus", "Rider", "Motorbike", "Pedestrian"]
GROUND_Z = -1.8  # common ground height after alignment (close to nuScenes, sensor ~1.8 m high)

# nuScenes category -> VN class; None = not a VN class (dropped, counted in the report)
NUSCENES_MAP = {
    "vehicle.car": "Car",
    "vehicle.emergency.police": "Car",
    "vehicle.emergency.ambulance": "Car",
    "vehicle.truck": "Truck",
    "vehicle.bus.rigid": "Bus",
    "vehicle.bus.bendy": "Bus",
    "vehicle.motorcycle": "Rider",  # only with a rider, see NUSCENES_NEEDS_RIDER
    "vehicle.bicycle": "Rider",
    "human.pedestrian.adult": "Pedestrian",
    "human.pedestrian.child": "Pedestrian",
    "human.pedestrian.construction_worker": "Pedestrian",
    "human.pedestrian.police_officer": "Pedestrian",
}
NUSCENES_NEEDS_RIDER = {"vehicle.motorcycle", "vehicle.bicycle"}  # without rider -> "Motorbike"
NUSCENES_MINI_VAL = {"scene-0103", "scene-0916"}

PHENIKAA_MAP = {"Car": "Car", "Rider": "Rider", "Pedestrian": "Pedestrian"}
RELABEL_MARGIN = 4.0  # same as QC rule R11 (qc/rules.py class_margin)


def valid_box(values) -> bool:
    return all(math.isfinite(v) for v in values) and min(values[3:6]) > 0


def vehicle_class(name: str, size: tuple[float, float, float]) -> str:
    """'Car' boxes the size of a truck or bus get that class (PHENIKAA has no Truck / Bus classes)."""
    if name != "Car":
        return name
    best, _ = best_class_by_size(size, ["Car", "Truck", "Bus"])
    margin = size_log_likelihood(VN_CLASSES[best], size) - size_log_likelihood(VN_CLASSES["Car"], size)
    return best if best != "Car" and margin >= RELABEL_MARGIN else "Car"


# ------------------------------------------------------------------------------------------- common


def source_ground(frames: list) -> float:
    """Ground height of a source in its LiDAR frame: median bottom of the labeled boxes within 30 m."""
    bottoms = [
        z - dz / 2
        for _, _, _, boxes in frames
        for x, y, z, _, _, dz, _, _ in boxes
        if math.hypot(x, y) < 30
    ]
    if not bottoms:
        raise SystemExit("No labeled boxes to estimate the ground height")
    return float(np.median(bottoms))


def write_frame(
    out: Path, frame_id: str, points: np.ndarray, boxes: list[tuple], max_range: float, shift: float,
) -> int:
    """boxes: (x, y, z, dx, dy, dz, heading, class) in the LiDAR frame. Returns boxes written."""
    if points is not None:  # None: labels only (the points of this frame are written already)
        keep = np.hypot(points[:, 0], points[:, 1]) <= max_range
        points = points[keep].copy()
        points[:, 2] += shift
        np.save(out / "points" / f"{frame_id}.npy", points.astype(np.float32))

    lines = []
    for x, y, z, dx, dy, dz, heading, name in boxes:
        if math.hypot(x, y) > max_range:
            continue
        lines.append(f"{x:.4f} {y:.4f} {z + shift:.4f} {dx:.4f} {dy:.4f} {dz:.4f} {heading:.6f} {name}")
    (out / "labels" / f"{frame_id}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


# ----------------------------------------------------------------------------------------- PHENIKAA


def read_laz(path: Path) -> np.ndarray:
    import laspy

    las = laspy.read(path)
    intensity = np.asarray(las.intensity, dtype=np.float32)
    if intensity.max(initial=0) > 255:
        intensity *= 255.0 / 65535.0
    return np.column_stack([las.x, las.y, las.z, intensity, np.zeros_like(intensity)]).astype(np.float32)


def phenikaa_time(stem: str) -> float:
    seconds, _, nanos = stem.partition("-")
    return int(seconds) + int(nanos) / 1e9


def phenikaa_pose(scene: Path, stem: str) -> np.ndarray | None:
    # LiDAR frame -> scene frame (the first frame of the scene), from ego_pose/<stem>.txt (3x4, row major)
    path = scene / "ego_pose" / f"{stem}.txt"
    if not path.exists():
        return None
    values = np.array(path.read_text().split(), dtype=np.float64)
    if values.size != 12:
        return None
    pose = np.eye(4)
    pose[:3] = values.reshape(3, 4)
    return pose


def phenikaa_loader(scene: Path, stems: list[str], index: int, sweeps: int, min_range: float):
    # points of stems[index] plus up to sweeps-1 earlier sweeps (consecutive, 10 Hz) in its frame
    stem = stems[index]
    path = scene / "lidar" / f"{stem}.laz"

    def load():
        current = read_laz(path)
        pose = phenikaa_pose(scene, stem) if sweeps > 1 else None
        if pose is None:
            return current
        past = []
        for k in range(1, sweeps):
            if index - k < 0:
                break
            older = stems[index - k]
            lag = phenikaa_time(stem) - phenikaa_time(older)
            older_pose = phenikaa_pose(scene, older)
            if older_pose is None or not 0 < lag <= 0.15 * k:  # a gap in the recording: stop
                break
            past.append((read_laz(scene / "lidar" / f"{older}.laz"), np.linalg.inv(pose) @ older_pose, lag))
        return merge_sweeps(current, past, min_range)

    return load


def phenikaa_frames(root: Path, val_scenes: set[str], report: dict, sweeps: int = 1, min_range: float = 20.0,
                    pseudo: bool = False):
    for scene in sorted(p for p in root.iterdir() if p.is_dir()):
        labels = {p.stem: p for p in (scene / "label").glob("*.txt")}
        # labels verified in CVAT (cvat_to_phenikaa.py: original + reviewed proposals) replace the originals
        verified = {p.stem: p for p in (scene / "label_verified").glob("*.txt")}
        report["phenikaa_verified_frames"] = report.get("phenikaa_verified_frames", 0) + len(verified)
        labels.update(verified)
        lidar = {p.stem: p for p in (scene / "lidar").glob("*.laz")}
        usable = sorted(set(labels) & set(lidar))
        all_stems = sorted(lidar, key=phenikaa_time)
        position = {stem: i for i, stem in enumerate(all_stems)}
        report["phenikaa_scenes"][scene.name] = {
            "labels": len(labels), "lidar": len(lidar), "used_frames": len(usable),
            "split": "val" if scene.name in val_scenes else "train",
        }
        for stem in usable:
            boxes = []
            lines = labels[stem].read_text().splitlines()
            proposed = scene / "label_proposed" / f"{stem}.txt"
            if pseudo and scene.name not in val_scenes and proposed.exists():
                # pseudo labels (propose_labels.py: confident, seen in neighbouring frames) for unlabeled objects;
                # train scenes only, so the held-out scenes keep measuring against human labels
                extra = proposed.read_text().splitlines()
                report["pseudo_labels"] = report.get("pseudo_labels", 0) + len(extra)
                lines += extra
            for line in lines:
                parts = line.split()
                if len(parts) != 8:
                    continue
                name = PHENIKAA_MAP.get(parts[7])
                if name is None:
                    report["dropped"][f"phenikaa:{parts[7]}"] += 1
                    continue
                values = [float(v) for v in parts[:7]]
                if not valid_box(values):
                    report["dropped"]["phenikaa:invalid_size"] += 1
                    continue
                x, y, z, length, width, height, yaw = values
                relabeled = vehicle_class(name, (length, width, height))
                if relabeled != name:
                    report["relabeled"][f"phenikaa:{name}->{relabeled}"] += 1
                boxes.append((x, y, z, length, width, height, yaw, relabeled))
            frame_id = "phx_" + scene.name.lower().replace(" ", "_") + "_" + stem.replace("-", "_")
            yield (frame_id, "val" if scene.name in val_scenes else "train",
                   phenikaa_loader(scene, all_stems, position[stem], sweeps, min_range), boxes)


# ----------------------------------------------------------------------------------------- nuScenes


def quat_to_matrix(q) -> np.ndarray:
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def nuscenes_frames(root: Path, version: str, report: dict, sweeps: int = 1, min_range: float = 20.0):
    def table(name):
        return json.loads((root / version / f"{name}.json").read_text())

    def by_token(rows):
        return {r["token"]: r for r in rows}

    scenes = by_token(table("scene"))
    samples = by_token(table("sample"))
    ego_poses = by_token(table("ego_pose"))
    sensors = by_token(table("sensor"))
    calibrated = by_token(table("calibrated_sensor"))
    categories = by_token(table("category"))
    attributes = by_token(table("attribute"))
    instances = by_token(table("instance"))
    annotations = defaultdict(list)
    for ann in table("sample_annotation"):
        annotations[ann["sample_token"]].append(ann)

    sample_data = by_token(table("sample_data"))

    def lidar_to_global(record) -> np.ndarray:
        ego, cal = ego_poses[record["ego_pose_token"]], calibrated[record["calibrated_sensor_token"]]
        to_ego, to_global = np.eye(4), np.eye(4)
        to_ego[:3, :3], to_ego[:3, 3] = quat_to_matrix(cal["rotation"]), cal["translation"]
        to_global[:3, :3], to_global[:3, 3] = quat_to_matrix(ego["rotation"]), ego["translation"]
        return to_global @ to_ego

    def read_bin(path: Path) -> np.ndarray:
        raw = np.fromfile(path, dtype=np.float32).reshape(-1, 5)  # x y z intensity ring
        points = raw.copy()
        points[:, 4] = 0.0  # time lag column
        return points

    lidar_keyframes = [
        sd for sd in sample_data.values()
        if sd["is_key_frame"] and sensors[calibrated[sd["calibrated_sensor_token"]]["sensor_token"]]["channel"] == "LIDAR_TOP"
    ]
    for sd in sorted(lidar_keyframes, key=lambda r: r["timestamp"]):
        scene_name = scenes[samples[sd["sample_token"]]["scene_token"]]["name"]
        split = "val" if scene_name in NUSCENES_MINI_VAL else "train"
        ego, cal = ego_poses[sd["ego_pose_token"]], calibrated[sd["calibrated_sensor_token"]]
        r_ego, t_ego = quat_to_matrix(ego["rotation"]), np.asarray(ego["translation"])
        r_cal, t_cal = quat_to_matrix(cal["rotation"]), np.asarray(cal["translation"])

        boxes = []
        for ann in annotations[sd["sample_token"]]:
            category = categories[instances[ann["instance_token"]]["category_token"]]["name"]
            name = NUSCENES_MAP.get(category)
            attrs = {attributes[a]["name"] for a in ann["attribute_tokens"]}
            if name and category in NUSCENES_NEEDS_RIDER and "cycle.with_rider" not in attrs:
                name = "Motorbike"
            if name is None or ann["num_lidar_pts"] < 1:
                report["dropped"][f"nuscenes:{category}" + ("" if ann["num_lidar_pts"] else ":no_points")] += 1
                continue
            # global -> ego -> LiDAR
            centre = r_cal.T @ (r_ego.T @ (np.asarray(ann["translation"]) - t_ego) - t_cal)
            rotation = r_cal.T @ r_ego.T @ quat_to_matrix(ann["rotation"])
            heading = math.atan2(rotation[1, 0], rotation[0, 0])
            width, length, height = ann["size"]  # nuScenes size is (w, l, h)
            boxes.append((*centre, length, width, height, heading, name))

        def load(record=sd):
            current = read_bin(root / record["filename"])
            if sweeps <= 1:
                return current
            to_key = np.linalg.inv(lidar_to_global(record))
            past, older = [], record
            for _ in range(sweeps - 1):
                if not older["prev"]:
                    break
                older = sample_data[older["prev"]]
                lag = (record["timestamp"] - older["timestamp"]) / 1e6
                past.append((read_bin(root / older["filename"]), to_key @ lidar_to_global(older), lag))
            return merge_sweeps(current, past, min_range)

        report["nuscenes_scenes"][scene_name] = split
        yield "nus_" + Path(sd["filename"]).name.split(".")[0], split, load, boxes


# --------------------------------------------------------------------------------------------- main


def build(args) -> dict:
    out = args.out
    for sub in ("points", "labels", "ImageSets"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    report = {
        "classes": CLASSES, "ground_z": GROUND_Z, "max_range": args.max_range,
        "sweeps": args.sweeps, "sweep_min_range": args.sweep_min_range,
        "phenikaa_scenes": {}, "nuscenes_scenes": {}, "dropped": Counter(), "relabeled": Counter(),
        "boxes": defaultdict(Counter), "frames": Counter(),
    }
    sources = []
    if args.phenikaa:
        sources.append(("phenikaa", phenikaa_frames(
            args.phenikaa, set(args.phenikaa_val), report, args.sweeps, args.sweep_min_range, args.pseudo_labels)))
    if args.nuscenes:
        sources.append(("nuscenes", nuscenes_frames(
            args.nuscenes, args.nuscenes_version, report, args.sweeps, args.sweep_min_range)))
    if not sources:
        raise SystemExit("Give --phenikaa and/or --nuscenes")

    splits = defaultdict(list)
    report["ground_shift"] = {}
    for source, frames in sources:
        frames = list(frames)  # points are loaded lazily, only labels are read here
        if not frames:
            continue
        shift = GROUND_Z - source_ground(frames)
        report["ground_shift"][source] = round(shift, 3)
        for frame_id, split, load, boxes in frames:
            if not boxes and not args.keep_empty:
                report["dropped"][f"{source}:frame_without_boxes"] += 1
                continue
            write_frame(out, frame_id, None if args.labels_only else load(), boxes, args.max_range, shift)
            splits[split].append(frame_id)
            report["frames"][f"{source}:{split}"] += 1
            for box in boxes:
                report["boxes"][f"{source}:{split}"][box[7]] += 1

    for split in ("train", "val"):
        (out / "ImageSets" / f"{split}.txt").write_text("\n".join(sorted(splits[split])) + "\n")
    report = json.loads(json.dumps(report))
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--phenikaa", type=Path, help="PHENIKAA 'Mini Dataset' dir (scenes with lidar/ label/)")
    parser.add_argument("--phenikaa-val", nargs="*", default=[], help="PHENIKAA scene names kept for validation")
    parser.add_argument("--nuscenes", type=Path, help="nuScenes root (contains v1.0-mini/ samples/)")
    parser.add_argument("--nuscenes-version", default="v1.0-mini")
    parser.add_argument("--max-range", type=float, default=54.0, help="drop points / boxes farther than this (m)")
    parser.add_argument("--keep-empty", action="store_true", help="keep frames without any VN-class box")
    parser.add_argument("--sweeps", type=int, default=1, help="LiDAR sweeps per frame (current + past ones)")
    parser.add_argument("--sweep-min-range", type=float, default=20.0,
                        help="past sweeps add only points beyond this range (m)")
    parser.add_argument("--pseudo-labels", action="store_true",
                        help="add label_proposed/ boxes (propose_labels.py) to the PHENIKAA train scenes")
    parser.add_argument("--labels-only", action="store_true",
                        help="rewrite labels/ and ImageSets/ only (points already in --out): for labels_overlay.zip")
    report = build(parser.parse_args())
    print(json.dumps({k: report[k] for k in ("frames", "boxes", "ground_shift")}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
