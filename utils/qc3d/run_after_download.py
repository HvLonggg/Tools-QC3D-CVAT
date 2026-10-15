# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Unattended steps after the PHENIKAA Mini download (run by overnight.ps1), each guarded so one failure
does not stop the others. Writes a Markdown report.

  1. verify the download against the counts listed on Google Drive
  2. build the training set (PHENIKAA + nuScenes-mini, VN classes) and check its geometry
  3. QC 3D rules on a sample of PHENIKAA labels (label quality before training)
  4. zip the training set for a Kaggle upload (POSIX paths)

Usage:
  python run_after_download.py --phenikaa D:/dataset/phenikaa_mini --nuscenes D:/dataset/nuscenes \
      --out D:/dataset/train_vn_v3 --zip D:/dataset/train_vn_kaggle_v3.zip --report D:/dataset/pipeline_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))

# files listed on Google Drive on 2026-10-12 (rclone size): lidar / label / ego_pose per scene
EXPECTED = {
    "COMPLEX CITY": (200, 200, 200), "COMPLEX URBAN": (200, 200, 200), "NIGHT TIME": (200, 200, 200),
    "NIGHT TIME ON BRIDGE": (200, 200, 200), "PARKING AREA": (0, 200, 200), "RAINY DAY": (200, 200, 200),
    "RAINY IN INTERSECTION": (77, 200, 200), "RESIDENTIAL AREA": (201, 200, 200), "ROUNDABOUT": (0, 200, 200),
    "RUST HOUR": (66, 200, 200), "SUPER CROWED": (0, 200, 200), "TWILIGHT ON BRIDGE": (200, 200, 200),
}
# held-out PHENIKAA scenes: one day, one night (never trained on)
VAL_SCENES = ["COMPLEX URBAN", "NIGHT TIME ON BRIDGE"]


class Report:
    def __init__(self, path: Path):
        self.path = path
        self.lines = [f"# Báo cáo xử lý dữ liệu PHENIKAA ({time.strftime('%Y-%m-%d %H:%M')})", ""]

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)
        self.path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
        for line in lines:
            print(line, flush=True)


def step(report: Report, title: str):
    def wrap(func):
        def run(*args, **kwargs):
            report.add("", f"## {title}", "")
            started = time.time()
            try:
                result = func(*args, **kwargs)
                report.add("", f"_Xong trong {time.time() - started:.0f} s._")
                return result
            except Exception:
                report.add("", "**LỖI**, bước sau vẫn chạy tiếp:", "", "```", traceback.format_exc()[-3000:], "```")
                return None
        return run
    return wrap


def verify(report: Report, root: Path) -> dict:
    report.add("| Scene | LiDAR | Nhãn | Ego-pose | Frame dùng được (có cả LiDAR và nhãn) |", "| --- | --- | --- | --- | --- |")
    usable_total, problems = 0, []
    for scene, (lidar_n, label_n, pose_n) in EXPECTED.items():
        folder = root / scene
        lidar = {p.stem for p in (folder / "lidar").glob("*.laz")}
        labels = {p.stem for p in (folder / "label").glob("*.txt")}
        poses = list((folder / "ego_pose").glob("*.txt"))
        usable = len(lidar & labels)
        usable_total += usable
        report.add(f"| {scene} | {len(lidar)}/{lidar_n} | {len(labels)}/{label_n} | {len(poses)}/{pose_n} | {usable} |")
        if len(lidar) < lidar_n or len(labels) < label_n or len(poses) < pose_n:
            problems.append(scene)
    report.add("", f"**Tổng frame dùng được: {usable_total}.** "
               + ("Tải đủ như trên Drive." if not problems else f"Thiếu file ở: {', '.join(problems)}."),
               "PARKING AREA, ROUNDABOUT, SUPER CROWED không có LiDAR ngay trên Drive gốc (lỗi phía PHENIKAA).")
    return {"usable": usable_total, "problems": problems}


def build(report: Report, phenikaa: Path, nuscenes: Path, out: Path) -> dict:
    from build_train_set import build as build_set

    if out.exists():
        import shutil
        shutil.rmtree(out)
    args = argparse.Namespace(out=out, phenikaa=phenikaa, phenikaa_val=VAL_SCENES, nuscenes=nuscenes,
                              nuscenes_version="v1.0-mini", max_range=54.0, keep_empty=False)
    result = build_set(args)
    classes = sorted({c for counts in result["boxes"].values() for c in counts})
    report.add(f"Tập kiểm định PHENIKAA (không dùng để huấn luyện): {', '.join(VAL_SCENES)}.", "",
               "| Nguồn / tập | Frame | " + " | ".join(classes) + " |",
               "| --- | --- | " + " | ".join("---" for _ in classes) + " |")
    for key in sorted(result["boxes"]):
        counts = result["boxes"][key]
        report.add(f"| {key} | {result['frames'].get(key, 0)} | " + " | ".join(str(counts.get(c, 0)) for c in classes) + " |")
    report.add("", f"Dịch mặt đường về z = -1,8 m: {json.dumps(result['ground_shift'])}")
    relabeled = result.get("relabeled", {})
    invalid = {k: v for k, v in result["dropped"].items() if "invalid" in k}
    report.add(f"- Đổi nhãn theo kích thước (PHENIKAA không có Truck/Bus): {json.dumps(relabeled, ensure_ascii=False)}",
               f"- Bỏ box hỏng (kích thước ≤ 0 / NaN): {json.dumps(invalid, ensure_ascii=False)}")

    # geometry check: box bottoms per source must sit on the common ground (-1.8 m)
    for prefix in ("phx_", "nus_"):
        bottoms = [float(line.split()[2]) - float(line.split()[5]) / 2
                   for f in (out / "labels").glob(f"{prefix}*.txt") for line in f.read_text().splitlines() if line]
        if bottoms:
            report.add(f"- Đáy box {prefix[:-1]}: trung vị {np.median(bottoms):+.2f} m (mục tiêu -1,80)")
    return result


def qc_labels(report: Report, phenikaa: Path, stride: int, max_minutes: float) -> None:
    from build_train_set import read_laz
    from cvat.apps.qc3d.qc.geometry import Cuboid
    from cvat.apps.qc3d.qc.guideline import qc_config
    from cvat.apps.qc3d.qc.rules import QcContext, run_rules

    config = qc_config(None)
    by_rule, by_class, examples = Counter(), defaultdict(Counter), defaultdict(list)
    frames = boxes_total = 0
    deadline = time.time() + max_minutes * 60
    pairs = []
    for scene in EXPECTED:
        folder = phenikaa / scene
        stems = sorted({p.stem for p in (folder / "lidar").glob("*.laz")} & {p.stem for p in (folder / "label").glob("*.txt")})
        pairs += [(scene, s) for s in stems[::stride]]
    for scene, stem in pairs:
        if time.time() > deadline:
            report.add(f"_Dừng QC vì hết thời gian cho phép ({max_minutes:.0f} phút)._")
            break
        points = read_laz(phenikaa / scene / "lidar" / f"{stem}.laz")[:, :3]
        boxes = []
        for i, line in enumerate((phenikaa / scene / "label" / f"{stem}.txt").read_text().splitlines()):
            p = line.split()
            if len(p) != 8:
                continue
            x, y, z, length, width, height, yaw = map(float, p[:7])
            boxes.append(Cuboid(uid=f"s:{i}", frame=0, label=p[7], position=(x, y, z), rotation=(0, 0, yaw),
                                scale=(length, width, height)))
        frames += 1
        boxes_total += len(boxes)
        for result in run_rules(QcContext(boxes, {0: points}, config=config)):
            for issue in result.issues:
                by_rule[issue.rule] += 1
                label = boxes[int(issue.uid[2:])].label
                by_class[issue.rule][label] += 1
                if len(examples[issue.rule]) < 3:
                    examples[issue.rule].append(f"{scene}/{stem} {label}: {issue.message}")
    report.add(f"Đã kiểm {frames} frame (cứ {stride} frame lấy 1), {boxes_total} box.", "",
               "| Rule | Vấn đề | % số box | Theo class | Ví dụ |", "| --- | --- | --- | --- | --- |")
    for rule, count in sorted(by_rule.items()):
        share = 100 * count / max(boxes_total, 1)
        classes = ", ".join(f"{c} {n}" for c, n in by_class[rule].most_common())
        report.add(f"| {rule} | {count} | {share:.1f}% | {classes} | {'<br>'.join(examples[rule])} |")
    report.add("", "Ghi chú: R05 (ít điểm LiDAR) thường do vật ở xa hoặc bị che, không hẳn là lỗi gán. "
               "R09/R10/R11 là các lỗi nên xem lại trước khi huấn luyện.")


def make_zip(report: Report, folder: Path, target: Path) -> None:
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(folder).as_posix())
    names = zipfile.ZipFile(target).namelist()
    report.add(f"`{target}`: {len(names)} file, {target.stat().st_size / 2**20:.0f} MB, "
               f"đường dẫn kiểu POSIX: {all(chr(92) not in n for n in names)}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phenikaa", type=Path, required=True)
    parser.add_argument("--nuscenes", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--qc-stride", type=int, default=10)
    parser.add_argument("--qc-minutes", type=float, default=40)
    parser.add_argument("--skip-qc", action="store_true")
    args = parser.parse_args()

    report = Report(args.report)
    step(report, "1. Kiểm tra dữ liệu tải về")(verify)(report, args.phenikaa)
    step(report, "2. Tạo bộ dữ liệu huấn luyện")(build)(report, args.phenikaa, args.nuscenes, args.out)
    if not args.skip_qc:
        step(report, "3. QC 3D trên nhãn PHENIKAA (mẫu)")(qc_labels)(report, args.phenikaa, args.qc_stride, args.qc_minutes)
    step(report, "4. Nén để upload Kaggle")(make_zip)(report, args.out, args.zip)
    report.add("", "## Việc tiếp theo", "",
               "1. Kaggle: dataset `train-vn` → New Version → upload file zip ở bước 4.",
               "2. Notebook: Import `utils/qc3d/kaggle/train_vn_centerpoint.ipynb` → Run All.",
               "3. Xem lại các lỗi nhãn ở bước 3 trước khi coi nhãn PHENIKAA là chuẩn.")


if __name__ == "__main__":
    main()
