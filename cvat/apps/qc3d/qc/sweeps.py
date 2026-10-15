# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Multi-sweep point clouds: past LiDAR sweeps moved into the current frame, with their time lag.
Shared by the training-set builder (utils/qc3d/build_train_set.py) and the QC detector, so the model
sees the same kind of input in training and in QC.
"""

from __future__ import annotations

import numpy as np

DEFAULT_SWEEPS = 4  # current + 3 past sweeps (0.3 s at 10 Hz)
DEFAULT_MIN_RANGE = 20.0  # past sweeps add points only beyond this range: near objects are dense already


def merge_sweeps(current: np.ndarray, past: list[tuple[np.ndarray, np.ndarray, float]],
                 min_range: float = DEFAULT_MIN_RANGE) -> np.ndarray:
    """
    current: (N, >=4) x y z intensity [time]; past: [(points (M, >=4), 4x4 transform into the current
    frame, time lag in s)]. Returns (N + K, 5) float32: x y z intensity time_lag.
    """
    parts = [np.column_stack([current[:, :4], np.zeros(len(current))]).astype(np.float32)]
    for points, transform, lag in past:
        xyz = points[:, :3] @ transform[:3, :3].T + transform[:3, 3]
        keep = np.hypot(xyz[:, 0], xyz[:, 1]) >= min_range
        if keep.any():
            parts.append(np.column_stack([xyz[keep], points[keep, 3], np.full(int(keep.sum()), lag)]).astype(np.float32))
    return np.concatenate(parts, axis=0)
