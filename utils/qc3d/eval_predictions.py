# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Diagnose an OpenPCDet result.pkl against the labels of the training set built by build_train_set.py.

Separates "not enough data" from "systematic bug" by measuring, per class:
  - recall / precision by distance (matching by BEV centre distance, like nuScenes),
  - AP by centre distance (0.5, 1, 2, 4 m) - meaningful even when boxes are not tight yet,
  - matched-pair errors: centre xy, z, size ratio, heading (and how many are flipped by ~180 deg),
  - 3D IoU of matched pairs.
A systematic bug shows as a biased error (e.g. median z offset, size ratio far from 1, many 90/180 deg
heading errors); lack of data shows as unbiased but noisy errors and low recall on rare classes.

Usage:
  python eval_predictions.py --result epoch_40_result.pkl --labels D:/dataset/train_vn/labels
"""

from __future__ import annotations

import argparse
import math
import pickle
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cvat.apps.qc3d.qc.geometry import Cuboid, iou_3d  # noqa: E402

DIST_BINS = [(0, 20), (20, 40), (40, 60)]
AP_THRESHOLDS = [0.5, 1.0, 2.0, 4.0]


def read_labels(path: Path) -> tuple[np.ndarray, list[str]]:
    boxes, names = [], []
    if path.exists():
        for line in path.read_text().splitlines():
            parts = line.split()
            if len(parts) == 8:
                boxes.append([float(v) for v in parts[:7]])
                names.append(parts[7])
    return np.asarray(boxes, dtype=np.float64).reshape(-1, 7), names


def wrap(angle: float) -> float:
    return (angle + math.pi) % (2 * math.pi) - math.pi


def cuboid(box, uid) -> Cuboid:
    x, y, z, dx, dy, dz, yaw = (float(v) for v in box[:7])
    return Cuboid(uid=uid, frame=0, label="", position=(x, y, z), rotation=(0, 0, yaw), scale=(dx, dy, dz))


def match(gt: np.ndarray, pred: np.ndarray, scores: np.ndarray, threshold: float) -> list[tuple[int, int]]:
    """Greedy by score: each prediction takes the closest free GT within threshold (BEV centre distance)."""
    pairs, taken = [], set()
    for p in np.argsort(-scores):
        if len(gt) == 0:
            break
        d = np.hypot(gt[:, 0] - pred[p, 0], gt[:, 1] - pred[p, 1])
        for g in np.argsort(d):
            if d[g] > threshold:
                break
            if g not in taken:
                taken.add(g)
                pairs.append((int(g), int(p)))
                break
    return pairs


def average_precision(frames, cls: str, threshold: float) -> float:
    records, total_gt = [], 0
    for gt, gt_names, pred, pred_names, scores in frames:
        g = gt[[n == cls for n in gt_names]] if len(gt) else gt
        mask = np.array([n == cls for n in pred_names], dtype=bool)
        p, s = pred[mask], scores[mask]
        total_gt += len(g)
        matched = {pi for _, pi in match(g, p, s, threshold)}
        records += [(float(s[i]), i in matched) for i in range(len(p))]
    if total_gt == 0 or not records:
        return float("nan")
    records.sort(key=lambda r: -r[0])
    tp = np.cumsum([r[1] for r in records])
    precision = tp / np.arange(1, len(records) + 1)
    recall = tp / total_gt
    # 101-point interpolated AP (like the KITTI R40 / nuScenes style)
    return float(np.mean([precision[recall >= r].max() if (recall >= r).any() else 0.0
                          for r in np.linspace(0, 1, 101)]))


def near_target(frames, cls: str, max_range: float, goal: float = 0.99) -> dict:
    """
    Within `max_range` m: precision / recall at every score threshold (match within 2 m), the threshold where
    precision first reaches `goal` and the recall left there (how far the model is from the 99 % target).
    """
    records, total_gt = [], 0
    for gt, gt_names, pred, pred_names, scores in frames:
        g = gt[[n == cls for n in gt_names]] if len(gt) else gt
        g = g[np.hypot(g[:, 0], g[:, 1]) < max_range] if len(g) else g
        mask = np.array([n == cls for n in pred_names], dtype=bool)
        p, sc = pred[mask], scores[mask]
        near = np.hypot(p[:, 0], p[:, 1]) < max_range if len(p) else np.zeros(0, bool)
        p, sc = p[near], sc[near]
        total_gt += len(g)
        matched = {pi for _, pi in match(g, p, sc, 2.0)}
        records += [(float(sc[i]), i in matched) for i in range(len(p))]
    if not total_gt or not records:
        return {}
    records.sort(key=lambda r: -r[0])
    tp = np.cumsum([r[1] for r in records])
    precision = tp / np.arange(1, len(records) + 1)
    recall = tp / total_gt
    scores = np.array([r[0] for r in records])
    at = int(np.searchsorted(-scores, -0.3, side="right")) - 1
    ok = np.flatnonzero(precision >= goal)
    best = int(ok[np.argmax(recall[ok])]) if len(ok) else None
    return {
        "n": total_gt,
        "p03": precision[at] if at >= 0 else float("nan"), "r03": recall[at] if at >= 0 else float("nan"),
        "goal_score": scores[best] if best is not None else None,
        "goal_recall": recall[best] if best is not None else 0.0,
    }


GROUPS = {"Truck": "LargeVehicle", "Bus": "LargeVehicle", "Rider": "TwoWheeler", "Motorbike": "TwoWheeler"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--score", type=float, default=0.3, help="score threshold for recall / precision / errors")
    parser.add_argument("--source", choices=["phx", "nus"],
                        help="only frames of one source: phx (PHENIKAA, VN roads) or nus (nuScenes)")
    parser.add_argument("--target-range", type=float, default=30.0,
                        help="range of the 99 %% precision target (m)")
    parser.add_argument("--groups", action="store_true",
                        help="compare the v2 groups: labels Truck/Bus -> LargeVehicle, Rider/Motorbike -> TwoWheeler")
    args = parser.parse_args()
    if args.groups:
        global read_labels
        plain = read_labels

        def read_labels(path):  # noqa: F811
            boxes, names = plain(path)
            return boxes, [GROUPS.get(n, n) for n in names]

    with open(args.result, "rb") as f:
        annos = pickle.load(f)
    if args.source:
        annos = [a for a in annos if str(a["frame_id"]).startswith(f"{args.source}_")]
    if args.groups:  # v1 predictions use the guideline classes: compare them as groups too
        for anno in annos:
            anno["name"] = [GROUPS.get(str(n), str(n)) for n in anno["name"]]

    frames = []
    for anno in annos:
        gt, gt_names = read_labels(args.labels / f"{anno['frame_id']}.txt")
        pred = np.asarray(anno["boxes_lidar"], dtype=np.float64).reshape(-1, 7)
        frames.append((gt, gt_names, pred, list(anno["name"]), np.asarray(anno["score"], dtype=np.float64)))
    classes = sorted({n for f in frames for n in f[1]})
    print(f"{len(frames)} frames, {sum(len(f[0]) for f in frames)} GT boxes, "
          f"{sum(len(f[2]) for f in frames)} predictions\n")

    print("AP by BEV centre distance (higher is better)")
    print(f"{'class':12s}" + "".join(f"{f'@{t}m':>9s}" for t in AP_THRESHOLDS))
    for cls in classes:
        print(f"{cls:12s}" + "".join(f"{average_precision(frames, cls, t) * 100:9.1f}" for t in AP_THRESHOLDS))

    print(f"\nRecall / precision at score >= {args.score}, match within 2 m, by distance")
    errors = defaultdict(lambda: defaultdict(list))
    for cls in classes:
        row = []
        for lo, hi in DIST_BINS:
            tp = n_gt = n_pred = 0
            for gt, gt_names, pred, pred_names, scores in frames:
                g_mask = np.array([n == cls for n in gt_names], dtype=bool)
                p_mask = np.array([n == cls for n in pred_names], dtype=bool) & (scores >= args.score)
                g, p, s = gt[g_mask] if len(gt) else gt, pred[p_mask], scores[p_mask]
                g_in = (np.hypot(g[:, 0], g[:, 1]) >= lo) & (np.hypot(g[:, 0], g[:, 1]) < hi) if len(g) else []
                p_in = (np.hypot(p[:, 0], p[:, 1]) >= lo) & (np.hypot(p[:, 0], p[:, 1]) < hi)
                pairs = match(g, p, s, 2.0)
                tp += sum(1 for gi, _ in pairs if g_in[gi])
                n_gt += int(np.sum(g_in))
                n_pred += int(np.sum(p_in))
                if lo == 0:  # errors over all distances, collected once
                    for gi, pi in pairs:
                        gb, pb = g[gi], p[pi]
                        e = errors[cls]
                        e["xy"].append(math.hypot(pb[0] - gb[0], pb[1] - gb[1]))
                        e["dz"].append(pb[2] - gb[2])
                        e["ratio_l"].append(pb[3] / gb[3])
                        e["ratio_w"].append(pb[4] / gb[4])
                        e["ratio_h"].append(pb[5] / gb[5])
                        e["yaw"].append(abs(wrap(pb[6] - gb[6])))
                        e["iou"].append(iou_3d(cuboid(gb, "g"), cuboid(pb, "p")))
            recall = tp / n_gt if n_gt else float("nan")
            precision = min(tp, n_pred) / n_pred if n_pred else float("nan")
            row.append(f"{lo}-{hi}m R {recall * 100:5.1f}% P {precision * 100:5.1f}% (n={n_gt})")
        print(f"{cls:12s}" + " | ".join(row))

    print(f"\nTarget within {args.target_range:g} m (goal: precision 99 %, match within 2 m)")
    for cls in classes:
        t = near_target(frames, cls, args.target_range)
        if not t:
            continue
        goal = (f"precision 99 % at score >= {t['goal_score']:.2f} keeps recall {t['goal_recall'] * 100:5.1f}%"
                if t["goal_score"] is not None else "precision 99 % not reached")
        print(f"{cls:12s} n={t['n']:5d} | score>=0.3: P {t['p03'] * 100:5.1f}% R {t['r03'] * 100:5.1f}% | {goal}")

    print("\nErrors of matched pairs (median; a bias != 0 or ratio != 1 means a systematic problem)")
    print(f"{'class':12s}{'n':>6s}{'xy m':>8s}{'dz m':>8s}{'L':>7s}{'W':>7s}{'H':>7s}"
          f"{'yaw°':>7s}{'>150°':>7s}{'60-120°':>9s}{'IoU3D':>7s}{'IoU>.5':>8s}")
    for cls in classes:
        e = errors[cls]
        if not e["xy"]:
            print(f"{cls:12s}{0:6d}")
            continue
        yaw = np.degrees(e["yaw"])
        iou = np.asarray(e["iou"])
        print(f"{cls:12s}{len(e['xy']):6d}{np.median(e['xy']):8.2f}{np.median(e['dz']):+8.2f}"
              f"{np.median(e['ratio_l']):7.2f}{np.median(e['ratio_w']):7.2f}{np.median(e['ratio_h']):7.2f}"
              f"{np.median(yaw):7.1f}{np.mean(yaw > 150) * 100:6.0f}%{np.mean((yaw > 60) & (yaw < 120)) * 100:8.0f}%"
              f"{np.median(iou):7.2f}{np.mean(iou > 0.5) * 100:7.0f}%")


if __name__ == "__main__":
    main()
