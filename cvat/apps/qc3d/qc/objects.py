# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
The same object across frames, and its real size.

A box drawn on what the LiDAR sees of an object is often too short: the far side of a car is hidden in
most frames. A rigid object keeps its size, so the frames where it is seen best tell the size of all.

Objects are tracks when the job has them. Otherwise the boxes of consecutive frames are linked: the ego
motion between two frames is estimated from the boxes themselves (rigid 2D transform, robust to moving
objects), then each box is paired with the nearest box of the same class group in the next frame.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from .geometry import Cuboid
from .taxonomy import ClassSpec

# class -> group: a box keeps its object when only its class is wrong between two-wheelers or trucks / buses
GROUPS = {"Truck": "large", "Bus": "large", "Rider": "two", "Motorbike": "two", "Bicycle": "two"}
NOT_RIGID = {"Pedestrian"}  # a walking person changes its box shape: no size reference
LINK_DISTANCE = 1.5  # m between a box and its next-frame box, after ego motion
MIN_FRAMES = 3  # frames an object needs before its size is a reference
SIZE_PERCENTILE = 80  # the best seen frames, not the largest box (a single loose box would win)


def _group(label: str) -> str:
    return GROUPS.get(label, label)


def _rigid(src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares 2D rotation + translation with dst ~ src @ R.T + t."""
    cs, cd = src.mean(axis=0), dst.mean(axis=0)
    u, _, vt = np.linalg.svd((src - cs).T @ (dst - cd))
    r = (u @ vt).T
    if np.linalg.det(r) < 0:
        vt[-1] *= -1
        r = (u @ vt).T
    return r, cd - cs @ r.T


def _pairs(a: np.ndarray, b: np.ndarray, groups_a: list, groups_b: list, gate: float) -> list[tuple[int, int]]:
    """Mutual nearest neighbours of the same group within `gate`."""
    if not len(a) or not len(b):
        return []
    d = np.hypot(a[:, None, 0] - b[None, :, 0], a[:, None, 1] - b[None, :, 1])
    same = np.array([[ga == gb for gb in groups_b] for ga in groups_a])
    d = np.where(same, d, np.inf)
    best_b, best_a = d.argmin(axis=1), d.argmin(axis=0)
    return [(i, int(j)) for i, j in enumerate(best_b) if d[i, j] <= gate and best_a[j] == i]


def ego_motion(a: list[Cuboid], b: list[Cuboid]) -> tuple[np.ndarray, np.ndarray] | None:
    """Transform of frame a's coordinates into frame b's, from the static objects both frames see."""
    pa = np.array([box.position[:2] for box in a], dtype=np.float64)
    pb = np.array([box.position[:2] for box in b], dtype=np.float64)
    ga, gb = [_group(box.label) for box in a], [_group(box.label) for box in b]
    r, t = np.eye(2), np.zeros(2)
    found = False
    for gate in (6.0, 4.0, 3.0, 2.0, 1.5, 1.5):
        pairs = _pairs(pa @ r.T + t, pb, ga, gb, gate)
        if len(pairs) < 3:
            break
        src, dst = pa[[i for i, _ in pairs]], pb[[j for _, j in pairs]]
        r, t = _rigid(src, dst)
        # moving objects disagree with the ego motion: fit again on the pairs that agree
        residual = np.hypot(*(src @ r.T + t - dst).T)
        keep = residual <= max(0.5, 2.5 * float(np.median(residual)))
        if keep.sum() >= 3:
            r, t = _rigid(src[keep], dst[keep])
        found = True
    return (r, t) if found else None


def link_objects(cuboids: list[Cuboid]) -> dict[str, str]:
    """Box uid -> object id: the track, or the chain of linked boxes over consecutive frames."""
    objects = {}
    by_frame = defaultdict(list)
    for box in cuboids:
        if box.track_id is not None:
            objects[box.uid] = f"t{box.track_id}"
        else:
            by_frame[box.frame].append(box)
    parent = {box.uid: box.uid for boxes in by_frame.values() for box in boxes}

    def find(uid: str) -> str:
        while parent[uid] != uid:
            parent[uid] = parent[parent[uid]]
            uid = parent[uid]
        return uid

    frames = sorted(by_frame)
    for f0, f1 in zip(frames, frames[1:]):
        a, b = by_frame[f0], by_frame[f1]
        motion = ego_motion(a, b)
        if motion is None:
            continue
        r, t = motion
        pa = np.array([box.position[:2] for box in a]) @ r.T + t
        pb = np.array([box.position[:2] for box in b])
        for i, j in _pairs(pa, pb, [_group(x.label) for x in a], [_group(x.label) for x in b], LINK_DISTANCE):
            parent[find(a[i].uid)] = find(b[j].uid)
    for uid in parent:
        objects[uid] = f"o{find(uid)}"
    return objects


def reference_sizes(cuboids: list[Cuboid], classes: dict[str, ClassSpec]) -> dict[str, dict]:
    """
    Box uid -> {"size": (length, width, height), "frames": n} of its object, for rigid objects seen in
    MIN_FRAMES frames or more; the size stays within the guideline range of the object's class.
    """
    links = link_objects(cuboids)
    members = defaultdict(list)
    for box in cuboids:
        members[links[box.uid]].append(box)
    reference = {}
    for boxes in members.values():
        labels = [box.label for box in boxes]
        label = max(set(labels), key=labels.count)
        if len({box.frame for box in boxes}) < MIN_FRAMES or label in NOT_RIGID:
            continue
        scales = np.array([box.scale for box in boxes], dtype=np.float64)
        size = [float(np.percentile(scales[:, 0], SIZE_PERCENTILE)), float(np.percentile(scales[:, 1], SIZE_PERCENTILE)),
                float(np.median(scales[:, 2]))]
        spec = classes.get(label)
        if spec:
            size = [min(max(v, lo), hi) for v, lo, hi in zip(size, spec.size_min, spec.size_max)]
        for box in boxes:
            reference[box.uid] = {"size": tuple(size), "frames": len(boxes), "label": label}
    return reference


def too_short(box: Cuboid, size: tuple) -> dict[str, float]:
    """Axes (length, width) on which the box is clearly shorter than its object."""
    short = {}
    for axis, name, minimum in ((0, "length", 0.3), (1, "width", 0.15)):
        if box.scale[axis] < size[axis] - max(minimum, 0.08 * size[axis]):
            short[name] = round(size[axis] - box.scale[axis], 2)
    return short


def extend_to(box: Cuboid, size: tuple, axes=(0, 1)) -> Cuboid:
    """
    Box grown to `size` on `axes` (never shrunk): the face the LiDAR sees stays, the hidden far side
    moves out; a box the sensor sees from the side along an axis grows on both sides.
    """
    c, s = math.cos(box.yaw), math.sin(box.yaw)
    # sensor (origin) in the box frame
    sx = -(box.position[0] * c + box.position[1] * s)
    sy = -(-box.position[0] * s + box.position[1] * c)
    scale, shift = list(box.scale), [0.0, 0.0]
    for axis, sensor in zip((0, 1), (sx, sy)):
        if axis not in axes or size[axis] <= scale[axis]:
            continue
        half = scale[axis] / 2
        if abs(sensor) > half:  # the near face stays where it is
            shift[axis] = -math.copysign(1.0, sensor) * (size[axis] - scale[axis]) / 2
        scale[axis] = float(size[axis])
    dx, dy = shift
    position = (box.position[0] + dx * c - dy * s, box.position[1] + dx * s + dy * c, box.position[2])
    return box.with_(position=tuple(float(v) for v in position), scale=tuple(scale))
