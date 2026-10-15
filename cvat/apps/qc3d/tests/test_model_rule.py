# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""QC rule R12 (labels vs detection model) and the numpy detector helpers (no Django, no weights)."""

import numpy as np

from cvat.apps.qc3d.qc.detector import rotated_nms, split_group
from cvat.apps.qc3d.qc.rules import DEFAULT_CONFIG as DEFAULT
from cvat.apps.qc3d.qc.rules import QcContext, run_rules
from cvat.apps.qc3d.qc.taxonomy import CLASSES
from cvat.apps.qc3d.tests.test_rules import car


def r12(boxes, detections):
    result = next(r for r in run_rules(QcContext(boxes, {}, detections=detections), ["R12"]))
    return result


def det(x, y, label="Car", score=0.9, size=(4.5, 1.9, 1.6)):
    return ([x, y, 0.8, *size, 0.0], label, score)


def test_not_checked_without_model_run():
    assert r12([car()], None).status == "not_checked"


def test_confident_detection_without_label_is_missing_label():
    found = r12([car()], {0: [det(10, 0), det(20, 5, score=0.95)]}).issues
    assert len(found) == 1 and found[0].uid.startswith("m:0:20:5") and found[0].proposal is None


def test_mostly_unlabeled_frame_gives_one_issue():
    detections = {0: [det(-24 + 6 * i, 12) for i in range(9)]}  # all within 40 m
    found = r12([car()], detections).issues
    assert len(found) == 1 and found[0].uid == "m:0:frame" and found[0].evidence["missing"] == 9


def test_low_score_far_or_untrusted_detections_are_ignored():
    detections = {0: [det(20, 5, score=0.4), det(45, 0), det(20, 5, label="Pedestrian", size=(0.6, 0.6, 1.7))]}
    assert r12([car()], detections).issues == []


def test_class_disagreement_on_a_labeled_object():
    rider = car().with_(label="Car", scale=(1.9, 0.8, 1.6))
    found = r12([rider], {0: [det(10, 0, label="Rider", score=0.8, size=(1.9, 0.8, 1.6))]}).issues
    assert len(found) == 1 and found[0].uid == rider.uid and found[0].proposal["changes"]["label"] == "Rider"


def test_no_disagreement_when_the_model_also_sees_the_labeled_class():
    detections = {0: [det(10, 0, label="Rider", score=0.6), det(10.2, 0, label="Car", score=0.5)]}
    assert r12([car()], detections).issues == []


def test_rotated_nms_keeps_the_best_of_overlapping_boxes():
    boxes = np.array([[0, 0, 0, 4, 2, 1.5, 0], [0.2, 0, 0, 4, 2, 1.5, 0.05], [10, 0, 0, 4, 2, 1.5, 0]], float)
    assert rotated_nms(boxes, np.array([0.5, 0.9, 0.7]), 0.2) == [1, 2]


def test_v2_groups_are_split_with_the_guideline_rules():
    assert split_group("LargeVehicle", [0, 0, 0, 6.5, 2.4, 2.9, 0], CLASSES) == "Truck"
    assert split_group("LargeVehicle", [0, 0, 0, 11.0, 2.55, 3.3, 0], CLASSES) == "Bus"
    assert split_group("TwoWheeler", [0, 0, 0, 1.9, 0.7, 1.6, 0], CLASSES) == "Rider"
    assert split_group("TwoWheeler", [0, 0, 0, 1.9, 0.6, 1.1, 0], CLASSES) == "Motorbike"
    assert split_group("Car", [0, 0, 0, 4.5, 1.9, 1.6, 0], CLASSES) == "Car"


def _scene():
    """A car on the road, its points and a flat ground around it."""
    from cvat.apps.qc3d.tests.test_autofix import ground
    from cvat.apps.qc3d.tests.test_rules import car_points

    truth = car()
    return truth, np.concatenate([car_points(truth), ground()])


def test_badly_drawn_box_is_rebuilt_from_the_model_with_the_guideline_class():
    from cvat.apps.qc3d.qc.geometry import iou_3d
    from cvat.apps.qc3d.qc.verify import apply_changes

    truth, cloud = _scene()
    wrong = truth.with_(label="Pedestrian", position=(10.3, 0.2, 2.5), scale=(2.7, 2.4, 1.0))  # junk box, wrong class
    detections = {0: [(list(truth.position) + list(truth.scale) + [truth.yaw + np.pi], "Car", 0.9)]}
    issues = [i for r in run_rules(QcContext([wrong], {0: cloud}, detections=detections)) for i in r.issues]
    proposals = {repr(i.proposal) for i in issues if i.proposal}
    assert len(proposals) == 1, "one complete correction for the object"
    proposal = next(i.proposal for i in issues if i.proposal)
    fixed = apply_changes(wrong, proposal["changes"])
    assert fixed.label == "Car" and proposal["method"] == "model"
    assert iou_3d(fixed, truth) > 0.8
    assert abs(fixed.yaw - wrong.yaw) < 0.1, "keeps the label heading when the model is 180 deg off"


def test_unlabeled_object_gets_a_create_proposal():
    truth, cloud = _scene()
    detections = {0: [(list(truth.position) + list(truth.scale) + [truth.yaw], "Car", 0.9)]}
    found = [i for r in run_rules(QcContext([], {0: cloud}, detections=detections)) for i in r.issues if i.rule == "R12"]
    assert len(found) == 1 and found[0].proposal["kind"] == "create"
    shape = found[0].proposal["shapes"][0]
    assert shape["label"] == "Car" and found[0].proposal["verified"]
    assert abs((shape["points"][2] - shape["points"][8] / 2) - 0.0) < 0.15, "bottom on the road"


def test_new_box_overlapping_a_label_is_refused():
    from cvat.apps.qc3d.qc.repair import new_box, verify_create

    truth, cloud = _scene()
    box = new_box((list(truth.position) + list(truth.scale) + [0.0], "Car", 0.9), 0, "m:0:1", cloud, dict(DEFAULT))
    assert any("đè lên" in c for c in verify_create(box, [truth], cloud, dict(DEFAULT)))
