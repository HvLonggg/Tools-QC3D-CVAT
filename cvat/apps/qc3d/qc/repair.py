# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Corrections that use the detection model (QC rule R12 detections):

  - refit: a labeled box with errors gets the model's box of the same object (position, size, heading)
    when that explains the object better than the point-based fit: a box drawn on the wrong spot, of the
    wrong size or class cannot be rebuilt from its own points;
  - create: an object the model sees with no label at all gets a new box (only classes the model is
    reliable for, see rules.MODEL_TRUSTED_CLASSES).

Every candidate keeps the guideline: class sizes from the guideline, bottom on the measured road, heading
kept from the label when the model only differs by 180 deg (its known weakness), no overlap with labels.
"""

from __future__ import annotations

import math

import numpy as np

from .fit import ground_plane, local_ground
from .geometry import Cuboid, count_points, iou_3d, wrap_angle
from .taxonomy import best_class_by_size, resolve_two_wheeler

MIN_SCORE = 0.4  # a detection this confident may rebuild a labeled box


def det_cuboid(detection, frame: int, uid: str = "m", label: str | None = None) -> Cuboid:
    values, name, _score = detection
    return Cuboid(uid, frame, label or name, tuple(float(v) for v in values[:3]), (0.0, 0.0, float(values[6])),
                  tuple(float(v) for v in values[3:6]))


def match_detections(boxes: list[Cuboid], detections: list) -> dict[str, tuple]:
    """Labeled box uid -> the detection of the same object (each detection used once, best pairs first)."""
    pairs = []
    for b_index, box in enumerate(boxes):
        reach = max(1.5, 0.5 * box.scale[0])
        for d_index, detection in enumerate(detections):
            values, _name, score = detection
            if score < MIN_SCORE:
                continue
            distance = math.dist(box.position[:2], values[:2])
            if distance > max(reach, 0.5 * values[3]):
                continue
            overlap = iou_3d(box.with_(position=(*box.position[:2], values[2])), det_cuboid(detection, box.frame))
            pairs.append((-(overlap + score - 0.2 * distance), b_index, d_index))
    matched, used_boxes, used_dets = {}, set(), set()
    for _, b_index, d_index in sorted(pairs):
        if b_index in used_boxes or d_index in used_dets:
            continue
        used_boxes.add(b_index)
        used_dets.add(d_index)
        matched[boxes[b_index].uid] = detections[d_index]
    return matched


def guideline_class(box_label: str, detection, cfg: dict, trusted) -> str:
    """Class for the model's box: the model's when it is reliable for it, else what the guideline sizes say."""
    values, name, _score = detection
    classes = cfg["classes"]
    labels = [c for c in cfg["labels"] if c in classes]
    if name in trusted and name in labels:
        label = name
    elif name == box_label:
        label = name
    else:
        ranges = cfg["size_ranges"].get(box_label)
        fits = ranges and all(lo <= v <= hi for v, (lo, hi) in zip(values[3:6], ranges))
        label = box_label if fits else (best_class_by_size(tuple(values[3:6]), labels, classes)[0] or box_label)
    return resolve_two_wheeler(label, float(values[5]), classes) or label


def _same_object(a, b) -> bool:
    """The centre of the smaller detection lies in the core of the larger one's footprint."""
    small, large = (a, b) if a[3] * a[4] <= b[3] * b[4] else (b, a)
    c, s = math.cos(large[6]), math.sin(large[6])
    dx, dy = small[0] - large[0], small[1] - large[1]
    along, across = dx * c + dy * s, -dx * s + dy * c
    return abs(along) <= 0.4 * large[3] and abs(across) <= 0.4 * large[4]


def dedupe_detections(detections: list) -> list:
    """
    One box per object: the model unsure between two classes (a rider seen as Pedestrian and TwoWheeler,
    a van as Car and Truck) puts two boxes on it; the less confident one is dropped.
    """
    order = sorted(range(len(detections)), key=lambda i: -detections[i][2])
    kept = []
    for i in order:
        if not any(_same_object(detections[i][0], detections[k][0]) for k in kept):
            kept.append(i)
    return [detections[i] for i in sorted(kept)]


def on_ground(box: Cuboid, points: np.ndarray | None, cfg: dict) -> Cuboid:
    """Bottom of the box on the locally measured road, where the road around it is a plane."""
    if points is None:
        return box
    ground, flat = ground_plane(points, box)
    if ground is None or not flat:
        return box
    bottom = box.position[2] - box.scale[2] / 2
    if abs(bottom - ground) <= 0.05:
        return box
    return box.with_(position=(box.position[0], box.position[1], ground + box.scale[2] / 2))


def model_refit(box: Cuboid, detection, points: np.ndarray | None, cfg: dict, trusted) -> Cuboid:
    values = detection[0]
    yaw = float(values[6])
    # the model mixes up front and back more than the size of the box: keep the label's heading then
    if abs(wrap_angle(yaw + math.pi - box.yaw)) < abs(wrap_angle(yaw - box.yaw)):
        yaw = wrap_angle(yaw + math.pi)
    fitted = box.with_(
        label=guideline_class(box.label, detection, cfg, trusted),
        position=tuple(float(v) for v in values[:3]),
        rotation=(0.0, 0.0, wrap_angle(yaw)),
        scale=tuple(float(v) for v in values[3:6]),
    )
    return on_ground(fitted, points, cfg)


def new_box(detection, frame: int, uid: str, points: np.ndarray | None, cfg: dict) -> Cuboid:
    return on_ground(det_cuboid(detection, frame, uid), points, cfg)


def verify_create(box: Cuboid, boxes: list[Cuboid], points: np.ndarray | None, cfg: dict) -> list[str]:
    """Failed checks of a new box (empty: safe to add automatically)."""
    failed = []
    if box.label not in cfg["labels"]:
        failed.append(f"schema của job không có class {box.label}")
    ranges = cfg["size_ranges"].get(box.label)
    if ranges:
        out = [f"{n} {v:.2f} ngoài [{lo:g}, {hi:g}]"
               for n, v, (lo, hi) in zip(("dài", "rộng", "cao"), box.scale, ranges) if not lo - 0.005 <= v <= hi + 0.005]
        if out:
            failed.append(f"kích thước model ngoài guideline {box.label}: " + "; ".join(out))
    for other in boxes:
        if other.frame == box.frame and math.dist(other.position[:2], box.position[:2]) < 10:
            overlap = iou_3d(box, other)
            if overlap > 0.05:
                failed.append(f"đè lên {other.uid} ({other.label}, IoU {overlap:.2f})")
    if points is None:
        failed.append("không có point cloud để kiểm chứng")
    else:
        inside = count_points(points, box, ground_clearance=cfg["ground_clearance"])
        if inside < 10:
            failed.append(f"chỉ {inside} điểm LiDAR trong box")
        ground = local_ground(points, box)
        if ground is not None and abs(box.position[2] - box.scale[2] / 2 - ground) > cfg["ground_tolerance"]:
            failed.append("không đặt được đáy box lên mặt đường")
    return failed


def create_shape(box: Cuboid, score: float) -> dict:
    """What a create proposal stores: enough to write the CVAT cuboid."""
    return {
        "label": box.label,
        "frame": box.frame,
        "points": [round(float(v), 4) for v in (*box.position, *box.rotation, *box.scale)],
        "score": round(float(score), 3),
    }
