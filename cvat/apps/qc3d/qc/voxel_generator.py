# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Numpy point -> voxel (pillar) generator with the spconv 1.x API that OpenPCDet's data processor uses:
    VoxelGeneratorV2(voxel_size, point_cloud_range, max_num_points, max_voxels).generate(points)
    -> (voxels [M, max_num_points, F], coordinates [M, 3] as (z, y, x), num_points_per_voxel [M])

Installed as `spconv.utils` in the training notebooks (utils/qc3d/kaggle/make_notebook.py), where the real
spconv cannot be imported, and used by the QC detector (detector.py) to run the model without a GPU.
Voxels keep the order in which they first appear in the (shuffled) point list, like spconv; points
beyond max_num_points in a voxel and voxels beyond max_voxels are dropped, like spconv.
"""

import numpy as np


class VoxelGeneratorV2:
    def __init__(self, voxel_size, point_cloud_range, max_num_points, max_voxels=20000, **_):
        self.voxel_size = np.asarray(voxel_size, dtype=np.float32)
        self.point_cloud_range = np.asarray(point_cloud_range, dtype=np.float32)
        self.grid_size = np.round(
            (self.point_cloud_range[3:] - self.point_cloud_range[:3]) / self.voxel_size
        ).astype(np.int64)
        self.max_num_points = int(max_num_points)
        self.max_voxels = int(max_voxels)

    def generate(self, points, max_voxels=None):
        max_voxels = int(max_voxels or self.max_voxels)
        index = np.floor((points[:, :3] - self.point_cloud_range[:3]) / self.voxel_size).astype(np.int64)
        inside = np.all((index >= 0) & (index < self.grid_size), axis=1)
        points, index = points[inside], index[inside]

        nx, ny, _ = self.grid_size
        key = (index[:, 2] * ny + index[:, 1]) * nx + index[:, 0]
        _, first, inverse = np.unique(key, return_index=True, return_inverse=True)
        inverse = inverse.reshape(-1)
        kept = np.argsort(first, kind="stable")[:max_voxels]  # voxels in order of first appearance
        rank = np.full(len(first), -1, dtype=np.int64)
        rank[kept] = np.arange(len(kept))
        voxel_id = rank[inverse]
        keep = voxel_id >= 0
        points, index, voxel_id = points[keep], index[keep], voxel_id[keep]

        order = np.argsort(voxel_id, kind="stable")  # keeps the point order inside each voxel
        voxel_id, points, index = voxel_id[order], points[order], index[order]
        count = len(kept)
        starts = np.searchsorted(voxel_id, np.arange(count))
        slot = np.arange(len(voxel_id)) - starts[voxel_id]
        fits = slot < self.max_num_points

        voxels = np.zeros((count, self.max_num_points, points.shape[1]), dtype=points.dtype)
        voxels[voxel_id[fits], slot[fits]] = points[fits]
        num_points = np.minimum(np.bincount(voxel_id, minlength=count), self.max_num_points).astype(np.int32)
        coordinates = np.zeros((count, 3), dtype=np.int32)
        coordinates[voxel_id] = index[:, ::-1]  # (z, y, x)
        return voxels, coordinates, num_points


VoxelGenerator = VoxelGeneratorV2
