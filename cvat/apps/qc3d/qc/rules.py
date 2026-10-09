# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
QC rules R01-R08 for 3D cuboids. Pure python + numpy (no Django), deterministic.

Each rule returns a RuleResult: issues found + coverage ("checked" / "not_checked" with a reason).
Missing inputs never mean "passed": a rule that cannot run reports not_checked.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .fit import class_from_points, describe_change, observe, propose_fit, propose_object_fix, visible_face_gaps
from .geometry import Cuboid, count_points, iou_3d, wrap_angle
from .taxonomy import CLASSES, best_class_by_size, size_log_likelihood

RULES_VERSION = "2026.10.4"

BLOCKER, MAJOR, MINOR = "blocker", "major", "minor"

DEFAULT_CONFIG = {
    "labels": list(CLASSES),
    # length, width, height ranges (m) per class: from the VN taxonomy (qc/taxonomy.py)
    "size_ranges": {name: list(zip(s.size_min, s.size_max)) for name, s in CLASSES.items()},
    "track_size_tolerance": 0.15,
    # R02
    "duplicate_center_eps": 0.05,
    "duplicate_size_eps": 0.05,
    "overlap_iou": 0.3,
    # R04
    "max_roll_pitch_deg": 15.0,
    # R05: minimum points = max(min_points, base * min(4, (10 / distance)^2))
    "min_points": 3,
    "points_at_10m": {name: s.points_at_10m for name, s in CLASSES.items()},
    "far_distance": 60.0,  # beyond this a low-point warning is minor
    "ground_clearance": 0.2,
    "ring_margin": 0.5,
    "ring_ratio": 0.5,  # points in the ring around the box > ratio * points inside -> box shifted
    "ring_min_points": 30,
    # R07 / R08 (need ego poses)
    "jump_min": 0.5,
    "jump_mad_factor": 6.0,
    "max_speed": {name: s.max_speed for name, s in CLASSES.items()},
    # R09 ground contact
    "ground_tolerance": 0.3,
    "ground_major": 0.5,
    # R10 fit: only judged where the LiDAR sees the object well
    "fit_max_distance": 40.0,
    "fit_min_points": 60,
    "face_gap": 0.35,  # gap between a sensor-facing face and the object points
    "face_gap_ratio": 0.1,
    "height_gap": 0.4,
    "height_gap_ratio": 0.25,
    # R11 class by size
    "class_margin": 4.0,  # log-likelihood margin of the size prior
}


@dataclass
class Issue:
    rule: str
    severity: str
    uid: str
    frame: int
    message: str
    evidence: dict = field(default_factory=dict)
    # {"kind": "update", "changes": {...}, "summary": "..."} | {"kind": "delete", "summary": "..."}
    proposal: dict | None = None


def update_proposal(before: Cuboid, after: Cuboid) -> dict:
    changes = {}
    for name in ("position", "rotation", "scale"):
        if getattr(before, name) != getattr(after, name):
            changes[name] = [round(float(v), 4) for v in getattr(after, name)]
    if before.label != after.label:
        changes["label"] = after.label
    return {"kind": "update", "changes": changes, "summary": describe_change(before, after)}


@dataclass
class RuleResult:
    rule: str
    status: str  # "checked" | "not_checked"
    issues: list[Issue] = field(default_factory=list)
    reason: str = ""


@dataclass
class QcContext:
    cuboids: list[Cuboid]
    # frame -> (N, 3) points; frames absent here are "no point cloud"
    point_clouds: dict[int, np.ndarray] = field(default_factory=dict)
    # frame -> 4x4 sensor->world pose
    poses: dict[int, np.ndarray] = field(default_factory=dict)
    # frame -> timestamp (s); used by R08 speed check
    timestamps: dict[int, float] = field(default_factory=dict)
    config: dict = field(default_factory=lambda: dict(DEFAULT_CONFIG))


RULES: dict[str, Callable[[QcContext], RuleResult]] = {}


def rule(code: str):
    def register(func):
        RULES[code] = func
        return func

    return register


def _finite(values) -> bool:
    return all(math.isfinite(v) for v in values)


@rule("R01")
def r01_invalid(ctx: QcContext) -> RuleResult:
    """Missing class, non-finite geometry, non-positive size."""
    result = RuleResult("R01", "checked")
    for box in ctx.cuboids:
        problems = []
        if not box.label:
            problems.append("thiếu class")
        if not (_finite(box.position) and _finite(box.rotation) and _finite(box.scale)):
            problems.append("toạ độ không hữu hạn")
        elif min(box.scale) <= 0:
            problems.append("kích thước ≤ 0")
        if problems:
            result.issues.append(
                Issue("R01", BLOCKER, box.uid, box.frame, "Nhãn không hợp lệ: " + ", ".join(problems))
            )
    return result


def _valid(boxes: list[Cuboid]) -> list[Cuboid]:
    return [
        b for b in boxes
        if b.label and _finite(b.position) and _finite(b.rotation) and _finite(b.scale) and min(b.scale) > 0
    ]


@rule("R02")
def r02_duplicates(ctx: QcContext) -> RuleResult:
    """Exact duplicates (same class, same geometry) and overlapping boxes in one frame."""
    cfg = ctx.config
    result = RuleResult("R02", "checked")
    by_frame = defaultdict(list)
    for box in _valid(ctx.cuboids):
        by_frame[box.frame].append(box)

    for frame, boxes in by_frame.items():
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                center = math.dist(a.position, b.position)
                if center > 10:
                    continue
                if (
                    a.label == b.label
                    and center <= cfg["duplicate_center_eps"]
                    and max(abs(x - y) for x, y in zip(a.scale, b.scale)) <= cfg["duplicate_size_eps"]
                ):
                    result.issues.append(
                        Issue("R02", MAJOR, b.uid, frame, f"Trùng lặp với {a.uid}",
                              {"duplicate_of": a.uid, "center_distance": round(center, 4)},
                              {"kind": "delete", "summary": f"Xoá bản trùng của {a.uid}"})
                    )
                    continue
                iou = iou_3d(a, b)
                if iou > cfg["overlap_iou"]:
                    result.issues.append(
                        Issue("R02", MAJOR, b.uid, frame, f"Chồng lấn với {a.uid} (IoU {iou:.2f})",
                              {"overlaps": a.uid, "iou": round(iou, 3)})
                    )
    return result


@rule("R03")
def r03_labels(ctx: QcContext) -> RuleResult:
    """Class outside the approved schema."""
    allowed = set(ctx.config["labels"])
    result = RuleResult("R03", "checked")
    for box in ctx.cuboids:
        if box.label and box.label not in allowed:
            result.issues.append(
                Issue("R03", MAJOR, box.uid, box.frame, f"Class '{box.label}' không có trong schema",
                      {"label": box.label, "allowed": sorted(allowed)})
            )
    return result


@rule("R04")
def r04_orientation(ctx: QcContext) -> RuleResult:
    """Yaw outside (-pi, pi] (auto-fixable) and excessive roll/pitch."""
    limit = math.radians(ctx.config["max_roll_pitch_deg"])
    result = RuleResult("R04", "checked")
    for box in _valid(ctx.cuboids):
        roll, pitch, yaw = box.rotation
        if abs(wrap_angle(roll)) > limit or abs(wrap_angle(pitch)) > limit:
            result.issues.append(
                Issue("R04", MINOR, box.uid, box.frame,
                      f"Box nghiêng bất thường (roll {math.degrees(roll):.0f}°, pitch {math.degrees(pitch):.0f}°)",
                      {"roll_deg": round(math.degrees(roll), 1), "pitch_deg": round(math.degrees(pitch), 1)})
            )
        if not (-math.pi < yaw <= math.pi):
            result.issues.append(
                Issue("R04", MINOR, box.uid, box.frame, "Góc yaw ngoài (−π, π]",
                      {"yaw": yaw, "fixed_yaw": wrap_angle(yaw), "autofix": True},
                      update_proposal(box, box.with_(rotation=(roll, pitch, wrap_angle(yaw)))))
            )
    return result


@rule("R05")
def r05_point_support(ctx: QcContext) -> RuleResult:
    """Too few LiDAR points for the distance, or the box sits next to its points (shifted)."""
    cfg = ctx.config
    boxes = [b for b in _valid(ctx.cuboids) if b.frame in ctx.point_clouds]
    if not boxes:
        return RuleResult("R05", "not_checked", reason="không có point cloud cho frame có nhãn")

    result = RuleResult("R05", "checked")
    for box in boxes:
        points = ctx.point_clouds[box.frame]
        distance = max(box.distance, 1.0)
        base = cfg["points_at_10m"].get(box.label, 50)
        required = max(cfg["min_points"], base * min(4.0, (10.0 / distance) ** 2))
        inside = count_points(points, box, ground_clearance=cfg["ground_clearance"])
        grown = count_points(points, box, margin=cfg["ring_margin"], ground_clearance=cfg["ground_clearance"])
        ring = grown - inside
        evidence = {"points": inside, "required": round(required, 1), "ring_points": ring,
                    "distance": round(distance, 1)}

        if inside < required:
            severity = MINOR if distance > cfg["far_distance"] else MAJOR
            proposal = None
            if distance <= cfg["fit_max_distance"]:
                # the object may sit next to the box: search one box width around it
                obs = observe(points, box, boxes, margin=box.scale[1] + 1.0)
                if len(obs.points) >= cfg["fit_min_points"]:
                    fitted = propose_fit(box, obs)
                    proposal = update_proposal(box, fitted) if fitted else None
            result.issues.append(
                Issue("R05", severity, box.uid, box.frame,
                      f"Ít điểm LiDAR: {inside} < {required:.0f} ở {distance:.0f} m", evidence, proposal)
            )
        elif ring >= cfg["ring_min_points"] and ring > cfg["ring_ratio"] * inside:
            fitted = propose_fit(box, observe(points, box, boxes, margin=box.scale[1] + 1.0))
            result.issues.append(
                Issue("R05", MAJOR, box.uid, box.frame,
                      f"Box có thể lệch: {ring} điểm sát ngoài so với {inside} điểm bên trong", evidence,
                      update_proposal(box, fitted) if fitted else None)
            )
    return result


@rule("R06")
def r06_size(ctx: QcContext) -> RuleResult:
    """Size outside the class guideline, or deviating from the track median."""
    cfg = ctx.config
    result = RuleResult("R06", "checked")
    boxes = _valid(ctx.cuboids)
    for box in boxes:
        ranges = cfg["size_ranges"].get(box.label)
        if not ranges:
            continue
        out = [
            f"{name} {value:.2f} ngoài [{lo}, {hi}]"
            for name, value, (lo, hi) in zip(("dài", "rộng", "cao"), box.scale, ranges)
            if not lo <= value <= hi
        ]
        if out:
            result.issues.append(
                Issue("R06", MAJOR, box.uid, box.frame, "Kích thước sai guideline: " + "; ".join(out),
                      {"scale": list(box.scale), "ranges": ranges})
            )

    tracks = defaultdict(list)
    for box in boxes:
        if box.track_id is not None:
            tracks[box.track_id].append(box)
    tolerance = cfg["track_size_tolerance"]
    for track_boxes in tracks.values():
        if len(track_boxes) < 3:
            continue
        median = np.median([b.scale for b in track_boxes], axis=0)
        for box in track_boxes:
            deviation = np.abs(np.asarray(box.scale) - median) / np.maximum(median, 1e-6)
            if deviation.max() > tolerance:
                result.issues.append(
                    Issue("R06", MAJOR, box.uid, box.frame,
                          f"Kích thước lệch {deviation.max():.0%} so với trung vị track",
                          {"track_median": median.round(3).tolist(), "scale": list(box.scale)})
                )
    return result


def _world_tracks(ctx: QcContext) -> dict[int, list[tuple[Cuboid, np.ndarray]]]:
    tracks = defaultdict(list)
    for box in _valid(ctx.cuboids):
        if box.track_id is None or box.frame not in ctx.poses:
            continue
        pose = ctx.poses[box.frame]
        world = pose[:3, :3] @ np.asarray(box.position) + pose[:3, 3]
        tracks[box.track_id].append((box, world))
    for items in tracks.values():
        items.sort(key=lambda item: item[0].frame)
    return tracks


def _has_tracks(ctx: QcContext) -> bool:
    return any(b.track_id is not None for b in ctx.cuboids)


@rule("R07")
def r07_track_jump(ctx: QcContext) -> RuleResult:
    """Keyframe whose world position jumps away from the interpolation of its neighbours."""
    if not _has_tracks(ctx):
        return RuleResult("R07", "not_checked", reason="job không có track")
    if not ctx.poses:
        return RuleResult("R07", "not_checked", reason="thiếu ego-pose")

    cfg = ctx.config
    result = RuleResult("R07", "checked")
    for items in _world_tracks(ctx).values():
        if len(items) < 3:
            continue
        residuals = []
        for (prev, p0), (box, p1), (nxt, p2) in zip(items, items[1:], items[2:]):
            t = (box.frame - prev.frame) / max(nxt.frame - prev.frame, 1)
            residuals.append((box, float(np.linalg.norm(p1 - (p0 + t * (p2 - p0)))), p0 + t * (p2 - p0)))
        values = np.array([r for _, r, _ in residuals])
        # one jump inflates its 2 neighbours' residuals too: estimate the noise without that window
        peak = int(values.argmax())
        background = np.delete(values, range(max(peak - 1, 0), min(peak + 2, len(values))))
        mad = float(np.median(np.abs(background - np.median(background)))) if len(background) >= 3 else 0.0
        threshold = max(cfg["jump_min"], cfg["jump_mad_factor"] * mad)
        for i, (box, residual, expected) in enumerate(residuals):
            local_max = residual >= max(values[max(i - 1, 0): i + 2])
            if residual > threshold and local_max:
                result.issues.append(
                    Issue("R07", MAJOR, box.uid, box.frame,
                          f"Track nhảy {residual:.2f} m so với nội suy (ngưỡng {threshold:.2f} m)",
                          {"residual": round(residual, 3), "threshold": round(threshold, 3),
                           "expected_world": expected.round(3).tolist()})
                )
    return result


@rule("R08")
def r08_track_consistency(ctx: QcContext) -> RuleResult:
    """Track speed above the class limit between keyframes (suspected ID switch)."""
    if not _has_tracks(ctx):
        return RuleResult("R08", "not_checked", reason="job không có track")
    if not ctx.poses or not ctx.timestamps:
        return RuleResult("R08", "not_checked", reason="thiếu ego-pose hoặc timestamp")

    result = RuleResult("R08", "checked")
    for items in _world_tracks(ctx).values():
        for (a, pa), (b, pb) in zip(items, items[1:]):
            dt = ctx.timestamps.get(b.frame, 0) - ctx.timestamps.get(a.frame, 0)
            if dt <= 0:
                continue
            speed = float(np.linalg.norm(pb - pa)) / dt
            limit = ctx.config["max_speed"].get(b.label, 40.0)
            if speed > limit:
                result.issues.append(
                    Issue("R08", MAJOR, b.uid, b.frame,
                          f"Vận tốc {speed:.1f} m/s vượt ngưỡng {limit:.0f} m/s (nghi đổi ID)",
                          {"speed": round(speed, 2), "limit": limit, "from_frame": a.frame})
                )
    return result


@rule("R09")
def r09_ground_contact(ctx: QcContext) -> RuleResult:
    """Box floating above or sunk into the locally measured ground."""
    cfg = ctx.config
    boxes = [b for b in _valid(ctx.cuboids) if b.frame in ctx.point_clouds]
    if not boxes:
        return RuleResult("R09", "not_checked", reason="không có point cloud cho frame có nhãn")

    result = RuleResult("R09", "checked")
    for box in boxes:
        obs = observe(ctx.point_clouds[box.frame], box, boxes)
        if obs.ground_z is None:
            continue
        gap = (box.position[2] - box.scale[2] / 2) - obs.ground_z
        if abs(gap) <= cfg["ground_tolerance"]:
            continue
        fixed = box.with_(position=(box.position[0], box.position[1], obs.ground_z + box.scale[2] / 2))
        result.issues.append(
            Issue("R09", MAJOR if abs(gap) > cfg["ground_major"] else MINOR, box.uid, box.frame,
                  f"Box {'lơ lửng' if gap > 0 else 'chìm'} {abs(gap):.2f} m so với mặt đường",
                  {"bottom_minus_ground": round(gap, 3), "ground_z": round(obs.ground_z, 3)},
                  update_proposal(box, fixed))
        )
    return result


@rule("R10")
def r10_box_fit(ctx: QcContext) -> RuleResult:
    """Box not tight on the faces the LiDAR sees (too loose / shifted) or taller than the object."""
    cfg = ctx.config
    boxes = [b for b in _valid(ctx.cuboids) if b.frame in ctx.point_clouds]
    if not boxes:
        return RuleResult("R10", "not_checked", reason="không có point cloud cho frame có nhãn")

    result = RuleResult("R10", "checked")
    for box in boxes:
        if box.distance > cfg["fit_max_distance"]:
            continue
        obs = observe(ctx.point_clouds[box.frame], box, boxes)
        if len(obs.points) < cfg["fit_min_points"]:
            continue
        gaps = visible_face_gaps(obs, box)
        loose = {}
        for name, axis in (("length", 0), ("width", 1)):
            if name in gaps and gaps[name] > max(cfg["face_gap"], cfg["face_gap_ratio"] * box.scale[axis]):
                loose[name] = round(gaps[name], 2)
        if gaps.get("height", 0) > max(cfg["height_gap"], cfg["height_gap_ratio"] * box.scale[2]):
            loose["height"] = round(gaps["height"], 2)
        if not loose:
            continue
        fitted = propose_fit(box, obs, shrink=True)
        names = {"length": "dài", "width": "rộng", "height": "cao"}
        result.issues.append(
            Issue("R10", MAJOR, box.uid, box.frame,
                  "Box không ôm sát vật: hở " + ", ".join(f"{names[k]} {v:.2f} m" for k, v in loose.items()),
                  {"gaps": loose, "points": len(obs.points)},
                  update_proposal(box, fitted) if fitted else None)
        )
    return result


@rule("R11")
def r11_class_by_size(ctx: QcContext) -> RuleResult:
    """Class that does not match the box size under the VN class priors."""
    cfg = ctx.config
    candidates = [name for name in CLASSES]
    result = RuleResult("R11", "checked")
    boxes = _valid(ctx.cuboids)
    for box in boxes:
        if box.label not in CLASSES:
            continue
        # 1) the LiDAR cluster cannot be this class (e.g. a motorbike labeled Truck)
        if box.frame in ctx.point_clouds and box.distance <= cfg["fit_max_distance"]:
            obs = observe(ctx.point_clouds[box.frame], box, boxes)
            from_points = class_from_points(obs, box)
            if from_points:
                in_schema = from_points in cfg["labels"]
                result.issues.append(
                    Issue("R11", MAJOR, box.uid, box.frame,
                          f"Nghi sai class: cụm điểm LiDAR là cỡ {from_points}, không thể là {box.label}"
                          + ("" if in_schema else f" (schema chưa có class {from_points})"),
                          {"suggested": from_points, "source": "points", "in_schema": in_schema},
                          update_proposal(box, box.with_(label=from_points)) if in_schema else None)
                )
                continue
            if len(obs.points) >= 150:
                extent = obs.points.max(axis=0) - obs.points.min(axis=0)
                if best_class_by_size(tuple(extent), candidates)[0] == box.label:
                    continue  # the object itself looks like its class: a wrong box size is R06 / R10
        # 2) the box size fits another class much better
        best, _ = best_class_by_size(box.scale, candidates)
        if not best or best == box.label:
            continue
        # how much better the best class explains the size than the current one
        margin = size_log_likelihood(CLASSES[best], box.scale) - size_log_likelihood(CLASSES[box.label], box.scale)
        if margin < cfg["class_margin"]:
            continue
        in_schema = best in cfg["labels"]
        result.issues.append(
            Issue("R11", MAJOR, box.uid, box.frame,
                  f"Nghi sai class: kích thước giống {best} hơn {box.label}"
                  + ("" if in_schema else f" (schema chưa có class {best})"),
                  {"suggested": best, "margin": round(margin, 1), "scale": list(box.scale),
                   "in_schema": in_schema},
                  update_proposal(box, box.with_(label=best)) if in_schema else None)
        )
    return result


GEOMETRIC_RULES = {"R05", "R06", "R09", "R10", "R11"}


def _consolidate_proposals(ctx: QcContext, results: list[RuleResult]) -> None:
    """One consistent correction per object instead of one partial fix per rule."""
    cfg = ctx.config
    boxes = _valid(ctx.cuboids)
    by_uid = {b.uid: b for b in boxes}
    issues_by_uid = defaultdict(list)
    for result in results:
        for issue in result.issues:
            if issue.rule in GEOMETRIC_RULES:
                issues_by_uid[issue.uid].append(issue)

    for uid, issues in issues_by_uid.items():
        box = by_uid.get(uid)
        if box is None or box.frame not in ctx.point_clouds or box.distance > cfg["fit_max_distance"]:
            continue
        points = ctx.point_clouds[box.frame]
        inside = count_points(points, box, ground_clearance=cfg["ground_clearance"])
        # search wider only when the box has lost its object
        margin = 0.4 if inside >= cfg["fit_min_points"] else box.scale[1] + 1.0
        obs = observe(points, box, boxes, margin=margin)
        if len(obs.points) < cfg["fit_min_points"]:
            continue
        label = None
        for issue in issues:
            suggested = issue.evidence.get("suggested")
            if issue.rule == "R11" and suggested and suggested in cfg["labels"]:
                label = suggested
        rules = {issue.rule for issue in issues}
        gaps = next((i.evidence.get("gaps", {}) for i in issues if i.rule == "R10"), {})
        shifted = "R05" in rules and inside < cfg["fit_min_points"] or any(
            i.rule == "R05" and "lệch" in i.message for i in issues
        )
        fixed = propose_object_fix(
            box, obs,
            label=label,
            fix_xy=shifted or "length" in gaps or "width" in gaps,
            fix_height="height" in gaps,
            fix_ground="R09" in rules,
        )
        if fixed is None:
            continue
        proposal = update_proposal(box, fixed)
        if not proposal["changes"]:
            continue
        for issue in issues:
            issue.proposal = proposal


def run_rules(ctx: QcContext, codes: list[str] | None = None) -> list[RuleResult]:
    results = [RULES[code](ctx) for code in (codes or sorted(RULES))]
    _consolidate_proposals(ctx, results)
    return results
