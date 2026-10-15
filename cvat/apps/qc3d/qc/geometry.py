# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""Pure numpy geometry for 3D cuboids (no Django imports: unit-testable standalone)."""

from __future__ import annotations

import math
import weakref
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class Cuboid:
    """A cuboid in the LiDAR frame. CVAT stores rotation as Euler XYZ (rad)."""

    uid: str  # stable id: "s:<shape_id>" or "t:<track_id>:<frame>"
    frame: int
    label: str
    position: tuple[float, float, float]
    rotation: tuple[float, float, float]
    scale: tuple[float, float, float]  # length, width, height
    track_id: int | None = None
    source_kind: str = "shape"  # "shape" | "track"
    source_id: int | None = None
    attributes: dict = field(default_factory=dict, compare=False)

    @property
    def yaw(self) -> float:
        return self.rotation[2]

    @property
    def distance(self) -> float:
        return math.hypot(self.position[0], self.position[1])

    def with_(self, **changes) -> "Cuboid":
        return replace(self, **changes)


def wrap_angle(angle: float) -> float:
    """Wrap to (-pi, pi]."""
    wrapped = math.fmod(angle + math.pi, 2 * math.pi)
    if wrapped <= 0:
        wrapped += 2 * math.pi
    return wrapped - math.pi


_INDEX: dict[int, tuple] = {}  # id(points) -> (weak ref, order by grid cell, sorted cell keys, grid origin)
INDEX_CELL = 4.0  # m


def crop(points: np.ndarray, x: float, y: float, radius: float) -> np.ndarray:
    """
    Points within `radius` (square) of (x, y). Each box needs only the points around it: rotating the whole
    cloud (~260k points) for every box made a QC run take minutes. The cloud is indexed once on a 4 m grid
    (kept while the cloud array lives); a query reads the few cells around the box.
    """
    if len(points) < 50000:  # a crop of a crop: filtering directly is cheaper than an index
        keep = (np.abs(points[:, 0] - x) <= radius) & (np.abs(points[:, 1] - y) <= radius)
        return points if keep.all() else points[keep]
    key = id(points)
    hit = _INDEX.get(key)
    if hit is None or hit[0]() is not points:
        finite = np.isfinite(points[:, 0]) & np.isfinite(points[:, 1])
        x0, y0 = float(points[finite, 0].min()), float(points[finite, 1].min())
        cells = np.floor((np.nan_to_num(points[:, :2], nan=-1e9) - (x0, y0)) / INDEX_CELL).astype(np.int64)
        ny = int(cells[finite, 1].max()) + 1
        keys = np.where(finite, cells[:, 0] * ny + cells[:, 1], -1)
        order = np.argsort(keys, kind="stable")
        hit = (weakref.ref(points, lambda _ref, k=key: _INDEX.pop(k, None)), order, keys[order], (x0, y0, ny))
        _INDEX[key] = hit
    _, order, keys, (x0, y0, ny) = hit
    ix0, ix1 = (int(math.floor((v - x0) / INDEX_CELL)) for v in (x - radius, x + radius))
    iy0, iy1 = (min(max(int(math.floor((v - y0) / INDEX_CELL)), 0), ny - 1) for v in (y - radius, y + radius))
    parts = []
    for ix in range(max(ix0, 0), ix1 + 1):
        lo, hi = np.searchsorted(keys, [ix * ny + iy0, ix * ny + iy1 + 1])
        if hi > lo:
            parts.append(order[lo:hi])
    if not parts:
        return points[:0]
    near = points[np.concatenate(parts)]
    return near[(np.abs(near[:, 0] - x) <= radius) & (np.abs(near[:, 1] - y) <= radius)]


def box_crop(points: np.ndarray, box: "Cuboid", margin: float) -> np.ndarray:
    return crop(points, box.position[0], box.position[1], math.hypot(box.scale[0], box.scale[1]) / 2 + margin)


def to_local(points: np.ndarray, box: Cuboid) -> np.ndarray:
    """Points (N,>=3) expressed in the box frame (yaw only; roll/pitch are checked by R04)."""
    d = points[:, :3] - np.asarray(box.position, dtype=np.float32)
    c, s = math.cos(-box.yaw), math.sin(-box.yaw)
    return np.column_stack([d[:, 0] * c - d[:, 1] * s, d[:, 0] * s + d[:, 1] * c, d[:, 2]])


def count_points(
    points: np.ndarray, box: Cuboid, *, margin: float = 0.0, ground_clearance: float = 0.0
) -> int:
    """Points inside the box grown horizontally by `margin`, ignoring the lowest `ground_clearance` m."""
    local = to_local(box_crop(points, box, margin + 0.5), box)
    length, width, height = box.scale
    mask = (
        (np.abs(local[:, 0]) <= length / 2 + margin)
        & (np.abs(local[:, 1]) <= width / 2 + margin)
        & (local[:, 2] >= -height / 2 + ground_clearance)
        & (local[:, 2] <= height / 2)
    )
    return int(mask.sum())


def bev_corners(box: Cuboid) -> np.ndarray:
    length, width, _ = box.scale
    c, s = math.cos(box.yaw), math.sin(box.yaw)
    # counter-clockwise order: required by the polygon clipping in iou_3d
    half = np.array([[1, 1], [-1, 1], [-1, -1], [1, -1]]) * [length / 2, width / 2]
    rot = np.array([[c, -s], [s, c]])
    return half @ rot.T + np.asarray(box.position[:2])


def _clip(subject: list, a: np.ndarray, b: np.ndarray) -> list:
    """Sutherland-Hodgman: keep the part of `subject` left of edge a->b."""

    def side(p):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

    def intersect(p, q, sp, sq):
        # interpolate by the signed distances (opposite signs): stable when the edges are almost
        # parallel, where a line-line intersection divides by ~0 (boxes with the same yaw)
        t = sp / (sp - sq)
        return p + (q - p) * t

    output = []
    for i, current in enumerate(subject):
        previous = subject[i - 1]
        sc, sp = side(current), side(previous)
        if sc >= 0:
            if sp < 0:
                output.append(intersect(previous, current, sp, sc))
            output.append(current)
        elif sp >= 0:
            output.append(intersect(previous, current, sp, sc))
    return output


def _area(polygon: list) -> float:
    if len(polygon) < 3:
        return 0.0
    p = np.asarray(polygon)
    return 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1)))


def iou_3d(a: Cuboid, b: Cuboid) -> float:
    """IoU of two z-rotated cuboids (BEV polygon intersection x height overlap)."""
    if math.hypot(a.position[0] - b.position[0], a.position[1] - b.position[1]) > (
        math.hypot(a.scale[0], a.scale[1]) + math.hypot(b.scale[0], b.scale[1])
    ) / 2:
        return 0.0
    clip_poly = bev_corners(b)
    polygon = list(bev_corners(a))
    for i in range(4):
        polygon = _clip(polygon, clip_poly[i - 1], clip_poly[i])
        if not polygon:
            return 0.0
    z_overlap = max(
        0.0,
        min(a.position[2] + a.scale[2] / 2, b.position[2] + b.scale[2] / 2)
        - max(a.position[2] - a.scale[2] / 2, b.position[2] - b.scale[2] / 2),
    )
    inter = _area(polygon) * z_overlap
    vol_a = a.scale[0] * a.scale[1] * a.scale[2]
    vol_b = b.scale[0] * b.scale[1] * b.scale[2]
    union = vol_a + vol_b - inter
    return inter / union if union > 0 else 0.0


def read_pcd(path: str | Path, *, with_intensity: bool = False) -> np.ndarray:
    """
    Read x, y, z (and intensity on the 0-255 scale, 0 when the file has none) from an ascii or binary
    PCD file (the formats CVAT stores).
    """
    with open(path, "rb") as f:
        header = {}
        while True:
            line = f.readline()
            if not line:
                raise ValueError(f"{path}: unexpected end of PCD header")
            text = line.decode("ascii", errors="replace").strip()
            if not text or text.startswith("#"):
                continue
            key, _, value = text.partition(" ")
            header[key.upper()] = value
            if key.upper() == "DATA":
                break
        fields = header["FIELDS"].split()
        sizes = [int(v) for v in header["SIZE"].split()]
        types = header["TYPE"].split()
        counts = [int(v) for v in header.get("COUNT", " ".join("1" * len(fields))).split()]
        n_points = int(header["POINTS"])
        data_kind = header["DATA"].lower()
        columns = [fields.index(name) for name in ("x", "y", "z")]
        intensity_field = next((name for name in ("intensity", "i") if name in fields), None)

        def with_extra(xyz: np.ndarray, intensity) -> np.ndarray:
            if not with_intensity:
                return xyz
            values = np.zeros(len(xyz), np.float32) if intensity is None else np.asarray(intensity, np.float32)
            if values.size and values.max() > 255:
                values = values * (255.0 / 65535.0)
            return np.column_stack([xyz, values]).astype(np.float32)

        if data_kind == "ascii":
            table = np.loadtxt(f, dtype=np.float32, max_rows=n_points, ndmin=2)
            return with_extra(table[:, columns], table[:, fields.index(intensity_field)] if intensity_field else None)
        if data_kind != "binary":
            raise ValueError(f"{path}: unsupported PCD DATA '{data_kind}'")

        kinds = {"F": "f", "I": "i", "U": "u"}
        dtype = np.dtype(
            [
                (name, f"<{kinds[t]}{size}", (count,) if count > 1 else ())
                for name, size, t, count in zip(fields, sizes, types, counts)
            ]
        )
        records = np.frombuffer(f.read(dtype.itemsize * n_points), dtype=dtype, count=n_points)
        xyz = np.column_stack([records[name].astype(np.float32) for name in ("x", "y", "z")])
        return with_extra(xyz, records[intensity_field] if intensity_field else None)
