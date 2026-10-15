# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Install a trained checkpoint for QC rule R12: keep the model weights (float tensors) of an OpenPCDet .pth
as cvat/apps/qc3d/model_weights/<key>.npz (no PyTorch needed), and check that they fit the network.

  python utils/qc3d/install_model.py --pth checkpoint_epoch_30.pth --key vn-cp-pillar-v2

The server uses the newest installed model of detection.MODELS (restart cvat_server after installing).
"""

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from cvat.apps.qc3d.qc.checkpoint import model_weights  # noqa: E402
from cvat.apps.qc3d.qc.detector import CONFIG, CONFIG_V2, Detector  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pth", type=Path, required=True)
    parser.add_argument("--key", required=True, choices=["vn-cp-pillar-e20", "vn-cp-pillar-v2", "vn-cp-pillar-v5"])
    args = parser.parse_args()

    weights = {k: v for k, v in model_weights(args.pth).items() if v.dtype.kind == "f"}
    config = CONFIG if args.key.endswith("e20") else CONFIG_V2  # v2 and later: 4 class groups
    detector = Detector(weights, config)  # fails here if the checkpoint does not fit the network
    for index, names in enumerate(config["heads"]):
        channels = detector.heads[index]["hm"][1][0].shape[0]
        if channels != len(names):
            raise SystemExit(f"head {index}: checkpoint has {channels} classes, {args.key} expects {names}")
    target = ROOT / "cvat/apps/qc3d/model_weights" / f"{args.key}.npz"
    np.savez(target, **weights)
    print(f"{len(weights)} tensors -> {target} ({target.stat().st_size / 2**20:.0f} MB)")


if __name__ == "__main__":
    main()
