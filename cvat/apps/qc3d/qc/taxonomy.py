# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Vietnamese traffic class taxonomy with size guidelines and size priors.

Sizes are (length, width, height) in metres. Ranges drive R06, priors drive the class
suggestion of R11. Values are a proposal to be confirmed by QA against the guideline;
PHENIKAA only ships Car / Rider / Pedestrian, so Truck / Bus come from large "Car" boxes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TAXONOMY_VERSION = "vn-2026.10.2"


@dataclass(frozen=True)
class ClassSpec:
    name: str
    name_vi: str
    description: str
    size_min: tuple[float, float, float]
    size_max: tuple[float, float, float]
    size_mean: tuple[float, float, float]
    size_std: tuple[float, float, float]
    points_at_10m: int  # expected LiDAR points of a fully visible object at 10 m
    max_speed: float  # m/s, for ID-switch detection


CLASSES: dict[str, ClassSpec] = {
    spec.name: spec
    for spec in (
        ClassSpec("Car", "Ô tô con", "Sedan, SUV, MPV, bán tải, taxi, van ≤ 9 chỗ",
                  (2.5, 1.4, 1.2), (6.2, 2.3, 2.5), (4.6, 1.9, 1.65), (0.6, 0.18, 0.22), 300, 40.0),
        ClassSpec("Truck", "Xe tải", "Xe tải nhẹ đến nặng, xe ben, đầu kéo, xe chuyên dụng",
                  (4.5, 1.7, 2.0), (13.0, 2.8, 4.3), (7.5, 2.35, 3.0), (2.0, 0.3, 0.55), 450, 30.0),
        ClassSpec("Bus", "Xe khách / xe buýt", "Xe buýt, xe khách ≥ 16 chỗ",
                  (6.5, 2.0, 2.6), (13.0, 2.7, 4.2), (10.5, 2.5, 3.3), (1.6, 0.15, 0.3), 500, 30.0),
        ClassSpec("Rider", "Người đi xe máy / xe đạp", "Xe máy, xe điện, xe đạp kèm người lái và người ngồi sau",
                  (1.1, 0.35, 1.0), (2.6, 1.3, 2.2), (1.9, 0.8, 1.65), (0.25, 0.15, 0.15), 100, 25.0),
        # sizes measured on 359 parked two-wheelers of nuScenes-mini (p5-p95 widened); ~40 cm lower than Rider
        ClassSpec("Motorbike", "Xe máy / xe đạp đỗ", "Xe máy, xe điện, xe đạp đang đỗ, không có người trên xe",
                  (1.3, 0.4, 0.8), (2.8, 1.3, 1.6), (1.95, 0.6, 1.25), (0.27, 0.1, 0.14), 80, 3.0),
        ClassSpec("Pedestrian", "Người đi bộ", "Người đứng hoặc đi bộ, không trên phương tiện",
                  (0.2, 0.2, 0.9), (1.3, 1.3, 2.1), (0.65, 0.65, 1.65), (0.2, 0.2, 0.15), 60, 4.0),
    )
}


def size_log_likelihood(spec: ClassSpec, scale: tuple[float, float, float]) -> float:
    """Gaussian log-likelihood of a box size under the class prior (independent axes)."""
    total = 0.0
    for value, mean, std in zip(scale, spec.size_mean, spec.size_std):
        total += -0.5 * ((value - mean) / std) ** 2 - math.log(std)
    return total


# Rider and Motorbike have the same footprint; only a person on the vehicle makes it taller.
# Medians on nuScenes-mini: Motorbike 1.29 m, Rider 1.71 m.
TWO_WHEELER_SPLIT = 1.25  # below: nobody on it; ambiguous heights default to Rider (more common in traffic)


def resolve_two_wheeler(name: str | None, height: float, classes) -> str | None:
    """Rider vs Motorbike by height (a partly seen rider looks like a parked motorbike otherwise)."""
    if name in ("Rider", "Motorbike") and {"Rider", "Motorbike"} <= set(classes):
        return "Motorbike" if height < TWO_WHEELER_SPLIT else "Rider"
    return name


def best_class_by_size(
    scale: tuple[float, float, float], candidates: list[str], classes: dict[str, ClassSpec] = CLASSES
) -> tuple[str, float]:
    """Most likely class among candidates, and its log-likelihood margin over the runner-up."""
    scored = sorted(
        ((size_log_likelihood(classes[name], scale), name) for name in candidates if name in classes),
        reverse=True,
    )
    if not scored:
        return "", 0.0
    if len(scored) == 1:
        return scored[0][1], math.inf
    return scored[0][1], scored[0][0] - scored[1][0]


def as_dict(classes: dict[str, ClassSpec] = CLASSES, version: str = TAXONOMY_VERSION) -> dict:
    return {
        "version": version,
        "classes": [
            {
                "name": s.name,
                "name_vi": s.name_vi,
                "description": s.description,
                "size_min": s.size_min,
                "size_max": s.size_max,
                "size_mean": s.size_mean,
                "size_std": s.size_std,
                "points_at_10m": s.points_at_10m,
                "max_speed": s.max_speed,
            }
            for s in classes.values()
        ],
    }
