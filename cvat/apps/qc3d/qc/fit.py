# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Point-based box analysis and correction proposals.

A LiDAR sees only the faces of an object that look towards the sensor, so the observed
point extent is usually shorter than the object. Corrections therefore:
  - anchor the box to the faces that are visible from the sensor,
  - keep the size unless the points prove it is larger (or the box is proven too loose),
  - put the box bottom on the locally measured ground.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .geometry import Cuboid, to_local
from .taxonomy import CLASSES


@dataclass
class BoxObservation:
    ground_z: float | None  # local ground height (sensor frame)
    points: np.ndarray  # object points in the box frame (N, 3)
    sensor_local: np.ndarray  # sensor origin in the box frame


def local_ground(points: np.ndarray, box: Cuboid, *, radius: float = 1.5) -> float | None:
    """Ground height around the footprint: low percentile of points near the box bottom."""
    local = to_local(points, box)
    length, width, height = box.scale
    bottom = -height / 2
    near = (
        (np.abs(local[:, 0]) <= length / 2 + radius)
        & (np.abs(local[:, 1]) <= width / 2 + radius)
        & (local[:, 2] >= bottom - 1.5)
        & (local[:, 2] <= bottom + 1.0)
    )
    if near.sum() < 15:
        return None
    return float(np.percentile(local[near, 2], 10)) + box.position[2]


def _bev_clusters(local: np.ndarray, cell: float = 0.3) -> list[np.ndarray]:
    """Connected components of occupied BEV grid cells (8-neighbourhood); returns point index arrays."""
    if len(local) == 0:
        return []
    keys = np.floor(local[:, :2] / cell).astype(np.int64)
    cells, inverse = np.unique(keys, axis=0, return_inverse=True)
    inverse = inverse.reshape(-1)
    index = {tuple(c): i for i, c in enumerate(cells)}
    parent = list(range(len(cells)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (cx, cy) in enumerate(cells):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                j = index.get((cx + dx, cy + dy))
                if j is not None:
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj
    roots = np.array([find(i) for i in range(len(cells))])[inverse]
    return [np.flatnonzero(roots == r) for r in np.unique(roots)]


def _select_cluster(local: np.ndarray, box: Cuboid) -> np.ndarray:
    """The cluster that can be this object: fits the class size, closest to the box centre."""
    spec = CLASSES.get(box.label)
    max_l = max(box.scale[0], spec.size_max[0] if spec else 0) + 0.3
    max_w = max(box.scale[1], spec.size_max[1] if spec else 0) + 0.3
    best, best_score = None, None
    for idx in _bev_clusters(local):
        if len(idx) < 10:
            continue
        pts = local[idx]
        extent = pts.max(axis=0) - pts.min(axis=0)
        if extent[0] > max_l or extent[1] > max_w:
            continue  # wall, vegetation or several objects merged
        center = (pts.max(axis=0) + pts.min(axis=0)) / 2
        score = math.hypot(center[0], center[1]) - 0.002 * len(idx)
        if best_score is None or score < best_score:
            best, best_score = idx, score
    return local[best] if best is not None else local[:0]


def observe(points: np.ndarray, box: Cuboid, others: list[Cuboid], *, margin: float = 0.4) -> BoxObservation:
    ground = local_ground(points, box)
    local = to_local(points, box)
    length, width, height = box.scale
    floor = (ground - box.position[2] if ground is not None else -height / 2) + 0.15
    mask = (
        (np.abs(local[:, 0]) <= length / 2 + margin)
        & (np.abs(local[:, 1]) <= width / 2 + margin)
        & (local[:, 2] >= floor)
        & (local[:, 2] <= height / 2 + 0.3)
    )
    candidates = points[mask]
    # points that belong to another labeled object are not ours
    for other in others:
        if other.uid == box.uid or other.frame != box.frame or math.dist(other.position, box.position) > 15:
            continue
        o = to_local(candidates, other)
        inside = (
            (np.abs(o[:, 0]) <= other.scale[0] / 2)
            & (np.abs(o[:, 1]) <= other.scale[1] / 2)
            & (np.abs(o[:, 2]) <= other.scale[2] / 2)
        )
        candidates = candidates[~inside]
    sensor = to_local(np.zeros((1, 3), dtype=np.float32), box)[0]
    return BoxObservation(ground, _select_cluster(to_local(candidates, box), box), sensor)


def visible_face_gaps(obs: BoxObservation, box: Cuboid) -> dict[str, float]:
    """Gap between each sensor-facing box face and the nearest object points (x: length, y: width)."""
    gaps = {}
    if len(obs.points) == 0:
        return gaps
    for axis, name in ((0, "length"), (1, "width")):
        half = box.scale[axis] / 2
        side = math.copysign(1.0, obs.sensor_local[axis])
        if abs(obs.sensor_local[axis]) <= half:
            continue  # sensor faces neither side along this axis
        nearest = obs.points[:, axis].max() if side > 0 else obs.points[:, axis].min()
        gaps[name] = abs(side * half - nearest)
    top = obs.points[:, 2].max()
    gaps["height"] = max(0.0, box.scale[2] / 2 - top)
    return gaps


def propose_fit(box: Cuboid, obs: BoxObservation, *, shrink: bool = False) -> Cuboid | None:
    """Box re-anchored on visible faces and the ground. Size kept unless the points prove otherwise."""
    if len(obs.points) < 10:
        return None
    lo, hi = obs.points.min(axis=0), obs.points.max(axis=0)
    extent = hi - lo
    spec = CLASSES.get(box.label)
    size = list(box.scale)
    center_local = [0.0, 0.0, 0.0]

    for axis in (0, 1):
        if extent[axis] > size[axis]:
            size[axis] = float(extent[axis])
        elif shrink and spec:
            # too loose: shrink towards the class mean, never below what the points show
            size[axis] = float(max(extent[axis] + 0.1, min(size[axis], spec.size_mean[axis])))
        s = obs.sensor_local[axis]
        if s > hi[axis]:
            center_local[axis] = float(hi[axis] - size[axis] / 2)  # visible face: +axis side
        elif s < lo[axis]:
            center_local[axis] = float(lo[axis] + size[axis] / 2)  # visible face: -axis side
        else:
            center_local[axis] = float((lo[axis] + hi[axis]) / 2)

    ground_local = (obs.ground_z - box.position[2]) if obs.ground_z is not None else lo[2]
    if shrink or hi[2] - ground_local > size[2]:
        size[2] = float(max(hi[2] - ground_local + 0.05, spec.size_min[2] if spec else 0.5))
    center_local[2] = float(ground_local + size[2] / 2)

    c, s = math.cos(box.yaw), math.sin(box.yaw)
    dx, dy, dz = center_local
    position = (box.position[0] + dx * c - dy * s, box.position[1] + dx * s + dy * c, box.position[2] + dz)
    return box.with_(position=tuple(float(v) for v in position), scale=tuple(float(v) for v in size))


def class_from_points(obs: BoxObservation, box: Cuboid, *, min_points: int = 150) -> str | None:
    """
    Class implied by a well observed point cluster when it cannot be the labeled class:
    the cluster is smaller than the labeled class minimum on 2+ axes (occlusion only hides
    part of an object, it does not shrink a truck to a motorbike).
    """
    spec = CLASSES.get(box.label)
    if spec is None or len(obs.points) < min_points:
        return None
    extent = obs.points.max(axis=0) - obs.points.min(axis=0)
    extent[2] = obs.points[:, 2].max() - ((obs.ground_z - box.position[2]) if obs.ground_z is not None else obs.points[:, 2].min())
    if sum(e < m * 0.6 for e, m in zip(extent, spec.size_min)) < 2:
        return None
    best = None
    for name, candidate in CLASSES.items():
        fits = all(e <= hi + 0.3 for e, hi in zip(extent, candidate.size_max))
        if fits and name != box.label:
            score = sum(abs(e - m) / s for e, m, s in zip(extent, candidate.size_mean, candidate.size_std))
            if best is None or score < best[0]:
                best = (score, name)
    return best[1] if best else None


def propose_object_fix(
    box: Cuboid,
    obs: BoxObservation,
    *,
    label: str | None = None,
    fix_xy: bool = False,
    fix_height: bool = False,
    fix_ground: bool = False,
) -> Cuboid | None:
    """
    One consistent, minimal correction for an object: only what the rules found wrong changes.
      label       -> new class; size axes invalid for that class are re-estimated
      fix_xy      -> re-anchor on the faces the LiDAR sees (shifted / loose box)
      fix_height  -> height from ground to the highest point
      fix_ground  -> bottom on the local ground
    """
    if len(obs.points) < 10:
        return None
    label = label or box.label
    spec = CLASSES.get(label)
    lo, hi = obs.points.min(axis=0), obs.points.max(axis=0)
    extent = hi - lo
    ground_local = (obs.ground_z - box.position[2]) if obs.ground_z is not None else lo[2]
    top_extent = hi[2] - ground_local

    size = list(box.scale)
    for axis in (0, 1):
        if spec and not (spec.size_min[axis] <= size[axis] <= spec.size_max[axis]):
            size[axis] = float(min(max(extent[axis], spec.size_mean[axis]), spec.size_max[axis]))
            fix_xy = True
        elif fix_xy and spec and extent[axis] < size[axis]:
            # loose box: shrink towards the class mean, never below what the points show
            size[axis] = float(max(extent[axis] + 0.1, min(size[axis], spec.size_mean[axis])))
        if extent[axis] > size[axis]:
            size[axis] = float(extent[axis])
            fix_xy = True
    if fix_height or (spec and not (spec.size_min[2] <= size[2] <= spec.size_max[2])):
        size[2] = float(max(top_extent + 0.05, spec.size_min[2] if spec else 0.5))
        fix_ground = True

    center_local = [0.0, 0.0, 0.0]
    if fix_xy:
        for axis in (0, 1):
            s = obs.sensor_local[axis]
            if s > hi[axis]:
                center_local[axis] = float(hi[axis] - size[axis] / 2)
            elif s < lo[axis]:
                center_local[axis] = float(lo[axis] + size[axis] / 2)
            else:
                center_local[axis] = float((lo[axis] + hi[axis]) / 2)
    if fix_ground or fix_xy:
        center_local[2] = float(ground_local + size[2] / 2)

    c, s = math.cos(box.yaw), math.sin(box.yaw)
    dx, dy, dz = center_local
    position = (box.position[0] + dx * c - dy * s, box.position[1] + dx * s + dy * c, box.position[2] + dz)
    return box.with_(
        label=label, position=tuple(float(v) for v in position), scale=tuple(float(v) for v in size)
    )


def describe_change(before: Cuboid, after: Cuboid) -> str:
    parts = []
    shift = math.dist(before.position[:2], after.position[:2])
    if shift >= 0.05:
        parts.append(f"dời tâm {shift:.2f} m")
    dz = after.position[2] - before.position[2]
    if abs(dz) >= 0.05:
        parts.append(f"z {dz:+.2f} m")
    for name, b, a in zip(("dài", "rộng", "cao"), before.scale, after.scale):
        if abs(a - b) >= 0.05:
            parts.append(f"{name} {b:.2f}→{a:.2f}")
    if before.label != after.label:
        parts.append(f"class {before.label}→{after.label}")
    return ", ".join(parts) or "thay đổi nhỏ"
