# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Controlled error injection ("set A"): corrupt clean cuboids with known errors so that
QC recall can be measured per error type. Deterministic for a given seed.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from .geometry import Cuboid, count_points

# error type -> rules expected to catch it
EXPECTED_RULES = {
    "shift": {"R05", "R10"},
    "oversize": {"R06"},
    "tilt": {"R04"},
    "duplicate": {"R02"},
    "invalid": {"R01"},
    "unknown_label": {"R03"},
    "loose": {"R10"},
    "float": {"R09"},
    "sink": {"R09"},
    "wrong_class": {"R11"},
}

# wrong class swaps that a size check can see (VN traffic)
CLASS_SWAPS = {"Rider": "Pedestrian", "Pedestrian": "Rider", "Car": "Rider"}


@dataclass(frozen=True)
class InjectedError:
    uid: str
    kind: str
    detail: str


def _eligible_for_shift(box: Cuboid, points: np.ndarray | None) -> bool:
    # a shift is only observable when the object has LiDAR support
    return points is not None and box.distance < 40 and count_points(points, box, ground_clearance=0.2) >= 50


def inject(
    cuboids: list[Cuboid],
    point_clouds: dict[int, np.ndarray],
    *,
    per_kind: int = 5,
    seed: int = 0,
) -> tuple[list[Cuboid], list[InjectedError]]:
    rng = random.Random(seed)
    boxes = list(cuboids)
    errors: list[InjectedError] = []
    used: set[str] = set()

    def pick(candidates: list[int], count: int) -> list[int]:
        candidates = [i for i in candidates if boxes[i].uid not in used]
        chosen = rng.sample(candidates, min(count, len(candidates)))
        used.update(boxes[i].uid for i in chosen)
        return chosen

    all_idx = list(range(len(boxes)))

    shift_idx = [i for i in all_idx if _eligible_for_shift(boxes[i], point_clouds.get(boxes[i].frame))]
    for i in pick(shift_idx, per_kind):
        b = boxes[i]
        # move across the width by width + 0.3..1.0 m so the box leaves its points
        offset = b.scale[1] + rng.uniform(0.3, 1.0)
        side = rng.choice([-1, 1])
        dx, dy = -math.sin(b.yaw) * offset * side, math.cos(b.yaw) * offset * side
        boxes[i] = b.with_(position=(b.position[0] + dx, b.position[1] + dy, b.position[2]))
        errors.append(InjectedError(b.uid, "shift", f"dịch {offset:.2f} m"))

    for i in pick(all_idx, per_kind):
        b = boxes[i]
        factor = rng.uniform(1.8, 2.5)
        boxes[i] = b.with_(scale=(b.scale[0] * factor, b.scale[1] * factor, b.scale[2]))
        errors.append(InjectedError(b.uid, "oversize", f"phóng ×{factor:.2f}"))

    for i in pick(all_idx, per_kind):
        b = boxes[i]
        roll = math.radians(rng.uniform(20, 40)) * rng.choice([-1, 1])
        boxes[i] = b.with_(rotation=(roll, b.rotation[1], b.rotation[2]))
        errors.append(InjectedError(b.uid, "tilt", f"roll {math.degrees(roll):.0f}°"))

    for i in pick(all_idx, per_kind):
        b = boxes[i]
        copy = b.with_(uid=f"{b.uid}:dup")
        boxes.append(copy)
        errors.append(InjectedError(copy.uid, "duplicate", f"bản sao của {b.uid}"))

    for i in pick(all_idx, per_kind):
        b = boxes[i]
        boxes[i] = b.with_(scale=(b.scale[0], 0.0, b.scale[2]))
        errors.append(InjectedError(b.uid, "invalid", "chiều rộng = 0"))

    for i in pick(all_idx, per_kind):
        b = boxes[i]
        boxes[i] = b.with_(label="xe_may")
        errors.append(InjectedError(b.uid, "unknown_label", f"{b.label} → xe_may"))

    # loose: grow the box towards the sensor by 0.6-1.0 m (stays inside the class size range)
    supported = [i for i in all_idx if _eligible_for_shift(boxes[i], point_clouds.get(boxes[i].frame))]
    for i in pick(supported, per_kind):
        b = boxes[i]
        grow = rng.uniform(0.6, 1.0)
        to_sensor = -np.asarray(b.position[:2]) / max(b.distance, 1e-6)
        boxes[i] = b.with_(
            position=(b.position[0] + to_sensor[0] * grow / 2, b.position[1] + to_sensor[1] * grow / 2, b.position[2]),
            scale=(b.scale[0] + grow * abs(math.cos(b.yaw - math.atan2(to_sensor[1], to_sensor[0]))),
                   b.scale[1] + grow * abs(math.sin(b.yaw - math.atan2(to_sensor[1], to_sensor[0]))),
                   b.scale[2]),
        )
        errors.append(InjectedError(b.uid, "loose", f"nới {grow:.2f} m về phía cảm biến"))

    for kind, sign in (("float", 1), ("sink", -1)):
        for i in pick(supported, per_kind):
            b = boxes[i]
            dz = rng.uniform(0.5, 1.0) * sign
            boxes[i] = b.with_(position=(b.position[0], b.position[1], b.position[2] + dz))
            errors.append(InjectedError(b.uid, kind, f"z {dz:+.2f} m"))

    swappable = [i for i in all_idx if boxes[i].label in CLASS_SWAPS]
    for i in pick(swappable, per_kind):
        b = boxes[i]
        boxes[i] = b.with_(label=CLASS_SWAPS[b.label])
        errors.append(InjectedError(b.uid, "wrong_class", f"{b.label} → {CLASS_SWAPS[b.label]}"))

    return boxes, errors
