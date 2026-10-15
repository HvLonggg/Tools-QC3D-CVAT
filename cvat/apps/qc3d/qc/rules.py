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
from .objects import extend_to, reference_sizes, too_short
from .geometry import Cuboid, count_points, iou_3d, wrap_angle
from .verify import apply_changes, change_kinds, verify_fix
from .taxonomy import CLASSES, ClassSpec, best_class_by_size, resolve_two_wheeler, size_log_likelihood

RULES_VERSION = "2026.10.14"

BLOCKER, MAJOR, MINOR = "blocker", "major", "minor"

THRESHOLDS = {
    "track_size_tolerance": 0.15,
    # R02
    "duplicate_center_eps": 0.05,
    "duplicate_size_eps": 0.05,
    "overlap_iou": 0.3,
    # R04
    "max_roll_pitch_deg": 15.0,
    # R05: minimum points = max(min_points, base * min(4, (10 / distance)^2))
    "min_points": 3,
    "far_distance": 60.0,  # beyond this a low-point warning is minor
    "ground_clearance": 0.2,
    "ring_margin": 0.5,
    "ring_ratio": 0.5,  # points in the ring around the box > ratio * points inside -> box shifted
    "ring_min_points": 30,
    # R07 / R08 (need ego poses)
    "jump_min": 0.5,
    "jump_mad_factor": 6.0,
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
    # auto-fix: a correction larger than this is left to the reviewer (qc/verify.py)
    "autofix_max_shift": 1.5,  # m, box centre
    "autofix_max_scale_change": 0.3,  # relative change of any size axis
    "autofix_min_coverage": 0.95,  # share of the object points inside the corrected box
    "autofix_class_margin": 2.0,  # size log-likelihood lead of a new class over every other class
    # R12 model vs labels: only classes / range where the model was measured reliable on VN roads
    # (PHENIKAA held-out scenes, epoch 20: Car and Rider within 40 m, precision 85-96 %)
    "model_max_distance": 30.0,  # far objects: the model is not accurate enough yet (v1)
    "model_missing_score": 0.6,  # a confident detection without any label nearby
    "model_class_score": 0.4,  # a confident detection of another class on a labeled object
    "model_match_distance": 1.5,  # m between a detection centre and a label centre
    "model_missing_per_frame": 8,  # more missing objects than this in a frame: one issue for the frame
}

# classes whose model output R12 trusts (others are reported by the model but never acted on)
MODEL_TRUSTED_CLASSES = ("Car", "Rider")


def build_config(
    classes: dict[str, ClassSpec] = CLASSES, thresholds: dict | None = None, disabled_rules=()
) -> dict:
    """QC configuration from a class guideline (default: the VN taxonomy) and threshold overrides."""
    return {
        **THRESHOLDS,
        **(thresholds or {}),
        "classes": dict(classes),
        "labels": list(classes),
        # length, width, height ranges (m) per class
        "size_ranges": {name: list(zip(s.size_min, s.size_max)) for name, s in classes.items()},
        "points_at_10m": {name: s.points_at_10m for name, s in classes.items()},
        "max_speed": {name: s.max_speed for name, s in classes.items()},
        "disabled_rules": sorted(disabled_rules),
    }


DEFAULT_CONFIG = build_config()


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
    # frame -> model detections [(x, y, z, l, w, h, yaw), label, score]; used by R12 (None: model not run)
    detections: dict[int, list] | None = None
    config: dict = field(default_factory=lambda: dict(DEFAULT_CONFIG))
    # box uid -> real size of its object over the frames (qc/objects.py); None: computed from `cuboids`
    reference: dict[str, dict] | None = None
    # boxes a reviewer marked as wrong (R14): each gets a complete correction, model included
    marked: set[str] = field(default_factory=set)
    # detections before one-box-per-object (run_rules): R12 still sees a second class the model considered
    raw_detections: dict[int, list] | None = None
    # (box geometry, margin) -> BoxObservation: several rules look at the same box's points
    observations: dict = field(default_factory=dict, repr=False)


RULES: dict[str, Callable[[QcContext], RuleResult]] = {}


def rule(code: str):
    def register(func):
        RULES[code] = func
        return func

    return register


def _observe(ctx: QcContext, box: Cuboid, boxes: list[Cuboid], margin: float = 0.4):
    """observe() once per box and margin in a run (the other boxes of the frame are the same for all rules)."""
    key = (box.uid, box.label, box.position, box.rotation, box.scale, round(margin, 3))
    if key not in ctx.observations:
        ctx.observations[key] = observe(ctx.point_clouds[box.frame], box, boxes, margin=margin,
                                        classes=ctx.config["classes"])
    return ctx.observations[key]


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
                obs = _observe(ctx, box, boxes, margin=box.scale[1] + 1.0)
                if len(obs.points) >= cfg["fit_min_points"]:
                    fitted = propose_fit(box, obs, classes=cfg["classes"])
                    proposal = update_proposal(box, fitted) if fitted else None
            result.issues.append(
                Issue("R05", severity, box.uid, box.frame,
                      f"Ít điểm LiDAR: {inside} < {required:.0f} ở {distance:.0f} m", evidence, proposal)
            )
        elif ring >= cfg["ring_min_points"] and ring > cfg["ring_ratio"] * inside:
            obs = _observe(ctx, box, boxes, margin=box.scale[1] + 1.0)
            fitted = propose_fit(box, obs, classes=cfg["classes"])
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
        if box.distance > cfg["fit_max_distance"]:
            continue  # too few road points far away to judge (PHENIKAA: most boxes beyond 40 m had none)
        obs = _observe(ctx, box, boxes)
        if obs.ground_z is None:
            continue
        gap = (box.position[2] - box.scale[2] / 2) - obs.ground_z
        if abs(gap) <= cfg["ground_tolerance"]:
            continue
        fixed = box.with_(position=(box.position[0], box.position[1], obs.ground_z + box.scale[2] / 2))
        result.issues.append(
            Issue("R09", MAJOR if abs(gap) > cfg["ground_major"] else MINOR, box.uid, box.frame,
                  f"Box {'lơ lửng' if gap > 0 else 'chìm'} {abs(gap):.2f} m so với mặt đường"
                  + ("" if obs.ground_flat else " (mặt đường dốc / gồ ghề: cần xem tay)"),
                  {"bottom_minus_ground": round(gap, 3), "ground_z": round(obs.ground_z, 3),
                   "ground_flat": obs.ground_flat},
                  # a box is put on the road plane only where the road around it is a plane
                  update_proposal(box, fixed) if obs.ground_flat else None)
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
        obs = _observe(ctx, box, boxes)
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
        fitted = propose_fit(box, obs, shrink=True, classes=cfg["classes"])
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
    """Class that does not match the box size under the guideline class priors."""
    cfg = ctx.config
    classes = cfg["classes"]
    candidates = list(classes)
    result = RuleResult("R11", "checked")
    boxes = _valid(ctx.cuboids)
    for box in boxes:
        if box.label not in classes:
            continue
        # 1) the LiDAR cluster cannot be this class (e.g. a motorbike labeled Truck)
        if box.frame in ctx.point_clouds and box.distance <= cfg["fit_max_distance"]:
            obs = _observe(ctx, box, boxes)
            from_points = class_from_points(obs, box, classes=classes)
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
                if best_class_by_size(tuple(extent), candidates, classes)[0] == box.label:
                    continue  # the object itself looks like its class: a wrong box size is R06 / R10
        # 2) the box size fits another class much better
        best, _ = best_class_by_size(box.scale, candidates, classes)
        best = resolve_two_wheeler(best, box.scale[2], classes)
        if not best or best == box.label:
            continue
        # how much better the best class explains the size than the current one
        margin = size_log_likelihood(classes[best], box.scale) - size_log_likelihood(classes[box.label], box.scale)
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


@rule("R12")
def r12_model(ctx: QcContext) -> RuleResult:
    """Labels that disagree with the detection model, where the model is reliable (trusted classes, near)."""
    cfg = ctx.config
    if ctx.detections is None:
        return RuleResult("R12", "not_checked", reason="chưa chạy model cho job (QC Check → Chạy model)")
    trusted = [c for c in MODEL_TRUSTED_CLASSES if c in cfg["labels"]]
    if not trusted:
        return RuleResult("R12", "not_checked", reason="schema không có class model đáng tin (Car, Rider)")
    by_frame = defaultdict(list)
    for box in _valid(ctx.cuboids):
        by_frame[box.frame].append(box)
    result = RuleResult("R12", "checked")
    for frame, detections in sorted(ctx.detections.items()):
        missing = []
        boxes = by_frame.get(frame, [])
        centers = np.array([b.position[:2] for b in boxes]) if boxes else np.zeros((0, 2))
        for detection in detections:
            values, name, score = detection
            if name not in trusted or math.hypot(values[0], values[1]) > cfg["model_max_distance"]:
                continue
            gaps = np.hypot(*(centers - np.asarray(values[:2])).T) if len(centers) else np.zeros(0)
            near = [boxes[i] for i in np.flatnonzero(gaps <= cfg["model_match_distance"])]
            if not near:
                overlap = any(
                    iou_3d(b, Cuboid("m", frame, name, tuple(values[:3]), (0, 0, values[6]), tuple(values[3:6]))) > 0.05
                    for b in boxes if math.dist(b.position[:2], values[:2]) < 8
                )
                if score >= cfg["model_missing_score"] and not overlap:
                    missing.append(Issue(
                        "R12", MAJOR, f"m:{frame}:{round(values[0])}:{round(values[1])}", frame,
                        f"Có thể thiếu nhãn: model thấy {name} (tin cậy {score:.2f}) "
                        f"ở {math.hypot(values[0], values[1]):.0f} m, không có box nào",
                        {"model_box": [round(float(v), 3) for v in values], "model_class": name,
                         "score": round(float(score), 3)},
                    ))
                continue
            box = min(near, key=lambda b: math.dist(b.position[:2], values[:2]))
            if box.label == name or score < cfg["model_class_score"]:
                continue
            # the model also supports the labeled class there: no disagreement worth a reviewer's time
            considered = (ctx.raw_detections or {}).get(frame, detections)
            if any(other[1] == box.label and other[2] >= 0.3 and math.dist(other[0][:2], box.position[:2])
                   <= cfg["model_match_distance"] for other in considered):
                continue
            result.issues.append(Issue(
                "R12", MAJOR, box.uid, frame,
                f"Model thấy {name} (tin cậy {score:.2f}), nhãn là {box.label}",
                {"suggested": name, "source": "model", "score": round(float(score), 3), "in_schema": True},
                update_proposal(box, box.with_(label=name)),
            ))
        if len(missing) > cfg["model_missing_per_frame"]:
            # a frame that is mostly not labeled yet: one issue, not dozens
            counts = defaultdict(int)
            for issue in missing:
                counts[issue.evidence["model_class"]] += 1
            result.issues.append(Issue(
                "R12", BLOCKER, f"m:{frame}:frame", frame,
                f"Frame chưa gán nhãn đủ: model thấy {len(missing)} vật không có box ("
                + ", ".join(f"{n} {c}" for c, n in sorted(counts.items())) + ")",
                {"model_class": "", "missing": len(missing), "by_class": dict(counts),
                 "model_boxes": [i.evidence["model_box"] + [i.evidence["model_class"], i.evidence["score"]]
                                 for i in missing[:200]]},
            ))
        else:
            result.issues.extend(missing)
    return result


@rule("R13")
def r13_object_size(ctx: QcContext) -> RuleResult:
    """Box shorter than its object: the same object is longer / wider in the other frames (qc/objects.py)."""
    reference = _reference(ctx)
    result = RuleResult("R13", "checked")
    names = {"length": "dài", "width": "rộng"}
    for box in _valid(ctx.cuboids):
        ref = reference.get(box.uid)
        if not ref or ref["label"] != box.label:
            continue
        short = too_short(box, ref["size"])
        if not short:
            continue
        axes = [axis for axis, name in enumerate(("length", "width")) if name in short]
        fixed = extend_to(box, ref["size"], axes=axes)
        parts = [f"{names[name]} {box.scale[axis]:.2f} m, vật {names[name]} {ref['size'][axis]:.2f} m"
                 for axis, name in enumerate(("length", "width")) if name in short]
        result.issues.append(Issue(
            "R13", MAJOR, box.uid, box.frame,
            "Box ngắn hơn vật thật: " + "; ".join(parts) + f" (theo {ref['frames']} frame của cùng vật)",
            {"short": short, "reference": [round(v, 3) for v in ref["size"]], "frames": ref["frames"]},
            update_proposal(box, fixed),
        ))
    return result


def _reference(ctx: QcContext) -> dict[str, dict]:
    if ctx.reference is None:
        ctx.reference = reference_sizes(_valid(ctx.cuboids), ctx.config["classes"])
    return ctx.reference


GEOMETRIC_RULES = {"R05", "R06", "R09", "R10", "R11", "R13"}


REPAIR_RULES = GEOMETRIC_RULES | {"R04", "R12", "R14"}
LIMIT_CHECK = "quá giới hạn tự sửa"  # auto-fix size limits: not a sign of a worse correction


def _point_fix(ctx: QcContext, box: Cuboid, issues: list[Issue], boxes: list[Cuboid], label: str | None):
    """Correction from the box's own LiDAR points (re-anchor, trim, ground), or None."""
    cfg = ctx.config
    if box.frame not in ctx.point_clouds or box.distance > cfg["fit_max_distance"]:
        return None
    points = ctx.point_clouds[box.frame]
    inside = count_points(points, box, ground_clearance=cfg["ground_clearance"])
    # search wider only when the box has lost its object
    margin = 0.4 if inside >= cfg["fit_min_points"] else box.scale[1] + 1.0
    obs = _observe(ctx, box, boxes, margin=margin)
    if len(obs.points) < cfg["fit_min_points"]:
        return None
    rules = {issue.rule for issue in issues}
    if rules == {"R14"}:
        # marked by a reviewer, no rule says what is wrong: visible faces and road, size kept
        return propose_fit(box, obs, classes=cfg["classes"]) if obs.ground_flat else None
    gaps = next((i.evidence.get("gaps", {}) for i in issues if i.rule == "R10"), {})
    shifted = "R05" in rules and inside < cfg["fit_min_points"] or any(
        i.rule == "R05" and "lệch" in i.message for i in issues
    )
    return propose_object_fix(
        box, obs,
        label=label,
        fix_xy=shifted,
        trim_axes=tuple(axis for axis, name in enumerate(("length", "width")) if name in gaps),
        fix_height="height" in gaps,
        fix_ground="R09" in rules,
        classes=cfg["classes"],
    )


def _consolidate_proposals(ctx: QcContext, results: list[RuleResult]) -> None:
    """
    One complete correction per object instead of one partial fix per rule: candidates from the box's own
    points, from the detection model's box of the same object and (class errors) a class change only are
    each checked against the guideline (qc/verify.py); the one that fits the object best is proposed for
    every issue of the object, so a single "fix" sets position, size, heading and class together.
    """
    from .repair import guideline_class, match_detections, model_refit

    cfg = ctx.config
    boxes = _valid(ctx.cuboids)
    by_uid = {b.uid: b for b in boxes}
    reference = _reference(ctx)
    issues_by_uid = defaultdict(list)
    for result in results:
        for issue in result.issues:
            if issue.rule in REPAIR_RULES and not issue.uid.startswith("m:"):
                issues_by_uid[issue.uid].append(issue)

    matches = {}
    if ctx.detections:
        by_frame = defaultdict(list)
        for box in boxes:
            by_frame[box.frame].append(box)
        for frame, detections in ctx.detections.items():
            matches.update(match_detections(by_frame.get(frame, []), detections))
    trusted = [c for c in MODEL_TRUSTED_CLASSES if c in cfg["labels"]]

    for uid, issues in issues_by_uid.items():
        box = by_uid.get(uid)
        if box is None:
            continue
        points = ctx.point_clouds.get(box.frame)
        rules = {issue.rule for issue in issues}
        label = None
        for issue in sorted(issues, key=lambda i: i.rule != "R12"):  # R11 (points / size) wins over R12
            suggested = issue.evidence.get("suggested")
            if issue.rule in ("R11", "R12") and suggested and suggested in cfg["labels"]:
                label = suggested
        candidates = []  # (method, corrected box, preference: lower first)
        geometric = bool(rules & {"R05", "R06", "R09", "R10", "R13", "R14"})
        fitted = _point_fix(ctx, box, issues, boxes, label)
        # measured on injected errors: the point fit is the more accurate one when it passes the checks;
        # the model's box rebuilds what the points cannot (box on the wrong spot, wrong size or class)
        if fitted is not None:
            candidates.append(("points", fitted, 0))
        detection = matches.get(uid)
        model_class = None
        # the model rebuilds a box only where it was measured reliable (trusted classes, within range)
        if detection is not None and (
            (detection[1] not in trusted and "R14" not in rules)
            or math.hypot(*detection[0][:2]) > cfg["model_max_distance"]
        ):
            detection = None  # a reviewer's mark asks for the model on any class, still within range
        if detection is not None:
            refit = model_refit(box, detection, points, cfg, trusted)
            # a class change is the model's call only for the object the label was drawn on: a box shifted
            # onto a neighbour must not take the neighbour's class
            inside = box.with_(scale=(box.scale[0] + 0.6, box.scale[1] + 0.6, box.scale[2] + 10))
            if refit.label == box.label or count_points(np.asarray([detection[0][:3]], np.float32), inside):
                # a reviewer who marked the box asked for the model's box first
                candidates.append(("model", refit, -1 if "R14" in rules else 1))
                model_class = refit.label
        if label and label != box.label:
            candidates.append(("class", box.with_(label=label), 0 if not geometric else 2))
        elif detection is not None and not geometric:
            model_label = guideline_class(box.label, detection, cfg, trusted)
            if model_label != box.label:
                candidates.append(("class", box.with_(label=model_label), 0))
        if "R04" in rules:
            candidates = [(m, c.with_(rotation=(0.0, 0.0, wrap_angle(c.yaw))), p) for m, c, p in candidates] or [
                ("rotation", box.with_(rotation=(0.0, 0.0, wrap_angle(box.yaw))), 0)
            ]
        ref = reference.get(uid)
        if "R13" in rules and ref:
            candidates.append(("size", extend_to(box, ref["size"]), 1))
        if ref and ref["label"] == box.label:
            # no correction makes a box shorter than its object (the LiDAR seldom sees the far side)
            candidates = [(m, extend_to(c, ref["size"]) if c.label == box.label else c, p) for m, c, p in candidates]
        if not candidates:
            for issue in issues:
                if issue.rule == "R14":
                    issue.evidence["no_fix"] = "không có điểm LiDAR hay box model đủ tin để sửa"
            continue

        def cost(candidate) -> tuple:
            failed = verify_fix(box, candidate[1], boxes, points, cfg, model_class)
            return len([f for f in failed if LIMIT_CHECK not in f]), candidate[2]

        method, fixed, _ = min(candidates, key=cost)
        if cost((method, fixed, 0))[0] and box.distance <= cfg["fit_max_distance"] and points is not None and (
            count_points(points, box, margin=0.2, ground_clearance=cfg["ground_clearance"]) < 10
        ):
            # nothing to fit and no valid correction: a box drawn on empty space (reviewer decides)
            proposal = {"kind": "delete", "summary": "Xoá box: gần như không có điểm LiDAR, không phải vật thể"}
            for issue in issues:
                issue.proposal = proposal
            continue
        proposal = update_proposal(box, fixed)
        if not proposal["changes"]:
            continue
        proposal["method"] = method
        if model_class:
            proposal["model_class"] = model_class
        proposal["summary"] = f"{proposal['summary']} · theo {dict(points='điểm LiDAR', model='model', size='kích thước vật qua các frame', **{'class': 'class', 'rotation': 'góc'})[method]}"
        for issue in issues:
            issue.proposal = proposal


def _create_proposals(ctx: QcContext, results: list[RuleResult]) -> None:
    """New boxes for objects the model sees with no label (R12): each one checked before auto-fix may add it."""
    from .repair import create_shape, new_box, verify_create

    cfg = ctx.config
    boxes = _valid(ctx.cuboids)
    for result in results:
        if result.rule != "R12":
            continue
        for issue in result.issues:
            if not issue.uid.startswith("m:"):
                continue
            points = ctx.point_clouds.get(issue.frame)
            items = issue.evidence.get("model_boxes") or [
                issue.evidence["model_box"] + [issue.evidence["model_class"], issue.evidence["score"]]
            ]
            shapes, refused, created = [], [], []
            for n, item in enumerate(items):
                values, name, score = item[:7], item[7], item[8]
                box = new_box((values, name, score), issue.frame, f"{issue.uid}:{n}", points, cfg)
                failed = verify_create(box, boxes + created, points, cfg)
                if failed:
                    refused.append(f"{name} ({score:.2f}): {failed[0]}")
                    continue
                created.append(box)
                shapes.append(create_shape(box, score))
            if not shapes:
                issue.evidence["create_refused"] = refused[:20]
                continue
            counts = defaultdict(int)
            for shape in shapes:
                counts[shape["label"]] += 1
            issue.proposal = {
                "kind": "create",
                "shapes": shapes,
                "summary": "Thêm " + ", ".join(f"{n} {c}" for c, n in sorted(counts.items()))
                + (f" (bỏ {len(refused)} chưa chắc)" if refused else ""),
                "verified": True,
                "kinds": ["create"],
                "checks": refused[:20],
                "method": "model",
            }


def _verify_proposals(ctx: QcContext, results: list[RuleResult]) -> None:
    """
    Mark each proposal verified (auto-fix may write it) or not, with the failed checks (qc/verify.py),
    and the kinds of change it makes (guideline autofix.fix).
    """
    boxes = _valid(ctx.cuboids)
    by_uid = {b.uid: b for b in boxes}
    done = set()
    for result in results:
        for issue in result.issues:
            proposal = issue.proposal
            if not proposal or id(proposal) in done:
                continue
            done.add(id(proposal))
            box = by_uid.get(issue.uid)
            if proposal["kind"] == "create":
                continue  # checked when it was made (_create_proposals)
            if proposal["kind"] == "delete" or box is None:
                proposal.update(verified=False, kinds=["delete"], checks=["xoá box: QC cần xem trước khi xoá"])
                continue
            fixed = apply_changes(box, proposal["changes"])
            failed = verify_fix(box, fixed, boxes, ctx.point_clouds.get(box.frame), ctx.config,
                                proposal.get("model_class"))
            proposal.update(verified=not failed, kinds=change_kinds(box, fixed), checks=failed)


def run_rules(ctx: QcContext, codes: list[str] | None = None) -> list[RuleResult]:
    from .repair import dedupe_detections

    if ctx.detections:
        # the model unsure between two classes gives one object two boxes: keep the more confident one
        ctx.raw_detections = ctx.detections
        ctx.detections = {frame: dedupe_detections(items) for frame, items in ctx.detections.items()}
    disabled = set(ctx.config.get("disabled_rules", ()))
    results = [
        RuleResult(code, "not_checked", reason="tắt theo guideline") if code in disabled else RULES[code](ctx)
        for code in (codes or sorted(RULES))
    ]
    if ctx.marked:
        marked = RuleResult("R14", "checked")
        marked.issues = [
            Issue("R14", MAJOR, box.uid, box.frame, "Người duyệt đánh dấu nhãn lỗi", {"source": "reviewer"})
            for box in _valid(ctx.cuboids) if box.uid in ctx.marked
        ]
        results.append(marked)
    _consolidate_proposals(ctx, results)
    _create_proposals(ctx, results)
    _verify_proposals(ctx, results)
    return results
