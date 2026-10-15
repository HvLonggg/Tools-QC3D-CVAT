# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Measure QC recall on injected errors ("set A") using the converter output of phenikaa_to_cvat.py.

  python eval_injected.py --data D:/dataset/cvat/residential --seeds 10 --per-kind 5

Reports:
  - baseline: issues on the clean labels (natural findings, reviewed by people)
  - recall per error type: injected error caught by one of its expected rules
  - new false alarms: issues on untouched objects that were not in the baseline
"""

import argparse
import json
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cvat.apps.qc3d.qc.geometry import Cuboid, iou_3d  # noqa: E402

FIXABLE = ["shift", "loose", "float", "sink", "wrong_class", "duplicate"]
from cvat.apps.qc3d.qc.inject import EXPECTED_RULES, inject  # noqa: E402
from cvat.apps.qc3d.qc.rules import QcContext, run_rules  # noqa: E402


def load(data_dir: Path) -> tuple[list[Cuboid], dict[int, np.ndarray]]:
    with zipfile.ZipFile(data_dir / "annotations_datumaro3d.zip") as z:
        dataset = json.loads(z.read("annotations/default.json"))
    labels = [label["name"] for label in dataset["categories"]["label"]["labels"]]

    cuboids, point_clouds = [], {}
    with zipfile.ZipFile(data_dir / "pointclouds.zip") as z:
        names = sorted(z.namelist())
        for item in dataset["items"]:
            frame = names.index(f"{item['id']}.pcd")
            raw = z.read(f"{item['id']}.pcd")
            header_end = raw.index(b"DATA binary\n") + len(b"DATA binary\n")
            point_clouds[frame] = np.frombuffer(raw[header_end:], dtype="<f4").reshape(-1, 4)  # x y z intensity
            for ann in item["annotations"]:
                cuboids.append(
                    Cuboid(
                        uid=f"s:{frame}:{ann['id']}",
                        frame=frame,
                        label=labels[ann["label_id"]],
                        position=tuple(ann["position"]),
                        rotation=tuple(ann["rotation"]),
                        scale=tuple(ann["scale"]),
                    )
                )
    return cuboids, point_clouds


def issue_keys(results) -> set[tuple[str, str]]:
    return {(issue.rule, issue.uid) for result in results for issue in result.issues}


def apply_proposal(box: Cuboid, proposal: dict) -> Cuboid | None:
    if proposal["kind"] == "delete":
        return None
    changes = dict(proposal["changes"])
    for name in ("position", "rotation", "scale"):
        if name in changes:
            changes[name] = tuple(changes[name])
    return box.with_(**changes)


def load_detections(args, cuboids, point_clouds) -> dict:
    """Model detections per frame, cached next to the data (the model takes ~5 s per frame on a CPU)."""
    import pickle

    cache = args.data / f"detections_{args.model.stem}.pkl"
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    from cvat.apps.qc3d.qc.detector import Detector

    detector = Detector.from_file(args.model)
    bottoms = [c.position[2] - c.scale[2] / 2 for c in cuboids if c.distance < 30]
    ground = float(np.median(bottoms))
    detections = {}
    for n, (frame, points) in enumerate(sorted(point_clouds.items()), 1):
        points = points.copy()
        if points[:, 3].max(initial=0) > 255:  # LAZ intensity 0-65535 -> the model's 0-255
            points[:, 3] *= 255.0 / 65535.0
        detections[frame] = [
            ([float(v) for v in d.box], d.label, d.score) for d in detector.predict(points, ground_z=ground) if d.score >= 0.3
        ]
        print(f"  model {n}/{len(point_clouds)}", flush=True)
    cache.write_bytes(pickle.dumps(detections))
    return detections


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--per-kind", type=int, default=5)
    parser.add_argument("--model", type=Path, help="detector weights (.npz / .pth): also measure model-based fixes")
    args = parser.parse_args()

    cuboids, point_clouds = load(args.data)
    detections = load_detections(args, cuboids, point_clouds) if args.model else None
    point_clouds = {f: p[:, :3] for f, p in point_clouds.items()}
    baseline = run_rules(QcContext(cuboids, point_clouds, detections=detections))
    baseline_keys = issue_keys(baseline)

    print(f"objects: {len(cuboids)} | frames with point cloud: {len(point_clouds)}")
    print("\n== Baseline (clean labels)")
    for result in baseline:
        severities = Counter(i.severity for i in result.issues)
        print(f"  {result.rule}: {result.status:11s} issues={len(result.issues):3d} {dict(severities)} {result.reason}")

    caught, total = Counter(), Counter()
    false_alarms = defaultdict(int)
    missed_examples = defaultdict(list)
    originals = {c.uid: c for c in cuboids}
    fix_stats = defaultdict(lambda: {"proposals": 0, "iou_before": [], "iou_after": [], "label_ok": 0})
    # auto-fix writes only verified proposals: their quality, and what they would change on untouched objects
    auto_stats = defaultdict(lambda: {"verified": 0, "iou_before": [], "iou_after": [], "label_ok": 0})
    auto_untouched = Counter()
    auto_checks = Counter()
    for seed in range(args.seeds):
        boxes, errors = inject(cuboids, point_clouds, per_kind=args.per_kind, seed=seed)
        results = run_rules(QcContext(boxes, point_clouds, detections=detections))
        keys = issue_keys(results)
        current = {b.uid: b for b in boxes}
        proposals = defaultdict(list)
        for result in results:
            for issue in result.issues:
                if issue.proposal:
                    proposals[issue.uid].append((issue.rule, issue.proposal))
        for error in errors:
            if error.kind not in FIXABLE or error.uid not in proposals:
                continue
            expected = EXPECTED_RULES[error.kind]
            rule, proposal = sorted(proposals[error.uid], key=lambda p: p[0] not in expected)[0]
            stats = fix_stats[error.kind]
            stats["proposals"] += 1
            if error.kind == "duplicate":
                stats["label_ok"] += proposal["kind"] == "delete"
                continue
            fixed = apply_proposal(current[error.uid], proposal)
            original = originals[error.uid]
            if proposal.get("verified") and fixed:
                auto = auto_stats[error.kind]
                auto["verified"] += 1
                auto["iou_before"].append(iou_3d(current[error.uid], original))
                auto["iou_after"].append(iou_3d(fixed, original))
                auto["label_ok"] += fixed.label == original.label
                if auto["iou_after"][-1] < max(0.7, auto["iou_before"][-1]) or fixed.label != original.label:
                    auto.setdefault("bad", []).append(
                        f"seed {seed} {error.uid} ({error.detail}): IoU {auto['iou_before'][-1]:.2f} → "
                        f"{auto['iou_after'][-1]:.2f}, {proposal['summary']}, cách {original.distance:.0f} m"
                    )
            else:
                auto_checks.update(c.split(" ")[0] + " " + c.split(" ")[1] for c in proposal.get("checks", [])[:1])
            stats["iou_before"].append(iou_3d(current[error.uid], original))
            stats["iou_after"].append(iou_3d(fixed, original) if fixed else 0.0)
            stats["label_ok"] += bool(fixed) and fixed.label == original.label
            if fixed and fixed.label != original.label:
                stats.setdefault("label_wrong", []).append(
                    f"seed {seed} {error.uid}: {original.label} -> {current[error.uid].label} -> "
                    f"{fixed.label} (cao {original.scale[2]:.2f} m, cách {original.distance:.0f} m)"
                )
        injected_uids = {e.uid for e in errors} | {e.uid.split(":dup")[0] for e in errors}
        for error in errors:
            total[error.kind] += 1
            if any((code, error.uid) in keys for code in EXPECTED_RULES[error.kind]):
                caught[error.kind] += 1
            elif len(missed_examples[error.kind]) < 3:
                missed_examples[error.kind].append(f"seed {seed} {error.uid} ({error.detail})")
        for uid, items in proposals.items():
            if uid not in injected_uids and any(p.get("verified") for _, p in items):
                auto_untouched["objects"] += 1
        for code, uid in keys - baseline_keys:
            if uid not in injected_uids:
                false_alarms[code] += 1

    print(f"\n== Injected errors ({args.seeds} seeds × {args.per_kind}/type)")
    all_caught, all_total = sum(caught.values()), sum(total.values())
    for kind in EXPECTED_RULES:
        recall = caught[kind] / total[kind] if total[kind] else float("nan")
        print(f"  {kind:14s} recall {recall:6.1%} ({caught[kind]}/{total[kind]})  rules={sorted(EXPECTED_RULES[kind])}")
        for example in missed_examples[kind]:
            print(f"      missed: {example}")
    print(f"  {'TOTAL':14s} recall {all_caught / all_total:6.1%} ({all_caught}/{all_total})")
    print(f"  new false alarms on untouched objects: {dict(false_alarms) or 0}")

    print("\n== Fix proposals (applied, compared with the original box)")
    for kind in FIXABLE:
        s = fix_stats[kind]
        if kind == "duplicate":
            print(f"  {kind:14s} proposals {s['proposals']:3d}/{total[kind]}  delete ok {s['label_ok']}")
            continue
        if not s["iou_after"]:
            print(f"  {kind:14s} proposals   0/{total[kind]}")
            continue
        after = np.array(s["iou_after"])
        print(
            f"  {kind:14s} proposals {s['proposals']:3d}/{total[kind]}  IoU before {np.mean(s['iou_before']):.2f}"
            f" → after {after.mean():.2f} (median {np.median(after):.2f}, ≥0.7: {np.mean(after >= 0.7):.0%})"
            f"  class ok {s['label_ok']}/{s['proposals']}"
        )
        for line in s.get("label_wrong", []):
            print(f"      class sai: {line}")

    print("\n== Autofix (only verified proposals are written)")
    for kind in FIXABLE:
        if kind == "duplicate":
            continue
        a, s = auto_stats[kind], fix_stats[kind]
        if not a["iou_after"]:
            print(f"  {kind:14s} verified   0/{s['proposals']}")
            continue
        after, before = np.array(a["iou_after"]), np.array(a["iou_before"])
        print(
            f"  {kind:14s} verified {a['verified']:3d}/{s['proposals']}  IoU {before.mean():.2f} → {after.mean():.2f}"
            f" (≥0.7: {np.mean(after >= 0.7):.0%}, ≥0.8: {np.mean(after >= 0.8):.0%}, worse: {np.sum(after < before)})"
            f"  class ok {a['label_ok']}/{a['verified']}"
        )
        for line in a.get("bad", [])[:6]:
            print(f"      chưa tốt: {line}")
    print(f"  verified proposals on untouched objects (natural label findings): {auto_untouched['objects']}")
    print(f"  most common failed check of unverified proposals: {auto_checks.most_common(6)}")


if __name__ == "__main__":
    main()
