# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Object size over frames (R13), road plane, one detection per object and reviewer marks (R14), no Django:
  python -m pytest cvat/apps/qc3d/tests/test_objects.py
"""

import math

import numpy as np

from cvat.apps.qc3d.qc.fit import ground_plane
from cvat.apps.qc3d.qc.geometry import Cuboid
from cvat.apps.qc3d.qc.objects import extend_to, link_objects, reference_sizes
from cvat.apps.qc3d.qc.repair import dedupe_detections
from cvat.apps.qc3d.qc.rules import QcContext, run_rules
from cvat.apps.qc3d.qc.taxonomy import CLASSES
from cvat.apps.qc3d.tests.test_autofix import ground, proposals
from cvat.apps.qc3d.tests.test_rules import car, car_points


def drive(frames=5, step=1.0, short_frame=None):
    """Ego driving +x past 3 parked cars and a rider: objects move -step per frame in the sensor frame."""
    objects = [(12.0, 4.0, "Car", (4.5, 1.9, 1.6)), (18.0, -4.0, "Car", (4.6, 1.9, 1.5)),
               (25.0, 4.5, "Car", (4.4, 1.8, 1.6)), (15.0, 0.0, "Rider", (1.8, 0.7, 1.6))]
    boxes = []
    for f in range(frames):
        for n, (x, y, label, size) in enumerate(objects):
            scale = size
            if f == short_frame and n == 0:
                scale = (3.4, size[1], size[2])  # drawn on the visible part only
            boxes.append(Cuboid(f"s:{f}{n}", f, label, (x - step * f, y, size[2] / 2), (0, 0, 0.0), scale))
    return boxes


def test_boxes_of_one_object_are_linked_across_frames():
    links = link_objects(drive())
    assert len({links[f"s:{f}0"] for f in range(5)}) == 1
    assert len(set(links.values())) == 4


def test_reference_size_and_short_box_issue():
    boxes = drive(short_frame=2)
    reference = reference_sizes(boxes, CLASSES)
    assert math.isclose(reference["s:20"]["size"][0], 4.5, abs_tol=1e-6)
    found = [i for r in run_rules(QcContext(boxes, {}), ["R13"]) for i in r.issues]
    assert [i.uid for i in found] == ["s:20"]
    assert math.isclose(found[0].proposal["changes"]["scale"][0], 4.5, abs_tol=1e-3)


def test_extension_keeps_the_face_the_lidar_sees():
    short = car(x=10.0, scale=(3.4, 1.9, 1.6))  # sensor at the origin looks at the -x face
    longer = extend_to(short, (4.5, 1.9, 1.6))
    near_before = short.position[0] - short.scale[0] / 2
    near_after = longer.position[0] - longer.scale[0] / 2
    assert math.isclose(near_before, near_after, abs_tol=1e-6) and longer.scale[0] == 4.5
    assert extend_to(longer, (3.0, 1.0, 1.0)).scale == longer.scale  # never shrinks


def test_no_fix_makes_a_box_shorter_than_its_object():
    boxes = drive(short_frame=None)
    loose = boxes[5].with_(scale=(5.6, 1.9, 1.6))  # frame 1, car 0: loose box
    boxes[5] = loose
    cloud = np.concatenate([car_points(boxes[0].with_(position=loose.position)), ground(x=(0, 30))])
    fix = proposals(run_rules(QcContext(boxes, {1: cloud}), ["R10"])).get(loose.uid)
    if fix and "scale" in fix["changes"]:
        assert fix["changes"]["scale"][0] >= 4.5 - 1e-3


def test_road_plane_flat_and_slope():
    box = car(x=10.0)
    road = ground(x=(4, 16), y=(-4, 4))
    z, flat = ground_plane(road, box)
    assert flat and abs(z) < 0.03
    slope = road.copy()
    slope[:, 2] = 0.2 * (slope[:, 0] - 10.0)  # 11 deg ramp
    _, flat = ground_plane(slope, box)
    assert not flat


def test_no_ground_fix_on_a_ramp():
    box = car()
    floating = box.with_(position=(10.0, 0.0, 1.6))
    road = ground()
    road[:, 2] = 0.2 * (road[:, 0] - 10.0)
    results = run_rules(QcContext([floating], {0: np.concatenate([car_points(box), road])}), ["R09"])
    issues = [i for r in results for i in r.issues if i.rule == "R09"]
    assert all(i.proposal is None for i in issues)


def test_one_detection_per_object():
    rider = ([10.0, 0.0, 0.8, 1.8, 0.7, 1.6, 0.0], "Rider", 0.7)
    person = ([10.1, 0.05, 0.9, 0.6, 0.6, 1.7, 0.0], "Pedestrian", 0.5)
    neighbour = ([10.0, 1.0, 0.8, 1.8, 0.7, 1.6, 0.0], "Rider", 0.6)
    assert dedupe_detections([rider, person, neighbour]) == [rider, neighbour]


def test_marked_box_gets_a_model_fix():
    box = car()
    drawn = box.with_(position=(10.6, 0.3, 0.8), scale=(4.0, 1.7, 1.6))
    cloud = np.concatenate([car_points(box), ground()])
    detections = {0: [([*box.position, *box.scale, 0.0], "Car", 0.9)]}
    ctx = QcContext([drawn], {0: cloud}, detections=detections, marked={drawn.uid})
    issues = [i for r in run_rules(ctx) for i in r.issues if i.uid == drawn.uid]
    assert any(i.rule == "R14" for i in issues)
    proposal = next(i.proposal for i in issues if i.rule == "R14")
    assert proposal and proposal["verified"]
    assert math.dist(proposal["changes"]["position"][:2], box.position[:2]) < 0.2
