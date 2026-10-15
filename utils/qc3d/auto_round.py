# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
One unattended training round, after a Colab run: wait for its results on Google Drive, download them,
score the new model against the installed one on the held-out PHENIKAA scenes and install it in CVAT only
if it is better. Writes a Markdown report.

  python auto_round.py --drive-dir qc3d/results --key vn-cp-pillar-v2 --config v2 \
      --out C:/dataset/round_v2 --baseline D:/dataset/results_colab/eval/eval_with_train/epoch_20/val/result.pkl

Google Drive is read with rclone (remote "gdrive", read only). Nothing on Drive is changed.
"""

from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import eval_predictions as E  # noqa: E402

RCLONE = r"C:\Users\HoangVanLong\tools\rclone\rclone.exe"
LABELS = Path("D:/dataset/train_vn_v3/labels")
GROUP_CLASSES = ["Car", "LargeVehicle", "TwoWheeler", "Pedestrian"]


def drive_files(folder: str) -> dict[str, int]:
    out = subprocess.run([RCLONE, "lsl", f"gdrive:{folder}"], capture_output=True, text=True)
    files = {}
    for line in out.stdout.splitlines():
        parts = line.split(maxsplit=3)
        if len(parts) == 4:
            files[parts[3]] = int(parts[0])
    return files


def frames_of(result: Path, source: str) -> list:
    annos = [a for a in pickle.loads(result.read_bytes()) if str(a["frame_id"]).startswith(f"{source}_")]
    frames = []
    for anno in annos:
        gt, names = E.read_labels(LABELS / f"{anno['frame_id']}.txt")
        names = [E.GROUPS.get(n, n) for n in names]
        import numpy as np

        pred = np.asarray(anno["boxes_lidar"], dtype=np.float64).reshape(-1, 7)
        frames.append((gt, names, pred, [E.GROUPS.get(str(n), str(n)) for n in anno["name"]],
                       np.asarray(anno["score"], dtype=np.float64)))
    return frames


def score(result: Path) -> dict:
    """Per source and group: AP (2 m) over all ranges, and the 30 m precision-99 % target."""
    summary = {}
    for source in ("phx", "nus"):
        frames = frames_of(result, source)
        for cls in GROUP_CLASSES:
            target = E.near_target(frames, cls, 30.0)
            summary[f"{source}:{cls}"] = {
                "ap_2m": round(E.average_precision(frames, cls, 2.0) * 100, 1),
                "p30": round(float(target.get("p03", float("nan"))) * 100, 1) if target else None,
                "r30": round(float(target.get("r03", float("nan"))) * 100, 1) if target else None,
                "r30_at_p99": round(float(target.get("goal_recall", 0.0)) * 100, 1) if target else None,
            }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--drive-dir", default="qc3d/results")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--key", default="vn-cp-pillar-v2")
    parser.add_argument("--config", default="v2")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True, help="result.pkl of the installed model")
    parser.add_argument("--poll-minutes", type=float, default=10)
    parser.add_argument("--max-hours", type=float, default=12)
    parser.add_argument("--local", action="store_true",
                        help="results already downloaded into --out (e.g. a Kaggle run): skip Google Drive")
    args = parser.parse_args()

    wanted = [f"epoch_{args.epochs}_result.pkl", f"checkpoint_epoch_{args.epochs}.pth"]
    deadline = time.time() + args.max_hours * 3600
    while not args.local:
        files = drive_files(args.drive_dir)
        if all(name in files for name in wanted):
            break
        if time.time() > deadline:
            raise SystemExit(f"Hết {args.max_hours} giờ mà Drive chưa có {wanted}")
        print(time.strftime("%H:%M"), "chưa có kết quả trên Drive, chờ tiếp", flush=True)
        time.sleep(args.poll_minutes * 60)

    args.out.mkdir(parents=True, exist_ok=True)
    if not args.local:
        subprocess.run([RCLONE, "copy", f"gdrive:{args.drive_dir}", str(args.out)], check=True)
    result, checkpoint = args.out / wanted[0], args.out / wanted[1]
    new, old = score(result), score(args.baseline)

    lines = [f"# Vòng train {args.key} ({time.strftime('%Y-%m-%d %H:%M')})", "",
             "| Nguồn:nhóm | AP 2 m mới (cũ) | ≤30 m đúng / phủ mới (cũ) | ≤30 m phủ khi đúng 99% mới (cũ) |",
             "| --- | --- | --- | --- |"]
    better = 0
    for key in new:
        n, o = new[key], old[key]
        lines.append(f"| {key} | {n['ap_2m']} ({o['ap_2m']}) | {n['p30']}/{n['r30']} ({o['p30']}/{o['r30']}) | "
                     f"{n['r30_at_p99']} ({o['r30_at_p99']}) |")
        if key.startswith("phx:") and key != "phx:Pedestrian":
            better += (n["ap_2m"] or 0) - (o["ap_2m"] or 0)
    install = better > 0
    lines += ["", f"Tổng chênh AP (PHENIKAA, Car + xe lớn + xe 2 bánh): {better:+.1f} → "
              + ("cài model mới vào CVAT" if install else "giữ model cũ")]
    if install:
        subprocess.run([sys.executable, str(HERE / "install_model.py"), "--pth", str(checkpoint), "--key", args.key],
                       check=True)
        subprocess.run(["docker", "restart", "cvat_server"], check=False)
    report = args.out / "round_report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (args.out / "round_scores.json").write_text(json.dumps({"new": new, "old": old}, indent=1), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
