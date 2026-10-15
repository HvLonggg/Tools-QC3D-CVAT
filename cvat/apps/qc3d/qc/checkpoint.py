# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Read the weights of a PyTorch checkpoint (OpenPCDet .pth) as numpy arrays, without PyTorch.

Two layouts exist:
  - zip (torch >= 1.6 default): `<name>/data.pkl` + one raw file per storage in `<name>/data/`;
  - legacy (OpenPCDet saves with `_use_new_zipfile_serialization=False`): magic number, protocol,
    system info, the pickled object, the list of storage keys, then each storage as int64 count + raw bytes.
Only plain data is rebuilt: tensors, dicts, lists, numbers and numpy scalars; any other class is replaced
by None (never executed).
"""

from __future__ import annotations

import io
import pickle
import struct
import zipfile
from collections import OrderedDict
from pathlib import Path

import numpy as np

STORAGE_DTYPES = {
    "FloatStorage": np.float32, "DoubleStorage": np.float64, "HalfStorage": np.float16,
    "LongStorage": np.int64, "IntStorage": np.int32, "ShortStorage": np.int16,
    "CharStorage": np.int8, "ByteStorage": np.uint8, "BoolStorage": np.bool_,
}
LEGACY_MAGIC = 0x1950A86A20F9469CFC6C


class _StorageType:
    def __init__(self, dtype):
        self.dtype = dtype


class _Storage:
    """A storage whose bytes may be read after the object pickle (legacy layout)."""

    def __init__(self, dtype):
        self.dtype = np.dtype(dtype)
        self.array: np.ndarray | None = None


class _Tensor:
    def __init__(self, storage: _Storage, offset: int, size, stride):
        self.storage, self.offset, self.size, self.stride = storage, offset, tuple(size), tuple(stride)

    def resolve(self) -> np.ndarray:
        array = self.storage.array
        if not self.size:
            return np.array(array[self.offset])
        itemsize = array.dtype.itemsize
        return np.lib.stride_tricks.as_strided(
            array[self.offset:], shape=self.size, strides=tuple(s * itemsize for s in self.stride)
        ).copy()


def _rebuild_tensor(storage, offset, size, stride, *_args):
    return _Tensor(storage, offset, size, stride)


def _rebuild_parameter(data, *_args):
    return data


class _Unpickler(pickle.Unpickler):
    def __init__(self, file, storages: dict, read_storage=None):
        super().__init__(file)
        self.storages, self.read_storage = storages, read_storage

    def find_class(self, module, name):
        if module == "collections" and name == "OrderedDict":
            return OrderedDict
        if module == "_codecs" and name == "encode":  # raw bytes of pickled numpy scalars
            return lambda text, encoding="latin1": text.encode(encoding)
        if module == "torch._utils" and name in ("_rebuild_tensor_v2", "_rebuild_tensor"):
            return _rebuild_tensor
        if module == "torch._utils" and name == "_rebuild_parameter":
            return _rebuild_parameter
        if module == "torch" and name in STORAGE_DTYPES:
            return _StorageType(STORAGE_DTYPES[name])
        if module in ("numpy", "numpy.core.multiarray", "numpy._core.multiarray") and name in (
            "scalar", "dtype", "_reconstruct", "ndarray",
        ):
            return super().find_class(module, name)
        return lambda *args, **kwargs: None  # anything else is not needed for the weights

    def persistent_load(self, saved_id):
        storage_type, key = saved_id[1], str(saved_id[2])
        if key not in self.storages:
            dtype = storage_type.dtype if isinstance(storage_type, _StorageType) else np.float32
            storage = _Storage(dtype)
            if self.read_storage is not None:  # zip layout: the bytes are available right away
                storage.array = np.frombuffer(self.read_storage(key), dtype=storage.dtype)
            self.storages[key] = storage
        return self.storages[key]


def _resolve(value):
    if isinstance(value, _Tensor):
        return value.resolve()
    if isinstance(value, dict):
        return type(value)((k, _resolve(v)) for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return type(value)(_resolve(v) for v in value)
    return value


def load_checkpoint(path: str | Path) -> dict:
    storages: dict[str, _Storage] = {}
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            pickle_name = next(n for n in archive.namelist() if n.endswith("/data.pkl"))
            prefix = pickle_name[: -len("/data.pkl")]
            obj = _Unpickler(
                io.BytesIO(archive.read(pickle_name)), storages,
                lambda key: archive.read(f"{prefix}/data/{key}"),
            ).load()
        return _resolve(obj)

    with open(path, "rb") as f:
        header = _Unpickler(f, storages)
        if header.load() != LEGACY_MAGIC:
            raise ValueError(f"{path}: not a PyTorch checkpoint")
        header.load()  # protocol version
        header.load()  # system info
        obj = _Unpickler(f, storages).load()
        keys = _Unpickler(f, storages).load()
        for key in keys:
            storage = storages[str(key)]
            (count,) = struct.unpack("<q", f.read(8))
            storage.array = np.frombuffer(f.read(count * storage.dtype.itemsize), dtype=storage.dtype)
    return _resolve(obj)


def model_weights(path: str | Path) -> dict[str, np.ndarray]:
    state = load_checkpoint(path)
    weights = state.get("model_state", state) if isinstance(state, dict) else {}
    return {k: np.asarray(v) for k, v in weights.items() if isinstance(v, np.ndarray)}
