# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Verification of a correction before auto-fix may write it.

Auto-fix changes the mislabeled object in place; it never creates a new one. A correction is written
automatically only when the corrected box itself passes, under the job's guideline, the checks it was
made for:
  - size inside the guideline range of its (new) class                         (R06)
  - bottom on the locally measured ground                                      (R09)
  - tight on the faces the LiDAR sees, not taller than the object               (R10)
  - contains the object points (nothing of the object is cut off)
  - the class agrees with the LiDAR cluster and with the size prior            (R11)
  - does not move onto another labeled object                                   (R02)
  - moves and resizes within the auto-fix limits (a larger change is likely a different object)
A correction that fails any check is left to the reviewer, with the failed checks listed.
"""

from __future__ import annotations

import math

import numpy as np

from .fit import class_from_points, observe, visible_face_gaps
from .geometry import Cuboid, iou_3d
from .taxonomy import resolve_two_wheeler, size_log_likelihood

EPS = 0.01  # m: smaller differences are rounding, not a change


def apply_changes(box: Cuboid, changes: dict) -> Cuboid:
    updates = {name: tuple(float(v) for v in changes[name]) for name in ("position", "rotation", "scale") if name in changes}
    if "label" in changes:
        updates["label"] = changes["label"]
    return box.with_(**updates)


def change_kinds(box: Cuboid, fixed: Cuboid) -> list[str]:
    """Kinds of change (guideline autofix.fix keys) a correction makes."""
    kinds = []
    # horizontal: position x/y, length, width; vertical: z and height (bottom on the ground, top on the object)
    if math.dist(box.position[:2], fixed.position[:2]) > EPS or any(
        abs(a - b) > EPS for a, b in zip(box.scale[:2], fixed.scale[:2])
    ):
        kinds.append("fit")
    if abs(box.position[2] - fixed.position[2]) > EPS or abs(box.scale[2] - fixed.scale[2]) > EPS:
        kinds.append("ground")
    if any(abs(a - b) > 1e-4 for a, b in zip(box.rotation, fixed.rotation)):
        kinds.append("orientation")
    if box.label != fixed.label:
        kinds.append("class")
    return kinds


def verify_fix(
    box: Cuboid, fixed: Cuboid, boxes: list[Cuboid], points: np.ndarray | None, cfg: dict,
    model_class: str | None = None,
) -> list[str]:
    """
    Failed checks of a corrected box (empty: safe to write automatically). `model_class`: the class the
    detection model sees on this object; a new class it confirms is not judged ambiguous by size alone.
    """
    disabled = set(cfg.get("disabled_rules", ()))
    classes = cfg["classes"]
    failed: list[str] = []

    if fixed.label not in cfg["labels"]:
        failed.append(f"class {fixed.label} không có trong schema của job")
    ranges = cfg["size_ranges"].get(fixed.label)
    if ranges is None:
        failed.append(f"guideline không có class {fixed.label}")
    elif "R06" not in disabled:
        out = [
            f"{name} {value:.2f} ngoài [{lo:g}, {hi:g}]"
            for name, value, (lo, hi) in zip(("dài", "rộng", "cao"), fixed.scale, ranges)
            if not lo - 0.005 <= value <= hi + 0.005
        ]
        if out:
            failed.append(f"sau sửa vẫn sai kích thước {fixed.label}: " + "; ".join(out))

    # horizontal move only: lowering a floating box onto the road is checked by the ground test below
    shift = math.dist(box.position[:2], fixed.position[:2])
    if shift > cfg["autofix_max_shift"]:
        failed.append(f"dời {shift:.2f} m, quá giới hạn tự sửa {cfg['autofix_max_shift']:g} m")
    spec = classes.get(fixed.label)
    for axis, (name, before, after) in enumerate(zip(("dài", "rộng", "cao"), box.scale, fixed.scale)):
        change = abs(after - before) / before if before > 0 else 0.0
        if change <= cfg["autofix_max_scale_change"]:
            continue
        # a large resize is safe only back towards a typical size of the class (an inflated box shrunk to
        # the object); a large move away from it means the box was fitted to something else
        towards = spec is not None and abs(after - spec.size_mean[axis]) < abs(before - spec.size_mean[axis]) and (
            abs(after - spec.size_mean[axis]) <= 2 * spec.size_std[axis]
        )
        if not towards:
            failed.append(f"{name} đổi {change:.0%} ({before:.2f}→{after:.2f} m), quá giới hạn tự sửa")

    if "R02" not in disabled:
        for other in boxes:
            if other.uid == box.uid or other.frame != box.frame or math.dist(other.position, fixed.position) > 15:
                continue
            after = iou_3d(fixed, other)
            if after > 0.01 and after > iou_3d(box, other) + 0.01:
                failed.append(f"sau sửa đè lên {other.uid} ({other.label}, IoU {after:.2f})")

    geometry_changed = (
        math.dist(box.position, fixed.position) > EPS or any(abs(a - b) > EPS for a, b in zip(box.scale, fixed.scale))
    )
    if box.label != fixed.label and fixed.label in classes and box.label in classes and "R11" not in disabled:
        new_fits = size_log_likelihood(classes[fixed.label], fixed.scale) >= size_log_likelihood(
            classes[box.label], fixed.scale
        )
        # two-wheelers are told apart by height (rider on board or not), as in R11
        if resolve_two_wheeler(fixed.label if new_fits else box.label, fixed.scale[2], classes) != fixed.label:
            failed.append(f"kích thước sau sửa vẫn giống {box.label} hơn {fixed.label}")
        # the new class must be clearly the best by size among all classes (two-wheelers: by height)
        siblings = {"Rider", "Motorbike"} if fixed.label in ("Rider", "Motorbike") else {fixed.label}
        own = size_log_likelihood(classes[fixed.label], fixed.scale)
        rivals = [(size_log_likelihood(spec, fixed.scale), name) for name, spec in classes.items() if name not in siblings]
        if rivals and model_class != fixed.label:
            best, rival = max(rivals)
            if own - best < cfg["autofix_class_margin"]:
                failed.append(f"class mới chưa chắc chắn: kích thước cũng giống {rival}")

    if points is None:
        if geometry_changed:
            failed.append("không có point cloud để kiểm chứng vị trí / kích thước")
        return failed
    if fixed.distance > cfg["fit_max_distance"]:
        if geometry_changed:
            failed.append(f"xa hơn {cfg['fit_max_distance']:g} m: không đủ điểm để kiểm chứng")
        return failed

    obs = observe(points, fixed, boxes, classes=classes)
    if "R09" not in disabled:
        if obs.ground_z is None:
            if abs(box.position[2] - fixed.position[2]) > EPS:
                failed.append("không đo được mặt đường quanh box")
        else:
            gap = (fixed.position[2] - fixed.scale[2] / 2) - obs.ground_z
            if abs(gap) > cfg["ground_tolerance"]:
                failed.append(f"sau sửa vẫn {'lơ lửng' if gap > 0 else 'chìm'} {abs(gap):.2f} m")

    if len(obs.points) >= cfg["fit_min_points"]:
        local = obs.points
        length, width, height = fixed.scale
        inside = (
            (np.abs(local[:, 0]) <= length / 2 + 0.1)
            & (np.abs(local[:, 1]) <= width / 2 + 0.1)
            & (local[:, 2] <= height / 2 + 0.1)
        )
        coverage = float(inside.mean())
        if coverage < cfg["autofix_min_coverage"]:
            failed.append(f"box sau sửa chỉ chứa {coverage:.0%} điểm của vật")
        if "R10" not in disabled:
            gaps = visible_face_gaps(obs, fixed)
            names = {"length": "dài", "width": "rộng"}
            for name, axis in (("length", 0), ("width", 1)):
                if name in gaps and gaps[name] > max(cfg["face_gap"], cfg["face_gap_ratio"] * fixed.scale[axis]):
                    failed.append(f"sau sửa vẫn hở {names[name]} {gaps[name]:.2f} m")
            if gaps.get("height", 0) > max(cfg["height_gap"], cfg["height_gap_ratio"] * fixed.scale[2]):
                failed.append(f"sau sửa đỉnh box vẫn cao hơn vật {gaps['height']:.2f} m")
        if box.label != fixed.label and "R11" not in disabled:
            other = class_from_points(obs, fixed, classes=classes)
            if other:
                failed.append(f"cụm điểm LiDAR không khớp class {fixed.label} (giống {other})")
    elif geometry_changed and len(obs.points) < 10 and math.dist(box.position[:2], fixed.position[:2]) > EPS:
        failed.append("quá ít điểm LiDAR để kiểm chứng vị trí mới")
    return failed
