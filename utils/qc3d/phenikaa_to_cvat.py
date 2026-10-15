# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Convert one PHENIKAA scene (LAZ point clouds + KITTI-like cuboid labels) into files
that CVAT can ingest as a 3D task:

  <out>/pointclouds.zip            flat binary PCD files, one per LiDAR frame (task data)
  <out>/annotations_datumaro3d.zip "Datumaro 3D 1.0" annotations for the labeled frames
  <out>/ego_poses.json             3x4 sensor->world poses per frame (for temporal QC rules)
  <out>/report.json                data checks: unmatched labels, points per box, by class/range

PHENIKAA label line: x y z length width height yaw class
  - x, y, z: box center in the LiDAR frame (m); verified: box bottom matches the ground
  - yaw: rotation around z (rad), stored as CVAT rotation [0, 0, yaw]

Usage:
  python phenikaa_to_cvat.py --scene "D:/dataset/Mini Dataset/RESIDENTIAL AREA" --out D:/dataset/cvat/residential
"""

import argparse
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

import laspy
import numpy as np

LABELS = ["Car", "Rider", "Pedestrian"]
RANGE_BINS = [(0, 30), (30, 60), (60, 100), (100, 1000)]


def read_laz(path: Path) -> np.ndarray:
    las = laspy.read(path)
    return np.column_stack([las.x, las.y, las.z, las.intensity]).astype(np.float32)


def pcd_bytes(points: np.ndarray) -> bytes:
    header = (
        "# .PCD v0.7 - Point Cloud Data file format\n"
        "VERSION 0.7\n"
        "FIELDS x y z intensity\n"
        "SIZE 4 4 4 4\n"
        "TYPE F F F F\n"
        "COUNT 1 1 1 1\n"
        f"WIDTH {len(points)}\n"
        "HEIGHT 1\n"
        "VIEWPOINT 0 0 0 1 0 0 0\n"
        f"POINTS {len(points)}\n"
        "DATA binary\n"
    )
    return header.encode("ascii") + np.ascontiguousarray(points, dtype="<f4").tobytes()


def read_labels(path: Path) -> list[dict]:
    boxes = []
    for line_no, line in enumerate(path.read_text().splitlines(), start=1):
        parts = line.split()
        if not parts:
            continue
        if len(parts) != 8:
            raise ValueError(f"{path}:{line_no}: expected 8 columns, got {len(parts)}")
        x, y, z, length, width, height, yaw = map(float, parts[:7])
        boxes.append(
            {
                "label": parts[7],
                "position": [x, y, z],
                "rotation": [0.0, 0.0, yaw],
                "scale": [length, width, height],
            }
        )
    return boxes


def points_in_box(points: np.ndarray, box: dict) -> int:
    """Number of points inside a z-rotated cuboid."""
    d = points[:, :3] - np.asarray(box["position"], dtype=np.float32)
    yaw = box["rotation"][2]
    c, s = np.cos(-yaw), np.sin(-yaw)
    local_x = d[:, 0] * c - d[:, 1] * s
    local_y = d[:, 0] * s + d[:, 1] * c
    length, width, height = box["scale"]
    inside = (
        (np.abs(local_x) <= length / 2)
        & (np.abs(local_y) <= width / 2)
        & (np.abs(d[:, 2]) <= height / 2)
    )
    return int(inside.sum())


def read_pose(path: Path) -> list[list[float]]:
    values = [float(v) for v in path.read_text().split()]
    if len(values) != 12:
        raise ValueError(f"{path}: expected 12 values (3x4 matrix), got {len(values)}")
    return [values[0:4], values[4:8], values[8:12]]


def range_bin(distance: float) -> str:
    for lo, hi in RANGE_BINS:
        if lo <= distance < hi:
            return f"{lo}-{hi}m"
    return "other"


def convert(scene: Path, out: Path, labeled_only: bool, max_frames: int | None, label_dir: str = "label") -> dict:
    lidar_files = sorted((scene / "lidar").glob("*.laz"))
    label_files = {p.stem: p for p in sorted((scene / "label").glob("*.txt"))}
    # another label folder (label_review: original + proposed labels) overrides the original per frame
    label_files.update({p.stem: p for p in sorted((scene / label_dir).glob("*.txt"))} if label_dir != "label" else {})
    pose_files = {p.stem: p for p in sorted((scene / "ego_pose").glob("*.txt"))}
    if not lidar_files:
        raise SystemExit(f"No LiDAR frames in {scene / 'lidar'}")

    if labeled_only:
        lidar_files = [p for p in lidar_files if p.stem in label_files]
    if max_frames:
        lidar_files = lidar_files[:max_frames]
    frame_ids = [p.stem for p in lidar_files]

    report = {
        "scene": scene.name,
        "frames": len(frame_ids),
        "labeled_frames": 0,
        "labels_without_lidar": sorted(set(label_files) - {p.stem for p in (scene / "lidar").glob("*.laz")}),
        "frames_without_pose": [f for f in frame_ids if f not in pose_files],
        "boxes": 0,
        "boxes_by_label": defaultdict(int),
        "unknown_labels": defaultdict(int),
        "points_in_box": defaultdict(lambda: {"boxes": 0, "zero_points": 0, "median_points": 0.0}),
    }
    per_bin_counts = defaultdict(list)

    out.mkdir(parents=True, exist_ok=True)
    items = []
    with zipfile.ZipFile(out / "pointclouds.zip", "w", zipfile.ZIP_DEFLATED) as pcd_zip:
        for frame_index, lidar_path in enumerate(lidar_files):
            frame_id = lidar_path.stem
            points = read_laz(lidar_path)
            pcd_zip.writestr(f"{frame_id}.pcd", pcd_bytes(points))

            if frame_id not in label_files:
                continue

            report["labeled_frames"] += 1
            annotations = []
            for box in read_labels(label_files[frame_id]):
                if box["label"] not in LABELS:
                    report["unknown_labels"][box["label"]] += 1
                    continue

                n_points = points_in_box(points, box)
                distance = float(np.hypot(*box["position"][:2]))
                per_bin_counts[(box["label"], range_bin(distance))].append(n_points)
                report["boxes"] += 1
                report["boxes_by_label"][box["label"]] += 1

                annotations.append(
                    {
                        "id": len(annotations),
                        "type": "cuboid_3d",
                        "attributes": {"occluded": False},
                        "group": 0,
                        "label_id": LABELS.index(box["label"]),
                        "position": box["position"],
                        "rotation": box["rotation"],
                        "scale": box["scale"],
                    }
                )

            items.append(
                {
                    "id": frame_id,
                    "annotations": annotations,
                    "attr": {"frame": frame_index},
                    "point_cloud": {"path": ""},
                }
            )

    dataset = {
        "dm_format_version": "1.0",
        "media_type": 6,  # datumaro MediaType.POINT_CLOUD
        "infos": {"source": "PHENIKAA", "scene": scene.name},
        "categories": {
            "label": {
                "labels": [{"name": name, "parent": "", "attributes": []} for name in LABELS],
                "label_groups": [],
                "attributes": ["occluded"],
            }
        },
        "items": items,
    }
    with zipfile.ZipFile(out / "annotations_datumaro3d.zip", "w", zipfile.ZIP_DEFLATED) as ann_zip:
        ann_zip.writestr("annotations/default.json", json.dumps(dataset, indent=1))

    poses = {f: read_pose(pose_files[f]) for f in frame_ids if f in pose_files}
    (out / "ego_poses.json").write_text(json.dumps(poses, indent=1))

    for (label, bin_name), counts in sorted(per_bin_counts.items()):
        report["points_in_box"][f"{label} {bin_name}"] = {
            "boxes": len(counts),
            "zero_points": int(sum(c == 0 for c in counts)),
            "median_points": float(np.median(counts)),
        }
    report = json.loads(json.dumps(report))
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scene", type=Path, required=True, help="PHENIKAA scene dir with lidar/ label/ ego_pose/")
    parser.add_argument("--out", type=Path, required=True, help="output dir")
    parser.add_argument("--labeled-only", action="store_true", help="export only frames that have labels")
    parser.add_argument("--max-frames", type=int, default=None, help="limit the number of frames")
    parser.add_argument("--label-dir", default="label",
                        help="label folder of the scene (label_review: original + proposed labels to verify)")
    args = parser.parse_args()

    report = convert(args.scene, args.out, args.labeled_only, args.max_frames, args.label_dir)
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
