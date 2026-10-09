# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""Pure unit tests for the QC rules (no Django): python -m pytest cvat/apps/qc3d/tests/test_rules.py"""

import math

import numpy as np

from cvat.apps.qc3d.qc.geometry import Cuboid, count_points, iou_3d, wrap_angle
from cvat.apps.qc3d.qc.inject import EXPECTED_RULES, inject
from cvat.apps.qc3d.qc.rules import QcContext, run_rules


def car(uid="s:1", x=10.0, y=0.0, yaw=0.0, **changes) -> Cuboid:
    box = Cuboid(uid=uid, frame=0, label="Car", position=(x, y, 0.8), rotation=(0.0, 0.0, yaw),
                 scale=(4.5, 1.9, 1.6))
    return box.with_(**changes) if changes else box


def car_points(box: Cuboid, n: int = 2000, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    local = rng.uniform(-0.5, 0.5, size=(n, 3)) * np.asarray(box.scale) * 0.9
    c, s = math.cos(box.yaw), math.sin(box.yaw)
    world = np.column_stack([local[:, 0] * c - local[:, 1] * s, local[:, 0] * s + local[:, 1] * c, local[:, 2]])
    return (world + np.asarray(box.position)).astype(np.float32)


def issues_by_rule(ctx: QcContext) -> dict[str, list]:
    return {r.rule: r.issues for r in run_rules(ctx)}


def test_clean_box_has_no_issues():
    box = car()
    found = issues_by_rule(QcContext([box], {0: car_points(box)}))
    assert all(not issues for issues in found.values())


def test_missing_point_cloud_is_not_checked_not_passed():
    results = {r.rule: r for r in run_rules(QcContext([car()]))}
    assert results["R05"].status == "not_checked"
    assert results["R07"].status == "not_checked"


def test_invalid_and_unknown_label():
    found = issues_by_rule(QcContext([car(scale=(4.5, 0.0, 1.6)), car(uid="s:2", label="xe_may", y=20)]))
    assert [i.uid for i in found["R01"]] == ["s:1"]
    assert [i.uid for i in found["R03"]] == ["s:2"]


def test_duplicate_and_overlap():
    a = car()
    found = issues_by_rule(QcContext([a, a.with_(uid="s:2"), car(uid="s:3", x=11.0)]))
    messages = {i.uid: i.message for i in found["R02"]}
    assert "Trùng lặp" in messages["s:2"]
    assert "Chồng lấn" in messages["s:3"]


def test_tilt_and_yaw_wrap():
    found = issues_by_rule(QcContext([car(rotation=(math.radians(30), 0, 0)), car(uid="s:2", y=20, yaw=4.0)]))
    assert {i.uid for i in found["R04"]} == {"s:1", "s:2"}
    assert any(i.evidence.get("autofix") for i in found["R04"])


def test_shifted_box_is_flagged():
    box = car()
    shifted = box.with_(position=(10.0, 2.6, 0.8))
    found = issues_by_rule(QcContext([shifted], {0: car_points(box)}))
    assert [i.uid for i in found["R05"]] == ["s:1"]


def test_size_guideline():
    found = issues_by_rule(QcContext([car(scale=(9.0, 3.0, 1.6))]))
    assert [i.uid for i in found["R06"]] == ["s:1"]


def test_track_jump_needs_poses_and_detects_jump():
    track = [car(uid=f"t:7:{f}", frame=f, x=10.0 + f, track_id=7, source_kind="track") for f in range(6)]
    track[3] = track[3].with_(position=(13.0, 3.0, 0.8))
    assert {r.rule: r.status for r in run_rules(QcContext(track))}["R07"] == "not_checked"

    poses = {f: np.eye(4) for f in range(6)}
    found = issues_by_rule(QcContext(track, poses=poses))
    assert [i.uid for i in found["R07"]] == ["t:7:3"]


def test_geometry_helpers():
    assert math.isclose(wrap_angle(3 * math.pi / 2), -math.pi / 2)
    assert math.isclose(wrap_angle(math.pi), math.pi)
    assert math.isclose(iou_3d(car(), car()), 1.0, rel_tol=1e-6)
    assert iou_3d(car(), car(x=30)) == 0.0
    box = car(yaw=0.7)
    assert count_points(car_points(box), box) == 2000


def test_floating_box_gets_ground_proposal():
    box = car()
    ground = np.column_stack([
        np.random.default_rng(1).uniform(5, 15, 400), np.random.default_rng(2).uniform(-3, 3, 400), np.zeros(400),
    ]).astype(np.float32)
    floating = box.with_(position=(10.0, 0.0, 1.6))
    found = issues_by_rule(QcContext([floating], {0: np.concatenate([car_points(box), ground])}))
    proposal = found["R09"][0].proposal
    assert abs(proposal["changes"]["position"][2] - 0.8) < 0.1


def test_class_suggestion_and_json_safe_output():
    import json

    truck = car(scale=(8.0, 2.4, 3.1))
    cloud = car_points(truck)
    results = run_rules(QcContext([truck], {0: cloud}))
    r11 = next(r for r in results if r.rule == "R11").issues
    assert r11 and r11[0].evidence["suggested"] == "Truck"
    for result in results:
        for issue in result.issues:
            json.dumps(issue.evidence, default=lambda o: o.tolist() if hasattr(o, "tolist") else float(o))


def test_injection_recall_on_synthetic_scene():
    boxes = [car(uid=f"s:{i}", x=8.0 + 6 * (i % 5), y=-12.0 + 6 * (i // 5)) for i in range(20)]
    cloud = np.concatenate([car_points(b, 1500, seed=i) for i, b in enumerate(boxes)])
    corrupted, errors = inject(boxes, {0: cloud}, per_kind=2, seed=1)
    found = {(i.rule, i.uid) for r in run_rules(QcContext(corrupted, {0: cloud})) for i in r.issues}
    caught = sum(any((code, e.uid) in found for code in EXPECTED_RULES[e.kind]) for e in errors)
    assert caught / len(errors) >= 0.95
