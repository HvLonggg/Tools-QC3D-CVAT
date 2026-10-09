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

from cvat.apps.qc3d.qc.geometry import Cuboid  # noqa: E402
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
            point_clouds[frame] = np.frombuffer(raw[header_end:], dtype="<f4").reshape(-1, 4)[:, :3]
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=10)
    parser.add_argument("--per-kind", type=int, default=5)
    args = parser.parse_args()

    cuboids, point_clouds = load(args.data)
    baseline = run_rules(QcContext(cuboids, point_clouds))
    baseline_keys = issue_keys(baseline)

    print(f"objects: {len(cuboids)} | frames with point cloud: {len(point_clouds)}")
    print("\n== Baseline (clean labels)")
    for result in baseline:
        severities = Counter(i.severity for i in result.issues)
        print(f"  {result.rule}: {result.status:11s} issues={len(result.issues):3d} {dict(severities)} {result.reason}")

    caught, total = Counter(), Counter()
    false_alarms = defaultdict(int)
    missed_examples = defaultdict(list)
    for seed in range(args.seeds):
        boxes, errors = inject(cuboids, point_clouds, per_kind=args.per_kind, seed=seed)
        results = run_rules(QcContext(boxes, point_clouds))
        keys = issue_keys(results)
        injected_uids = {e.uid for e in errors} | {e.uid.split(":dup")[0] for e in errors}
        for error in errors:
            total[error.kind] += 1
            if any((code, error.uid) in keys for code in EXPECTED_RULES[error.kind]):
                caught[error.kind] += 1
            elif len(missed_examples[error.kind]) < 3:
                missed_examples[error.kind].append(f"seed {seed} {error.uid} ({error.detail})")
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


if __name__ == "__main__":
    main()
