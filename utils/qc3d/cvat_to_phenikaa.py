# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Bring labels verified in CVAT back to a PHENIKAA scene, for the next training set.

Export the reviewed job from CVAT as "Datumaro 3D 1.0", then:

  python cvat_to_phenikaa.py --export job_23_datumaro.zip --scene "D:/dataset/phenikaa_mini/COMPLEX CITY"

writes <scene>/label_verified/<stem>.txt (x y z l w h yaw class, LiDAR frame), one file per exported frame;
build_train_set.py uses these files instead of label/ for those frames.
"""

import argparse
import json
import zipfile
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export", type=Path, required=True, help="CVAT export (Datumaro 3D 1.0 zip)")
    parser.add_argument("--scene", type=Path, required=True, help="PHENIKAA scene dir")
    args = parser.parse_args()

    with zipfile.ZipFile(args.export) as archive:
        name = next(n for n in archive.namelist() if n.startswith("annotations/") and n.endswith(".json"))
        dataset = json.loads(archive.read(name))
    labels = [label["name"] for label in dataset["categories"]["label"]["labels"]]
    stems = {p.stem for p in (args.scene / "lidar").glob("*.laz")}
    out = args.scene / "label_verified"
    out.mkdir(exist_ok=True)
    counts, skipped = Counter(), 0
    for item in dataset["items"]:
        stem = Path(str(item["id"])).name
        if stem not in stems:
            skipped += 1
            continue
        lines = []
        for ann in item.get("annotations", []):
            if ann.get("type") != "cuboid_3d":
                continue
            (x, y, z), yaw, (l, w, h) = ann["position"], ann["rotation"][2], ann["scale"]
            label = labels[ann["label_id"]]
            lines.append(f"{x:.4f} {y:.4f} {z:.4f} {l:.4f} {w:.4f} {h:.4f} {yaw:.6f} {label}")
            counts[label] += 1
        (out / f"{stem}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))
    print(f"{len(dataset['items']) - skipped} frame -> {out} | box: {dict(counts)} | bỏ qua {skipped} frame không thuộc scene")


if __name__ == "__main__":
    main()
