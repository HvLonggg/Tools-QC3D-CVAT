# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
CenterPoint-PointPillar (OpenPCDet, utils/qc3d/kaggle/vn_centerpoint_pillar.yaml) in numpy, for CPU-only
inference inside CVAT: the trained checkpoint is loaded as is (qc/checkpoint.py, no PyTorch), so QC can
compare the model with the labels without a GPU.

Layers mirror OpenPCDet module by module (PillarVFE -> PointPillarScatter -> BaseBEVBackbone -> CenterHead);
batch norm is folded into the convolutions. Box decoding and NMS follow CenterHead.generate_predicted_boxes
(top-K on the heat maps, per-head rotated BEV NMS). The input is the training frame: LiDAR at the origin,
road at z = -1.8 m (see `predict(..., ground_z=...)`), intensity 0-255.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .checkpoint import model_weights
from .voxel_generator import VoxelGeneratorV2

CONFIG = {
    "classes": ["Car", "Truck", "Bus", "Rider", "Motorbike", "Pedestrian"],
    "heads": [["Car"], ["Truck", "Bus"], ["Rider", "Motorbike"], ["Pedestrian"]],
    "point_cloud_range": [-51.2, -51.2, -5.0, 51.2, 51.2, 3.0],
    "voxel_size": [0.2, 0.2, 8.0],
    "max_points_per_voxel": 20,
    "max_voxels": 40000,
    "layer_nums": [3, 5, 5],
    "layer_strides": [2, 2, 2],
    "upsample_strides": [0.5, 1, 2],
    "feature_map_stride": 4,
    "head_order": ["center", "center_z", "dim", "rot", "hm"],
    "score_thresh": 0.1,
    "post_center_limit_range": [-61.2, -61.2, -10.0, 61.2, 61.2, 10.0],
    "max_obj_per_sample": 500,
    "nms_thresh": 0.2,
    "nms_pre_maxsize": 1000,
    "nms_post_maxsize": 200,  # per head: 83 (nuScenes) cut the two-wheelers in dense VN traffic
    "ground_z": -1.8,  # road height in the training frames (build_train_set.py)
}

# v2 (vn_centerpoint_pillar.yaml): 4 groups, split into the guideline classes by split_group()
CONFIG_V2 = {
    **CONFIG,
    "classes": ["Car", "LargeVehicle", "TwoWheeler", "Pedestrian"],
    "heads": [["Car"], ["LargeVehicle"], ["TwoWheeler"], ["Pedestrian"]],
}


def split_group(label: str, box, classes) -> str:
    """Guideline class of a v2 group detection: Truck / Bus by size, Rider / Motorbike by height (as R11)."""
    from .taxonomy import TWO_WHEELER_SPLIT, size_log_likelihood

    if label == "LargeVehicle":
        options = [name for name in ("Truck", "Bus") if name in classes] or ["Truck"]
        return max(options, key=lambda name: size_log_likelihood(classes[name], tuple(box[3:6])))
    if label == "TwoWheeler":
        return "Motorbike" if box[5] < TWO_WHEELER_SPLIT and "Motorbike" in classes else "Rider"
    return label


@dataclass
class Detection:
    box: np.ndarray  # x, y, z, length, width, height, yaw (LiDAR frame of the input points)
    label: str
    score: float


# --------------------------------------------------------------------------------------------- layers


def _fold_bn(w: dict, conv: str, bn: str, eps: float, out_axis: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Weights and bias of a convolution / linear layer followed by batch norm (eval mode)."""
    scale = w[f"{bn}.weight"] / np.sqrt(w[f"{bn}.running_var"] + eps)
    weight = w[f"{conv}.weight"].astype(np.float32)
    shape = [1] * weight.ndim
    shape[out_axis] = -1
    bias = w.get(f"{conv}.bias", 0.0)
    bias = (bias - w[f"{bn}.running_mean"]) * scale + w[f"{bn}.bias"]
    return (weight * scale.reshape(shape)).astype(np.float32), bias.astype(np.float32)


IM2COL_BYTES = 32 * 2**20  # size of the patch matrix built per band of output rows


def _conv(x: np.ndarray, weight: np.ndarray, bias, *, stride: int = 1, pad: int = 1) -> np.ndarray:
    """
    2D convolution of x (C, H, W) with weight (O, C, k, k), as one matrix product per band of output rows
    (im2col): 2-3x faster than one product per kernel tap, same sums up to float rounding.
    """
    channels, height, width = x.shape
    out_channels, _, k, _ = weight.shape
    if pad:
        x = np.pad(x, ((0, 0), (pad, pad), (pad, pad)))
    out_h = (height + 2 * pad - k) // stride + 1
    out_w = (width + 2 * pad - k) // stride + 1
    matrix = np.ascontiguousarray(weight.transpose(0, 2, 3, 1).reshape(out_channels, k * k * channels))
    out = np.empty((out_channels, out_h, out_w), dtype=np.float32)
    rows = max(1, IM2COL_BYTES // (k * k * channels * out_w * 4))
    for r0 in range(0, out_h, rows):
        r1 = min(out_h, r0 + rows)
        cols = np.empty((k, k, channels, r1 - r0, out_w), dtype=np.float32)
        for i in range(k):
            for j in range(k):
                cols[i, j] = x[:, i + stride * r0:i + stride * (r1 - 1) + 1:stride, j:j + stride * (out_w - 1) + 1:stride]
        out[:, r0:r1] = (matrix @ cols.reshape(k * k * channels, -1)).reshape(out_channels, r1 - r0, out_w)
    out += np.asarray(bias, dtype=np.float32).reshape(-1, 1, 1)
    return out


def _conv_at(x: np.ndarray, layers: list, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    """
    Output of a stack of 3x3 / pad 1 convolutions (ReLU between them) at the pixels (ys, xs) only, from the
    (2L+1)^2 neighbourhood of each pixel; positions outside the map are zero at every layer, as with padding.
    Returns (O, N).
    """
    channels, height, width = x.shape
    radius = len(layers)
    size = 2 * radius + 1
    padded = np.pad(x, ((0, 0), (radius, radius), (radius, radius)))
    offsets = np.arange(size)
    rows = ys[:, None, None] + offsets[None, :, None]  # in padded coordinates
    cols = xs[:, None, None] + offsets[None, None, :]
    patch = padded[:, rows, cols].transpose(1, 0, 2, 3)  # (N, C, size, size)
    for index, (weight, bias) in enumerate(layers):
        out_channels = weight.shape[0]
        size -= 2
        out = np.zeros((len(ys), out_channels, size, size), dtype=np.float32)
        for i in range(3):
            for j in range(3):
                window = patch[:, :, i:i + size, j:j + size]
                out += np.einsum("oc,ncij->noij", weight[:, :, i, j], window, optimize=True)
        out += np.asarray(bias, dtype=np.float32).reshape(1, -1, 1, 1)
        if index < len(layers) - 1:
            _relu(out)
            # zero outside the map, as the padding of the next convolution
            r = ys[:, None] - (size // 2) + np.arange(size)[None, :]
            c = xs[:, None] - (size // 2) + np.arange(size)[None, :]
            inside = ((r >= 0) & (r < height))[:, :, None] & ((c >= 0) & (c < width))[:, None, :]
            out *= inside[:, None, :, :]
        patch = out
    return patch[:, :, 0, 0].T


def _deconv(x: np.ndarray, weight: np.ndarray, bias, stride: int) -> np.ndarray:
    """ConvTranspose2d with kernel == stride; weight (C, O, k, k)."""
    channels, height, width = x.shape
    out_channels, k = weight.shape[1], weight.shape[2]
    flat = x.reshape(channels, -1)
    out = np.zeros((out_channels, height * k, width * k), dtype=np.float32)
    taps = np.ascontiguousarray(weight.transpose(2, 3, 1, 0))  # (k, k, O, C)
    for i in range(k):
        for j in range(k):
            out[:, i::k, j::k] = (taps[i, j] @ flat).reshape(out_channels, height, width)
    return out + np.asarray(bias, dtype=np.float32).reshape(-1, 1, 1)


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(x, 0, out=x)


def _bev_corners(boxes: np.ndarray) -> np.ndarray:
    c, s = np.cos(boxes[:, 6]), np.sin(boxes[:, 6])
    half = np.array([[1, 1], [-1, 1], [-1, -1], [1, -1]], dtype=np.float64) / 2
    dx = half[None, :, 0] * boxes[:, 3:4]
    dy = half[None, :, 1] * boxes[:, 4:5]
    return np.stack([boxes[:, 0:1] + dx * c[:, None] - dy * s[:, None],
                     boxes[:, 1:2] + dx * s[:, None] + dy * c[:, None]], axis=-1)


def _poly_area(points: list) -> float:
    if len(points) < 3:
        return 0.0
    p = np.asarray(points)
    return 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1)))


def _clip(subject: list, a: np.ndarray, b: np.ndarray) -> list:
    def side(p):
        return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

    out = []
    for i, current in enumerate(subject):
        previous = subject[i - 1]
        sc, sp = side(current), side(previous)
        if sc >= 0:
            if sp < 0:
                out.append(previous + (current - previous) * (sp / (sp - sc)))
            out.append(current)
        elif sp >= 0:
            out.append(previous + (current - previous) * (sp / (sp - sc)))
    return out


def _bev_iou(corners_a: np.ndarray, area_a: float, corners_b: np.ndarray, area_b: float) -> float:
    polygon = list(corners_a)
    for i in range(4):
        polygon = _clip(polygon, corners_b[i - 1], corners_b[i])
        if not polygon:
            return 0.0
    inter = _poly_area(polygon)
    return inter / (area_a + area_b - inter) if area_a + area_b - inter > 0 else 0.0


def rotated_nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> list[int]:
    """Greedy NMS on rotated BEV IoU, like OpenPCDet iou3d_nms_utils.nms_gpu; `boxes` sorted or not."""
    order = np.argsort(-scores, kind="stable")
    corners = _bev_corners(boxes)
    areas = boxes[:, 3] * boxes[:, 4]
    radius = np.hypot(boxes[:, 3], boxes[:, 4]) / 2
    keep: list[int] = []
    for i in order:
        suppressed = False
        for j in keep:
            if math.hypot(boxes[i, 0] - boxes[j, 0], boxes[i, 1] - boxes[j, 1]) > radius[i] + radius[j]:
                continue
            if _bev_iou(corners[i], areas[i], corners[j], areas[j]) > threshold:
                suppressed = True
                break
        if not suppressed:
            keep.append(int(i))
    return keep


# --------------------------------------------------------------------------------------------- model


class Detector:
    def __init__(self, weights: dict[str, np.ndarray], config: dict = CONFIG):
        self.cfg = config
        w = {k: v.astype(np.float32) if v.dtype.kind == "f" else v for k, v in weights.items()}
        self.voxelizer = VoxelGeneratorV2(
            config["voxel_size"], config["point_cloud_range"], config["max_points_per_voxel"], config["max_voxels"]
        )
        self.grid = self.voxelizer.grid_size  # x, y, z cells

        # PillarVFE: linear (no bias) + BN1d(eps 1e-3) per PFN layer
        self.pfn = []
        index = 0
        while f"vfe.pfn_layers.{index}.linear.weight" in w:
            self.pfn.append(_fold_bn(w, f"vfe.pfn_layers.{index}.linear", f"vfe.pfn_layers.{index}.norm", 1e-3))
            index += 1

        # BaseBEVBackbone: [ZeroPad, conv s, BN, ReLU] + layer_nums x [conv, BN, ReLU]; BN eps 1e-3
        self.blocks = []
        for b, layers in enumerate(config["layer_nums"]):
            convs = []
            for layer in range(layers + 1):
                conv_index = 1 + 3 * layer
                convs.append(_fold_bn(w, f"backbone_2d.blocks.{b}.{conv_index}", f"backbone_2d.blocks.{b}.{conv_index + 1}", 1e-3))
            self.blocks.append(convs)
        self.deblocks = []
        for b, stride in enumerate(config["upsample_strides"]):
            transposed = stride >= 1
            weight, bias = _fold_bn(w, f"backbone_2d.deblocks.{b}.0", f"backbone_2d.deblocks.{b}.1", 1e-3,
                                    out_axis=1 if transposed else 0)
            self.deblocks.append((transposed, int(round(stride if transposed else 1 / stride)), weight, bias))

        # CenterHead: shared conv (bias + BN eps 1e-5), per head SeparateHead [conv+BN+ReLU] + conv
        self.shared = _fold_bn(w, "dense_head.shared_conv.0", "dense_head.shared_conv.1", 1e-5)
        self.heads = []
        for h in range(len(config["heads"])):
            head = {}
            for name in config["head_order"]:
                prefix = f"dense_head.heads_list.{h}.{name}"
                hidden, n = [], 0
                while f"{prefix}.{n}.0.weight" in w:
                    hidden.append(_fold_bn(w, f"{prefix}.{n}.0", f"{prefix}.{n}.1", 1e-5))
                    n += 1
                head[name] = (hidden, (w[f"{prefix}.{n}.weight"], w[f"{prefix}.{n}.bias"]))
            self.heads.append(head)
        # the heat-map hidden convolutions of all heads as one (one pass over the feature map)
        self.hm_fused = None
        if all(len(head["hm"][0]) == 1 for head in self.heads):
            self.hm_fused = (
                np.concatenate([head["hm"][0][0][0] for head in self.heads]),
                np.concatenate([head["hm"][0][0][1] for head in self.heads]),
            )

    @classmethod
    def from_file(cls, path: str | Path, config: dict = CONFIG) -> "Detector":
        path = Path(path)
        if path.suffix == ".npz":
            with np.load(path) as data:
                return cls({k: data[k] for k in data.files}, config)
        return cls(model_weights(path), config)

    # ------------------------------------------------------------------------------------ forward

    def _pillars(self, points: np.ndarray) -> np.ndarray:
        cfg = self.cfg
        lo, hi = cfg["point_cloud_range"][:3], cfg["point_cloud_range"][3:]
        keep = (points[:, 0] >= lo[0]) & (points[:, 0] <= hi[0]) & (points[:, 1] >= lo[1]) & (points[:, 1] <= hi[1])
        voxels, coords, counts = self.voxelizer.generate(points[keep])
        voxels = voxels.astype(np.float32)
        counts = np.maximum(counts, 1).astype(np.float32)
        vx, vy, vz = cfg["voxel_size"]
        mean = voxels[:, :, :3].sum(axis=1, keepdims=True) / counts.reshape(-1, 1, 1)
        f_cluster = voxels[:, :, :3] - mean
        f_center = np.empty_like(voxels[:, :, :3])
        f_center[:, :, 0] = voxels[:, :, 0] - (coords[:, 2:3] * vx + vx / 2 + lo[0])
        f_center[:, :, 1] = voxels[:, :, 1] - (coords[:, 1:2] * vy + vy / 2 + lo[1])
        f_center[:, :, 2] = voxels[:, :, 2] - (coords[:, 0:1] * vz + vz / 2 + lo[2])
        features = np.concatenate([voxels, f_cluster, f_center], axis=-1)
        mask = np.arange(voxels.shape[1])[None, :] < counts[:, None]
        features *= mask[:, :, None]
        # PFN layers: linear + ReLU per point, max per pillar; a later layer's input is [point features, pillar
        # max repeated], applied here as two products instead of building the repeated (V, P, 2F) array
        n_voxels, n_points, _ = features.shape
        pooled = None
        for index, (weight, bias) in enumerate(self.pfn):
            own = features.shape[-1]
            # one 2D matrix product: on (V, P, F) arrays numpy runs V small products, ~20x slower
            x = (features.reshape(n_voxels * n_points, own) @ weight[:, :own].T).reshape(n_voxels, n_points, -1)
            if pooled is not None:
                x += (pooled @ weight[:, own:].T)[:, None, :]
            x = _relu(x + bias)
            pooled = x.max(axis=1)
            features = x
        features = pooled

        nx, ny = int(self.grid[0]), int(self.grid[1])
        bev = np.zeros((features.shape[1], ny * nx), dtype=np.float32)
        bev[:, coords[:, 0] + coords[:, 1] * nx + coords[:, 2]] = features.T
        return bev.reshape(-1, ny, nx)

    def _backbone(self, x: np.ndarray) -> np.ndarray:
        ups = []
        for b, convs in enumerate(self.blocks):
            for layer, (weight, bias) in enumerate(convs):
                stride = self.cfg["layer_strides"][b] if layer == 0 else 1
                x = _relu(_conv(x, weight, bias, stride=stride, pad=1))
            transposed, stride, weight, bias = self.deblocks[b]
            up = _deconv(x, weight, bias, stride) if transposed else _conv(x, weight, bias, stride=stride, pad=0)
            ups.append(_relu(up))
        return np.concatenate(ups, axis=0)

    def _heat(self, shared: np.ndarray) -> list[np.ndarray]:
        """Heat-map logits of every head, (classes, H, W) each."""
        if self.hm_fused is None:
            maps = []
            for head in self.heads:
                hidden, final = head["hm"]
                x = shared
                for weight, bias in hidden:
                    x = _relu(_conv(x, weight, bias))
                maps.append(_conv(x, *final))
            return maps
        x = _relu(_conv(shared, *self.hm_fused))
        maps, start = [], 0
        for head in self.heads:
            hidden, final = head["hm"]
            width = hidden[0][0].shape[0]
            maps.append(_conv(x[start:start + width], *final))
            start += width
        return maps

    def _decode(self, head_index: int, hm: np.ndarray, shared: np.ndarray) -> list[Detection]:
        """
        Top-K peaks of the heat map; the box outputs (centre, height, size, heading) are computed at those
        pixels only, not over the whole map (same values, ~5x less work for the heads).
        """
        cfg = self.cfg
        names = cfg["heads"][head_index]
        heat = 1 / (1 + np.exp(-np.clip(hm, -50, 50)))  # sigmoid, (classes, H, W)
        n_cls, height, width = heat.shape
        flat = heat.reshape(-1)
        k = min(cfg["max_obj_per_sample"], flat.size)
        top = np.argpartition(-flat, k - 1)[:k]
        top = top[np.argsort(-flat[top], kind="stable")]
        top = top[flat[top] > cfg["score_thresh"]]
        if not len(top):
            return []
        scores = flat[top]
        class_ids = top // (height * width)
        pixel = top % (height * width)
        ys, xs = pixel // width, pixel % width
        outputs = {}
        for name in ("center", "center_z", "dim", "rot"):
            hidden, final = self.heads[head_index][name]
            outputs[name] = _conv_at(shared, [*hidden, final], ys, xs)
        center, center_z, rot = outputs["center"], outputs["center_z"], outputs["rot"]
        dim = np.exp(outputs["dim"])
        angle = np.arctan2(rot[1], rot[0])
        stride, (vx, vy) = cfg["feature_map_stride"], cfg["voxel_size"][:2]
        x = (xs + center[0]) * stride * vx + cfg["point_cloud_range"][0]
        y = (ys + center[1]) * stride * vy + cfg["point_cloud_range"][1]
        boxes = np.column_stack([x, y, center_z[0], dim.T, angle]).astype(np.float64)
        limit = np.asarray(cfg["post_center_limit_range"])
        mask = (boxes[:, :3] >= limit[:3]).all(1) & (boxes[:, :3] <= limit[3:]).all(1)
        boxes, scores, class_ids = boxes[mask], scores[mask], class_ids[mask]
        order = np.argsort(-scores, kind="stable")[: cfg["nms_pre_maxsize"]]
        boxes, scores, class_ids = boxes[order], scores[order], class_ids[order]
        keep = rotated_nms(boxes, scores, cfg["nms_thresh"])[: cfg["nms_post_maxsize"]]
        return [Detection(boxes[i], names[int(class_ids[i])], float(scores[i])) for i in keep]

    def predict(self, points: np.ndarray, *, ground_z: float | None = None) -> list[Detection]:
        """
        points: (N, >=3) x, y, z [, intensity 0-255]. `ground_z`: road height of these points; the cloud is
        shifted to the training road height (-1.8 m) and the boxes shifted back.
        """
        shift = 0.0 if ground_z is None else self.cfg["ground_z"] - ground_z
        cloud = np.zeros((len(points), 5), dtype=np.float32)
        cloud[:, :3] = points[:, :3]
        cloud[:, 2] += shift
        if points.shape[1] > 3:
            cloud[:, 3] = points[:, 3]
        if points.shape[1] > 4:  # multi-sweep input: time lag of each point (qc/sweeps.py)
            cloud[:, 4] = points[:, 4]
        features = self._backbone(self._pillars(cloud))
        shared = _relu(_conv(features, *self.shared))
        detections = []
        for index, hm in enumerate(self._heat(shared)):
            detections.extend(self._decode(index, hm, shared))
        for detection in detections:
            detection.box[2] -= shift
        return detections
