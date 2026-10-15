# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Auto-fix safety (no Django): a correction is written automatically only when the corrected box passes
the guideline checks, lands on no other object and changes the existing object in place.
  python -m pytest cvat/apps/qc3d/tests/test_autofix.py
"""

import math
from types import SimpleNamespace

import numpy as np

from cvat.apps.qc3d.qc.autofix import plan_autofix
from cvat.apps.qc3d.qc.geometry import Cuboid, iou_3d
from cvat.apps.qc3d.qc.guideline import parse_file
from cvat.apps.qc3d.qc.rules import DEFAULT_CONFIG, QcContext, run_rules
from cvat.apps.qc3d.qc.verify import apply_changes, change_kinds, verify_fix
from cvat.apps.qc3d.tests.test_rules import car, car_points


def ground(x=(0, 20), y=(-6, 6), n=3000, seed=1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.column_stack([rng.uniform(*x, n), rng.uniform(*y, n), np.zeros(n)]).astype(np.float32)


def proposals(results) -> dict[str, dict]:
    return {i.uid: i.proposal for r in results for i in r.issues if i.proposal}


def test_iou_is_symmetric_and_bounded_for_parallel_boxes():
    """Regression: same-yaw boxes made the polygon clipping divide by ~0 (IoU 1.39 seen)."""
    big = Cuboid("a", 0, "Car", (-9.8376, 18.8291, 0.4), (0, 0, -3.137182), (4.8299, 2.7007, 1.62))
    small = Cuboid("b", 0, "Car", (-10.0308, 19.1989, 0.4), (0, 0, -3.137182), (4.4468, 1.9594, 1.62))
    assert 0 < iou_3d(big, small) <= 1
    assert math.isclose(iou_3d(big, small), iou_3d(small, big), rel_tol=1e-9)


def test_floating_box_fix_is_verified():
    box = car()
    floating = box.with_(position=(10.0, 0.0, 1.5))
    cloud = np.concatenate([car_points(box), ground()])
    proposal = proposals(run_rules(QcContext([floating], {0: cloud})))[floating.uid]
    assert proposal["verified"], proposal["checks"]
    assert proposal["kinds"] == ["ground"]
    assert abs(apply_changes(floating, proposal["changes"]).position[2] - 0.8) < 0.1


def test_loose_box_keeps_the_far_face():
    """Only the face the LiDAR sees is moved onto the points; the unseen far face stays."""
    box = car(x=10.0)
    loose = box.with_(position=(9.6, 0.0, 0.8), scale=(5.3, 1.9, 1.6))  # extended 0.8 m towards the sensor
    cloud = np.concatenate([car_points(box), ground()])
    proposal = proposals(run_rules(QcContext([loose], {0: cloud})))[loose.uid]
    fixed = apply_changes(loose, proposal["changes"])
    assert proposal["verified"], proposal["checks"]
    assert abs((fixed.position[0] + fixed.scale[0] / 2) - (loose.position[0] + loose.scale[0] / 2)) < 0.01
    assert iou_3d(fixed, box) > 0.8


def test_fix_landing_on_another_object_is_not_verified():
    box, other = car(), car(uid="s:2", x=10.0, y=2.2)
    cfg = dict(DEFAULT_CONFIG)
    cloud = np.concatenate([car_points(box), car_points(other, seed=2), ground()])
    moved = box.with_(position=(10.0, 1.6, 0.8))
    failed = verify_fix(box, moved, [box, other], cloud, cfg)
    assert any("đè lên s:2" in check for check in failed)


def test_large_move_or_resize_away_from_class_is_not_verified():
    box = car()
    cfg = dict(DEFAULT_CONFIG)
    cloud = np.concatenate([car_points(box), ground()])
    assert any("quá giới hạn" in c for c in verify_fix(box, box.with_(position=(12.5, 0, 0.8)), [box], cloud, cfg))
    assert any("quá giới hạn" in c for c in verify_fix(box, box.with_(scale=(3.0, 1.9, 1.6)), [box], cloud, cfg))


def test_ambiguous_new_class_is_not_verified():
    box = car(scale=(4.6, 1.9, 1.65))
    failed = verify_fix(box, box.with_(label="Truck"), [box], None, dict(DEFAULT_CONFIG))
    assert any("Car" in check for check in failed)


def test_change_kinds():
    box = car()
    assert change_kinds(box, box.with_(position=(10, 0, 1.0))) == ["ground"]
    assert change_kinds(box, box.with_(position=(10.3, 0, 0.8), scale=(4.0, 1.9, 1.6))) == ["fit"]
    assert change_kinds(box, box.with_(position=(10, 0, 0.75), scale=(4.5, 1.9, 1.5))) == ["ground"]
    assert change_kinds(box, box.with_(label="Truck")) == ["class"]


def _issue(uid, proposal, label="Car", **kw):
    return SimpleNamespace(id=hash(uid) % 1000, uid=uid, frame=0, label=label, proposal=proposal,
                           fix_status=kw.get("fix_status", "none"), status=kw.get("status", "open"))


def test_plan_writes_only_verified_allowed_in_place_fixes():
    ok = {"kind": "update", "changes": {"position": [1, 0, 0]}, "summary": "z", "verified": True,
          "kinds": ["ground"], "checks": []}
    issues = [
        _issue("s:1", ok),
        _issue("s:2", {**ok, "verified": False, "checks": ["sau sửa đè lên s:9"]}),
        _issue("s:3", {"kind": "delete", "summary": "xoá", "verified": False}),
        _issue("s:4", {**ok, "kinds": ["class"], "changes": {"label": "Truck"}}),
        _issue("t:5:0", {**ok, "kinds": ["class"], "changes": {"label": "Bus"}}),
        _issue("s:6", ok, label="Pedestrian"),
        _issue("s:7", {"kind": "update", "changes": {}, "summary": "old run"}),
    ]
    targets, skipped = plan_autofix(
        issues, {"Pedestrian"}, applied_status="applied", waived_status="waived",
        allowed_kinds={"ground", "fit", "orientation"},
    )
    assert [i.uid for i in targets] == ["s:1"]
    reasons = {s["uid"]: s["reason"] for s in skipped}
    assert "kiểm chứng" in reasons["s:2"] and reasons["s:2"] != reasons["s:7"]
    assert "xoá" in reasons["s:3"]
    assert "guideline" in reasons["s:4"]
    assert "track" in reasons["t:5:0"]
    assert "Pedestrian" in reasons["s:6"]


def test_plain_sentence_guideline_drives_qc_and_autofix():
    text = (
        "Ô tô con dài từ 3 đến 5,5 m, rộng 1,5-2,1 m.\n"
        "Đáy box lệch mặt đường không quá 10 cm.\n"
        "Không kiểm tra vận tốc.\n"
        "Không tự sửa người đi bộ.\n"
        "Autofix không đổi class.\n"
        "Xe tải thì hơi to.\n"
    )
    compiled = []
    content, warnings = parse_file("guide.md", text.encode("utf-8"), compiled)
    car_spec = next(c for c in content["classes"] if c["name"] == "Car")
    assert car_spec["size_min"][:2] == [3.0, 1.5] and car_spec["size_max"][:2] == [5.5, 2.1]
    assert content["thresholds"]["ground_tolerance"] == 0.1
    assert content["rules"]["R08"] is False
    assert content["autofix"]["exclude_classes"] == ["Pedestrian"]
    assert content["autofix"]["fix"]["class"] is False
    assert len(compiled) == 5
    assert any("bộ class giao thông VN mặc định" in w for w in warnings)
