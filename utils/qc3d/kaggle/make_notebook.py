# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Generate the training notebooks from the YAML configs next to them, so the configs have one source of truth:

  - train_vn_centerpoint.ipynb        Kaggle (dataset added as Input, outputs in /kaggle/working/results)
  - train_vn_centerpoint_colab.ipynb  Google Colab (zip and outputs on Google Drive; a disconnected session
                                      resumes from the last saved epoch)

  python utils/qc3d/kaggle/make_notebook.py
"""

import json
from pathlib import Path

HERE = Path(__file__).parent
CLASSES = ["Car", "LargeVehicle", "TwoWheeler", "Pedestrian"]  # v2: see vn_centerpoint_pillar.yaml
MODEL_TAG = "v5"  # new output folder per round: never resumes from the checkpoints of another round
WORK = {"kaggle": "/kaggle/working", "colab": "/content"}
GPU_HINT = {
    "kaggle": "panel phải → Session options → Accelerator → GPU T4 x2 "
              "(khi Save Version: Advanced settings → chọn \"Run with GPU\")",
    "colab": "menu Runtime → Change runtime type → Hardware accelerator: T4 GPU → Save",
}


def md(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": text.strip("\n").splitlines(keepends=True)}


def code(text: str) -> dict:
    return {
        "cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [],
        "source": text.strip("\n").splitlines(keepends=True),
    }


def write_file_cell(target: str, path: Path) -> dict:
    return code(f"%%writefile {target}\n" + path.read_text(encoding="utf-8"))


INTRO = {
    "kaggle": """
# QC 3D · Huấn luyện model phát hiện 3D theo class giao thông VN

Model: **CenterPoint (pillar)** trên OpenPCDet, học tiếp từ model đã huấn luyện trên toàn bộ nuScenes;
class: Car, Truck, Bus, Rider, Motorbike (xe 2 bánh đỗ), Pedestrian.
Dữ liệu: thư mục do `utils/qc3d/build_train_set.py` tạo (PHENIKAA + nuScenes-mini), upload thành Kaggle Dataset **Private**.

Cách chạy:
1. *Add Input* → chọn dataset dữ liệu huấn luyện của bạn.
2. *Settings* → *Accelerator*: **GPU T4 x2** hoặc **GPU P100**; *Internet*: **On** (để tải OpenPCDet).
3. Chạy thử: để `EPOCHS = 2`, *Run All* (khoảng 30-40 phút gồm cả biên dịch).
4. Chạy thật: đặt `EPOCHS = 20` → *Save Version* → **Save & Run All (Commit)**: chạy nền tới 12 giờ, tắt trình duyệt được.
5. Kết quả (checkpoint, log, điểm đánh giá) ở tab *Output*, thư mục `results/`.
""",
    "colab": """
# QC 3D · Huấn luyện model phát hiện 3D theo class giao thông VN (Google Colab)

Model: **CenterPoint (pillar)** trên OpenPCDet, học tiếp từ model đã huấn luyện trên toàn bộ nuScenes;
class: Car, Truck, Bus, Rider, Motorbike (xe 2 bánh đỗ), Pedestrian.
Dữ liệu: file zip do `utils/qc3d/run_after_download.py` tạo (PHENIKAA + nuScenes-mini).

Cách chạy:
1. Upload `train_vn_kaggle_v4.zip` (hoặc bản mới nhất) lên Google Drive, vào thư mục **`qc3d`** (tạo thư mục này ở *My Drive*).
2. Menu *Runtime* → *Change runtime type* → **T4 GPU** → *Save*.
3. *Runtime* → **Run all**; khi được hỏi, cho phép notebook truy cập Google Drive.
4. Giữ tab trình duyệt mở. Mỗi 5 phút notebook in một dòng tiến độ.
5. Nếu Colab ngắt giữa chừng: kết nối lại → *Run all*. Checkpoint nằm trên Drive nên học tiếp từ epoch đã lưu
   (mất khoảng 15 phút cài lại OpenPCDet). Muốn học lại từ đầu: đặt `START_FRESH = True`.
6. Kết quả (checkpoint, log, điểm đánh giá) ở Google Drive: `qc3d/results/`.
""",
}

SETTINGS = {
    "kaggle": """
EPOCHS = 15         # học tiếp từ model vòng trước (Add Input: output của notebook đó); 30 nếu học từ nuScenes
BATCH_SIZE = 4      # mỗi GPU (T4 x2: 8 mẫu một bước); giảm còn 2 nếu báo hết bộ nhớ GPU (CUDA out of memory)
CLASSES = %s
""" % CLASSES,
    "colab": """
EPOCHS = 30         # 2 để chạy thử nhanh; 30 cho bản v2 (v1: loss vẫn giảm ở epoch 20)
BATCH_SIZE = 4      # giảm còn 2 nếu báo hết bộ nhớ GPU (CUDA out of memory)
CLASSES = %s
DRIVE_DIR = '/content/drive/MyDrive/qc3d'   # chứa file zip dữ liệu; checkpoint và kết quả cũng lưu ở đây
START_FRESH = False  # True: xoá checkpoint cũ trên Drive và học lại từ đầu
""" % CLASSES,
}

DATA = {
    "kaggle": """
import os, zipfile
from pathlib import Path

def find_root(base):
    for dirpath, dirnames, _ in os.walk(base, followlinks=True):
        if {'ImageSets', 'points', 'labels'} <= set(dirnames):
            return Path(dirpath)
    return None

def show_tree(base, depth=3):
    base = Path(base)
    for dirpath, dirnames, filenames in os.walk(base, followlinks=True):
        level = len(Path(dirpath).relative_to(base).parts)
        if level >= depth:
            dirnames[:] = []
        print('  ' * level + Path(dirpath).name + '/', f'({len(filenames)} files)')

RESULTS = Path('/kaggle/working/results')
src = find_root('/kaggle/input')
if src is None:  # dataset kept as a zip: extract it
    zips = [Path(d) / f for d, _, fs in os.walk('/kaggle/input', followlinks=True) for f in fs if f.endswith('.zip')]
    for archive in zips:
        print('extracting', archive)
        zipfile.ZipFile(archive).extractall('/kaggle/working/train_vn')
    src = find_root('/kaggle/working/train_vn') if zips else None
if src is None:
    show_tree('/kaggle/input')
    raise SystemExit('Không thấy dữ liệu: panel phải → Input → + Add Input → chọn dataset train-vn')
# labels_overlay (labels/ + ImageSets/, no points; Kaggle unpacks the zip): newer labels over the dataset,
# merged in a writable folder since /kaggle/input is read only
label_root = src
overlay = next((Path(d) for d, dirs, _ in os.walk('/kaggle/input', followlinks=True)
                if {'labels', 'ImageSets'} <= set(dirs) and 'points' not in dirs), None)
if overlay is not None:
    import shutil
    label_root = Path('/kaggle/working/train_vn_labels')
    shutil.rmtree(label_root, ignore_errors=True)
    shutil.copytree(src / 'labels', label_root / 'labels')
    shutil.copytree(overlay / 'labels', label_root / 'labels', dirs_exist_ok=True)
    shutil.copytree(overlay / 'ImageSets', label_root / 'ImageSets')
    print('labels_overlay:', overlay, len(list((overlay / 'labels').glob('*.txt'))), 'file nhãn mới')
""",
    "colab": """
# Google Drive holds the zip (uploaded once) and the outputs, which survive a disconnected session
import os, shutil, zipfile
from pathlib import Path
from google.colab import drive
drive.mount('/content/drive')

def find_root(base):
    for dirpath, dirnames, _ in os.walk(base, followlinks=True):
        if {'ImageSets', 'points', 'labels'} <= set(dirnames):
            return Path(dirpath)
    return None

DRIVE = Path(DRIVE_DIR)
DRIVE.mkdir(parents=True, exist_ok=True)
RESULTS = DRIVE / 'results'
src = find_root('/content/train_vn') if Path('/content/train_vn').exists() else None
if src is None:
    zips = sorted(DRIVE.glob('train_vn*.zip')) or sorted(Path('/content/drive/MyDrive').glob('train_vn*.zip'))
    if not zips:
        raise SystemExit('Không thấy file zip dữ liệu: upload train_vn_kaggle_v4.zip vào Google Drive, '
                         'thư mục My Drive/qc3d, rồi chạy lại cell này')
    archive = max(zips, key=lambda p: p.stat().st_mtime)
    # copy to the local disk first: extracting thousands of files straight from Drive is very slow
    print(f'copy {archive} ({archive.stat().st_size / 2**30:.2f} GB) → /content ...')
    local = Path('/content') / archive.name
    shutil.copy(archive, local)
    zipfile.ZipFile(local).extractall('/content/train_vn')
    local.unlink()
    src = find_root('/content/train_vn')
if src is None:
    raise SystemExit('File zip không có các thư mục points/, labels/, ImageSets/: dùng file do run_after_download.py tạo')
# labels_overlay.zip (labels/ + ImageSets/ only, a few MB): newer labels over the big data zip, no new upload of it
overlay = DRIVE / 'labels_overlay.zip'
if overlay.exists():
    zipfile.ZipFile(overlay).extractall(src)
    print('labels_overlay.zip:', len(zipfile.ZipFile(overlay).namelist()), 'file nhãn mới')
label_root = src
""",
}

PRETRAINED_FALLBACK = {
    "kaggle": """
if not os.path.exists(src):  # fallback: the .pth uploaded as a Kaggle dataset
    found = glob.glob('/kaggle/input/**/*centerpoint*.pth', recursive=True)
    src = found[0] if found else None
""",
    "colab": """
if not os.path.exists(src):  # fallback: the .pth uploaded to the Drive folder
    found = glob.glob(f'{DRIVE_DIR}/*centerpoint*.pth')
    src = found[0] if found else None
""",
}

# checkpoint of an earlier round to learn on from (Kaggle: that notebook's output added as input)
INIT_GLOB = {"kaggle": "'/kaggle/input/**/checkpoint_epoch_*.pth'", "colab": "f'{DRIVE_DIR}/init/checkpoint_epoch_*.pth'"}

OUTPUT_DIR = {
    "kaggle": """
# 1) no stale run: train.py silently resumes from any checkpoint left in the output dir
shutil.rmtree('/kaggle/working/OpenPCDet/output/custom_models/vn_centerpoint_pillar', ignore_errors=True)
""",
    "colab": """
# 1) the output dir lives on Google Drive: after a disconnect, Run all again and train.py resumes from the
#    newest checkpoint it finds there
out_root = Path('/content/OpenPCDet/output')
drive_out = Path(DRIVE_DIR) / 'output'
run_dir = drive_out / 'custom_models' / 'vn_centerpoint_pillar'
if START_FRESH:
    shutil.rmtree(run_dir, ignore_errors=True)
drive_out.mkdir(parents=True, exist_ok=True)
if not out_root.is_symlink():
    shutil.rmtree(out_root, ignore_errors=True)
    out_root.symlink_to(drive_out)
saved = sorted(run_dir.glob('*/ckpt/*.pth'), key=lambda f: f.stat().st_mtime)
print('Học tiếp từ', saved[-1].name if saved else 'đầu (chưa có checkpoint trên Drive)')
""",
}

EXTRA_TRAIN_ARGS = {"kaggle": "", "colab": ", '--max_ckpt_save_num', '3'"}  # Drive space: keep 3 epochs


def build_cells(platform: str) -> list[dict]:
    tag = f"{platform}_{MODEL_TAG}"
    return [
        md(INTRO[platform]),
        code(SETTINGS[platform]),
        code("""
!nvidia-smi --query-gpu=name,memory.total --format=csv
import sys, torch
print(sys.version)
print('torch', torch.__version__, 'cuda', torch.version.cuda, 'gpu', torch.cuda.is_available())
if not torch.cuda.is_available():
    raise SystemExit('Chưa bật GPU: GPU_HINT')
""".replace("GPU_HINT", GPU_HINT[platform])),
        md("## 1. Cài OpenPCDet (biên dịch CUDA, khoảng 10-15 phút)"),
        code("""
%cd /kaggle/working
!test -d OpenPCDet || git clone --depth 1 https://github.com/open-mmlab/OpenPCDet.git
!pip install -q easydict tensorboardX pyquaternion numba scikit-image ninja
!pip install -q SharedArray || echo "SharedArray skipped (only needed by other datasets)"
"""),
        code('''
# spconv: needed only by voxel (sparse conv) models, but OpenPCDet imports it at start-up.
# Pillar models never call it, and the real wheels JIT-compile `cumm` on import, which fails on
# recent Python versions (missing tensorview headers): always use a stub instead.
import site, subprocess, sys
from pathlib import Path
subprocess.run([sys.executable, '-m', 'pip', 'uninstall', '-y', '-q', 'spconv', 'cumm',
                'spconv-cu126', 'spconv-cu124', 'spconv-cu121', 'spconv-cu120',
                'cumm-cu126', 'cumm-cu124', 'cumm-cu121', 'cumm-cu120'], capture_output=True)
STUB = (
    '"""Stub of spconv: imported by OpenPCDet, never used by pillar models."""\\n'
    'import types as _types\\n'
    'import torch.nn as _nn\\n'
    "__version__ = '2.3.6'\\n\\n\\n"
    'def _resolve(owner, name):\\n'
    "    # lower case: a sub-module (spconv.conv, spconv.pytorch.functional); upper case: a layer class\\n"
    "    if name.startswith('__'):\\n"
    '        raise AttributeError(name)\\n'
    '    value = _Namespace(name) if name[:1].islower() else type(name, (_nn.Module,), {})\\n'
    '    setattr(owner, name, value)\\n'
    '    return value\\n\\n\\n'
    'class _Namespace(_types.ModuleType):\\n'
    '    def __getattr__(self, name):\\n'
    '        return _resolve(self, name)\\n\\n\\n'
    'class SparseModule(_nn.Module):\\n'
    '    pass\\n\\n\\n'
    'def __getattr__(name):\\n'
    '    import sys\\n'
    '    return _resolve(sys.modules[__name__], name)\\n'
)
import importlib.util, shutil
spec = importlib.util.find_spec('spconv')  # a leftover real package must be replaced where it is found
if spec and spec.origin:
    shutil.rmtree(Path(spec.origin).parent, ignore_errors=True)
root = Path(spec.origin).parent if spec and spec.origin else Path(site.getsitepackages()[0]) / 'spconv'
(root / 'pytorch').mkdir(parents=True, exist_ok=True)
(root / '__init__.py').write_text(STUB)
(root / 'pytorch' / '__init__.py').write_text(STUB)
# spconv.utils: the point -> pillar generator used by OpenPCDet's data processor (numpy, utils/qc3d/kaggle)
(root / 'utils').mkdir(exist_ok=True)
(root / 'utils' / '__init__.py').write_text(VOXEL_GENERATOR_SOURCE)
check = subprocess.run([sys.executable, '-c', (
    'import numpy as np, spconv, spconv.pytorch; from spconv.utils import VoxelGeneratorV2 as G; '
    'g = G([0.2, 0.2, 8.0], [-51.2, -51.2, -5.0, 51.2, 51.2, 3.0], 20, 40000); '
    'v, c, n = g.generate(np.array([[1.0, 2.0, 0.0, 9.0, 0.0]], np.float32)); '
    'print(spconv.__doc__, v.shape, c.tolist(), n.tolist())')], capture_output=True, text=True)
assert 'Stub of spconv' in check.stdout and '[[0, 266, 261]]' in check.stdout, check.stdout + check.stderr
print('spconv: stub + numpy voxel generator at', root)
'''.replace('VOXEL_GENERATOR_SOURCE', repr((HERE.parents[2] / 'cvat/apps/qc3d/qc/voxel_generator.py').read_text(encoding='utf-8')))),
        code("""
# OpenPCDet imports every dataset at start-up; datasets we do not use may need packages that are not
# installed (waymo, argo2, ...): make those imports optional.
import re
from pathlib import Path
init = Path('/kaggle/working/OpenPCDet/pcdet/datasets/__init__.py')
text = init.read_text()
if 'QC3D_OPTIONAL' not in text:
    def optional(match):
        line = match.group(0)
        if 'custom' in line or 'dataset import DatasetTemplate' in line:
            return line
        name = re.search(r'import (\\w+)', line).group(1)
        return f'try:\\n    {line}\\nexcept Exception:  # QC3D_OPTIONAL\\n    {name} = None'
    text = re.sub(r'^from \\.[\\w.]+ import \\w+$', optional, text, flags=re.M)
    text = re.sub(r"^(\\s+)'(\\w+)': (\\w+),$", lambda m: m.group(0) if m.group(3) != 'None' else '', text, flags=re.M)
    init.write_text(text)

# numpy 2 removed np.int / np.float / np.bool / np.long, still used in places by OpenPCDet
changed = 0
for path in Path('/kaggle/working/OpenPCDet/pcdet').rglob('*.py'):
    source = path.read_text()
    fixed = re.sub(r'\\bnp\\.long\\b(?!\\w)', 'np.int64', source)
    fixed = re.sub(r'\\bnp\\.(int|float|bool)\\b(?!\\w)', r'\\1', fixed)
    if fixed != source:
        path.write_text(fixed)
        changed += 1
print('numpy aliases fixed in', changed, 'files')

# custom_dataset.py imports Path only under `if __name__ == '__main__'`, but uses it in functions we call
custom = Path('/kaggle/working/OpenPCDet/pcdet/datasets/custom/custom_dataset.py')
source = custom.read_text()
if not re.search(r'^from pathlib import Path', source, flags=re.M):
    source = 'from pathlib import Path\\n' + source
    print('custom_dataset.py: added `from pathlib import Path`')
# v2: label names -> the classes the model learns (vn_dataset.yaml CLASS_GROUPS), when the labels are read
if 'QC3D_GROUPS' not in source:
    source = source.replace(
        'gt_names.append(line_list[-1])',
        "gt_names.append(QC3D_GROUPS.get(line_list[-1], line_list[-1]))",
    )
    assert 'QC3D_GROUPS.get' in source, 'custom_dataset.get_label changed: cannot map the class groups'
    source = ("QC3D_GROUPS = {'Truck': 'LargeVehicle', 'Bus': 'LargeVehicle', 'Rider': 'TwoWheeler', "
              "'Motorbike': 'TwoWheeler'}\\n" + source)
    print('custom_dataset.py: class groups', 'Truck/Bus -> LargeVehicle, Rider/Motorbike -> TwoWheeler')
# multi-sweep data (5th column = time lag): in training, half of the samples keep only the newest sweeps
# (sometimes one), so the model also works on a single sweep (CVAT jobs usually come without ego poses)
if 'QC3D_SWEEP_DROP' not in source:
    match = re.search(r'^(\\s*)point_features = np\\.load\\(lidar_file\\)$', source, flags=re.M)
    assert match, 'custom_dataset.get_lidar changed: cannot add the sweep dropout'
    pad = match.group(1)
    source = source.replace(match.group(0), match.group(0) + (
        f'\\n{pad}if getattr(self, "training", False) and point_features.shape[1] > 4 '
        f'and np.random.rand() < QC3D_SWEEP_DROP:'
        f'\\n{pad}    lags = np.unique(point_features[:, 4])'
        f'\\n{pad}    point_features = point_features[point_features[:, 4] <= np.random.choice(lags)]'
    ))
    source = 'QC3D_SWEEP_DROP = 0.5\\n' + source
    print('custom_dataset.py: sweep dropout 0.5 in training')
custom.write_text(source)

# v5, terrain steps (vn_dataset.yaml: random_region_z_shift): one side of a random line moves up or down with
# the objects on it, so the model also finds objects on a road lower or higher than the ego road
# (PHENIKAA val: 24 % of the cars within 30 m stand > 0.8 m below it, 2 % in the training data)
augmentor = Path('/kaggle/working/OpenPCDet/pcdet/datasets/augmentor/data_augmentor.py')
source = augmentor.read_text()
if 'QC3D_REGION_Z' not in source:
    source += '''

def _qc3d_region_z_shift(self, data_dict=None, config=None):  # QC3D_REGION_Z
    if data_dict is None:
        return partial(_qc3d_region_z_shift, self, config=config)
    if np.random.rand() >= config['PROBABILITY']:
        return data_dict
    points, boxes = data_dict['points'], data_dict['gt_boxes']
    angle = np.random.uniform(0, 2 * np.pi)
    normal = np.array([np.cos(angle), np.sin(angle)], dtype=np.float32)
    line = np.random.uniform(*config['LINE_RANGE'])
    shift = np.random.uniform(*config['SHIFT_RANGE'])
    moved = points[:, :2] @ normal > line
    box_moved = boxes[:, :2] @ normal > line
    # a box across the line moves as a whole, with all its points
    across = np.abs(boxes[:, :2] @ normal - line) < np.hypot(boxes[:, 3], boxes[:, 4]) / 2 + 0.1
    for i in np.flatnonzero(across):
        x, y, z, dx, dy, dz, yaw = boxes[i, :7]
        rx, ry = points[:, 0] - x, points[:, 1] - y
        lx, ly = rx * np.cos(yaw) + ry * np.sin(yaw), -rx * np.sin(yaw) + ry * np.cos(yaw)
        inside = (np.abs(lx) <= dx / 2 + 0.1) & (np.abs(ly) <= dy / 2 + 0.1) & (np.abs(points[:, 2] - z) <= dz / 2 + 0.1)
        moved[inside] = box_moved[i]
    points[moved, 2] += shift
    boxes[box_moved, 2] += shift
    data_dict['points'], data_dict['gt_boxes'] = points, boxes
    return data_dict


DataAugmentor.random_region_z_shift = _qc3d_region_z_shift
'''
    augmentor.write_text(source)
    print('data_augmentor.py: random_region_z_shift (terrain steps)')
"""),
        code("""
# compile the CUDA ops in place (full log: /kaggle/working/build.log) and make pcdet importable
%cd /kaggle/working/OpenPCDet
import os, re, site, subprocess, sys
from pathlib import Path

# C++ APIs removed in recent PyTorch, still used by some OpenPCDet ops
for path in Path('pcdet/ops').rglob('*'):
    if path.suffix in ('.cpp', '.cu', '.h', '.cuh'):
        source = path.read_text()
        fixed = re.sub(r'\\.type\\(\\)\\.is_cuda\\(\\)', '.is_cuda()', source)
        fixed = re.sub(r'\\.data<(\\w+)>\\(\\)', r'.data_ptr<\\1>()', fixed)
        if fixed != source:
            path.write_text(fixed)

import glob, shutil, torch
cuda_home = os.environ.get('CUDA_HOME')
if not cuda_home:  # the CUDA toolkit (nvcc) is needed to compile the ops
    nvcc = shutil.which('nvcc') or next(iter(sorted(glob.glob('/usr/local/cuda*/bin/nvcc'))), None)
    cuda_home = str(Path(nvcc).resolve().parents[1]) if nvcc else None
if not cuda_home:
    raise SystemExit('Không thấy CUDA toolkit (nvcc): phiên này chưa bật GPU. GPU_HINT, rồi chạy lại từ đầu')
arch = '%d.%d' % torch.cuda.get_device_capability()  # compile only for this GPU (T4 7.5, P100 6.0, L4 8.9, ...)
print('CUDA_HOME =', cuda_home, '| GPU arch', arch)
env = {**os.environ, 'CUDA_HOME': cuda_home, 'PATH': f"{cuda_home}/bin:" + os.environ.get('PATH', ''),
       'TORCH_CUDA_ARCH_LIST': arch, 'MAX_JOBS': '4'}
with open('/kaggle/working/build.log', 'w') as log:
    proc = subprocess.run([sys.executable, 'setup.py', 'build_ext', '--inplace'],
                          stdout=log, stderr=subprocess.STDOUT, env=env)
built = sorted(p.name.split('.')[0] for p in Path('pcdet/ops').rglob('*.so'))
print(len(built), 'CUDA ops:', built)
if proc.returncode != 0 or not built:
    lines = open('/kaggle/working/build.log').read().splitlines()
    print('\\n'.join([l for l in lines if 'error' in l.lower()][:40] or lines[-40:]))
    raise SystemExit('Biên dịch OpenPCDet lỗi: gửi phần log ở trên')

(Path(site.getsitepackages()[0]) / 'openpcdet.pth').write_text('/kaggle/working/OpenPCDet\\n')
sys.path.insert(0, '/kaggle/working/OpenPCDet')
import pcdet
from pcdet.ops.roiaware_pool3d import roiaware_pool3d_utils  # fails here if the ops are broken
print('pcdet', pcdet.__version__, 'OK')
""".replace("GPU_HINT", GPU_HINT[platform])),
        md("## 2. Nối dữ liệu huấn luyện"),
        code(DATA[platform] + """
dst = Path('/kaggle/working/OpenPCDet/data/custom')
dst.mkdir(parents=True, exist_ok=True)
for name in ('points', 'labels', 'ImageSets'):
    link = dst / name
    if not link.exists():
        link.symlink_to((src if name == 'points' else label_root) / name)
print('data:', src, '| labels:', label_root)
for split in ('train', 'val'):
    print(split, len((dst / 'ImageSets' / f'{split}.txt').read_text().split()), 'frames')
"""),
        write_file_cell("/kaggle/working/OpenPCDet/tools/cfgs/dataset_configs/vn_dataset.yaml", HERE / "vn_dataset.yaml"),
        code("!mkdir -p /kaggle/working/OpenPCDet/tools/cfgs/custom_models"),
        write_file_cell(
            "/kaggle/working/OpenPCDet/tools/cfgs/custom_models/vn_centerpoint_pillar.yaml",
            HERE / "vn_centerpoint_pillar.yaml",
        ),
        code("""
# index files + object database (used to paste rare objects into training scenes)
%cd /kaggle/working/OpenPCDet
import yaml
from easydict import EasyDict
import pcdet.datasets.custom.custom_dataset as custom_dataset
from pcdet.datasets.custom.custom_dataset import create_custom_infos
custom_dataset.Path = Path  # in case the file was imported before the patch above
cfg = EasyDict(yaml.safe_load(open('tools/cfgs/dataset_configs/vn_dataset.yaml')))
root = Path('data/custom')
create_custom_infos(dataset_cfg=cfg, class_names=CLASSES, data_path=root, save_path=root)
!ls -la data/custom
"""),
        md("## 3. Model khởi đầu: CenterPoint-PointPillar đã huấn luyện trên toàn bộ nuScenes (OpenPCDet model zoo)"),
        code("""
# Download the nuScenes weights and map its 10-class heads onto our VN heads, class by class.
# Without them (download blocked), training still runs from scratch.
import glob, subprocess, torch
PRETRAINED_ID = '1UvGm6mROMyJzeSRu7OD1leU_YWoAZG7v'  # cbgs_dyn_pp_centerpoint (nuScenes mAP 50.0)
SRC_HEADS = [['car'], ['truck', 'construction_vehicle'], ['bus', 'trailer'], ['barrier'],
             ['motorcycle', 'bicycle'], ['pedestrian', 'traffic_cone']]
OUR_HEADS = [['Car'], ['LargeVehicle'], ['TwoWheeler'], ['Pedestrian']]  # = vn_centerpoint_pillar.yaml
SOURCE = {'Car': 'car', 'LargeVehicle': 'truck', 'TwoWheeler': 'motorcycle', 'Pedestrian': 'pedestrian'}

src = '/kaggle/working/nuscenes_pp_centerpoint.pth'
if not os.path.exists(src):
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'gdown'])
    subprocess.run(['gdown', PRETRAINED_ID, '-O', src])
""" + PRETRAINED_FALLBACK[platform] + """
PRETRAINED = None
if src:
    state = torch.load(src, map_location='cpu', weights_only=False)['model_state']
    for h, names in enumerate(SRC_HEADS):  # the assumed head layout must match the file
        assert state[f'dense_head.heads_list.{h}.hm.1.weight'].shape[0] == len(names), 'head layout differs'
    where = {c: (h, i) for h, names in enumerate(SRC_HEADS) for i, c in enumerate(names)}
    new = {k: v for k, v in state.items() if not k.startswith('dense_head.heads_list.')}
    for h, names in enumerate(OUR_HEADS):
        prefix = f'dense_head.heads_list.{where[SOURCE[names[0]]][0]}.'
        for key, value in state.items():
            if key.startswith(prefix) and '.vel.' not in key:
                new[f'dense_head.heads_list.{h}.' + key[len(prefix):]] = value.clone()
        for part in ('weight', 'bias'):
            new[f'dense_head.heads_list.{h}.hm.1.{part}'] = torch.stack([
                state[f'dense_head.heads_list.{where[SOURCE[n]][0]}.hm.1.{part}'][where[SOURCE[n]][1]] for n in names
            ])
    PRETRAINED = '/kaggle/working/pretrained_vn.pth'
    torch.save({'model_state': new}, PRETRAINED)
    print('pretrained:', PRETRAINED, len(new), 'tensors')
else:
    print('Không tải được model nuScenes: huấn luyện từ đầu')

# a model of an earlier round (same classes and heads) added as input: learn on from it instead
INIT = sorted(glob.glob(INIT_GLOB, recursive=True), key=lambda f: int(Path(f).stem.rsplit('_', 1)[1]))
if INIT:
    state = torch.load(INIT[-1], map_location='cpu', weights_only=False)['model_state']
    heads = [state.get(f'dense_head.heads_list.{h}.hm.1.weight') for h in range(len(OUR_HEADS))]
    if all(w is not None and w.shape[0] == len(names) for w, names in zip(heads, OUR_HEADS)):
        PRETRAINED = INIT[-1]
        print('học tiếp từ model vòng trước:', PRETRAINED)
    else:
        print('bỏ qua', INIT[-1], ': khác cấu trúc head')
""".replace("INIT_GLOB", INIT_GLOB[platform])),
        md("## 4. Huấn luyện và đánh giá"),
        code("""
# full log in /kaggle/working/train.log; the cell fails if training fails
%cd /kaggle/working/OpenPCDet/tools
import shutil, subprocess, time
""" + OUTPUT_DIR[platform] + """
# 2) preflight (~1-2 min): build data + model, load the pretrained weights, one train step, one inference
#    batch on GPU, so a problem shows now instead of after an hour of training
PREFLIGHT = r'''
import os, sys
os.chdir('/kaggle/working/OpenPCDet/tools')
sys.path.insert(0, '/kaggle/working/OpenPCDet')
import torch
from pcdet.config import cfg, cfg_from_yaml_file
from pcdet.datasets import build_dataloader
from pcdet.models import build_network, load_data_to_gpu
from pcdet.utils import common_utils
cfg_from_yaml_file('cfgs/custom_models/vn_centerpoint_pillar.yaml', cfg)
logger = common_utils.create_logger()
train_set, train_loader, _ = build_dataloader(dataset_cfg=cfg.DATA_CONFIG, class_names=cfg.CLASS_NAMES,
                                              batch_size=2, dist=False, workers=0, logger=logger, training=True)
model = build_network(model_cfg=cfg.MODEL, num_class=len(cfg.CLASS_NAMES), dataset=train_set).cuda()
if len(sys.argv) > 1:
    model.load_params_from_file(filename=sys.argv[1], logger=logger, to_cpu=False)
model.train()
batch = next(iter(train_loader))
load_data_to_gpu(batch)
ret, _, _ = model(batch)
ret['loss'].backward()
print('PREFLIGHT train step OK, loss', float(ret['loss']))
sys.path.insert(0, '/kaggle/working/OpenPCDet/tools')
from train_utils.optimization import build_optimizer
from train_utils.train_utils import checkpoint_state, save_checkpoint
optimizer = build_optimizer(model, cfg.OPTIMIZATION)
optimizer.step()
save_checkpoint(checkpoint_state(model, optimizer, 1, 1), filename='/kaggle/working/preflight_ckpt')
model.load_params_from_file(filename='/kaggle/working/preflight_ckpt.pth', logger=logger, to_cpu=False)
print('PREFLIGHT checkpoint save + load OK')
test_set, test_loader, _ = build_dataloader(dataset_cfg=cfg.DATA_CONFIG, class_names=cfg.CLASS_NAMES,
                                            batch_size=2, dist=False, workers=0, logger=logger, training=False)
model.eval()
batch = next(iter(test_loader))
load_data_to_gpu(batch)
with torch.no_grad():
    pred_dicts, _ = model(batch)
annos = test_set.generate_prediction_dicts(batch, pred_dicts, cfg.CLASS_NAMES)
print('PREFLIGHT inference OK,', len(annos[0]['name']), 'boxes on the first frame')
'''
Path('/kaggle/working/preflight.py').write_text(PREFLIGHT)
# PyTorch >= 2.6 loads with weights_only=True, which rejects the numpy scalars OpenPCDet stores in its own
# checkpoints; these files are produced by this notebook, so a full load is safe
RUN_ENV = {**os.environ, 'TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD': '1'}
check = subprocess.run([sys.executable, '/kaggle/working/preflight.py'] + ([PRETRAINED] if PRETRAINED else []),
                       capture_output=True, text=True, env=RUN_ENV)
print('\\n'.join(l for l in (check.stdout + check.stderr).splitlines()
                if 'PREFLIGHT' in l or 'loaded' in l or 'Error' in l or 'error' in l)[-3000:])
if check.returncode != 0:
    print((check.stdout + check.stderr)[-4000:])
    raise SystemExit('Preflight lỗi: gửi phần log ở trên (chưa tốn thời gian huấn luyện)')

# 3) the real training run, on every GPU (T4 x2: about twice as fast); a progress line every 5 minutes
def train_cmd(gpus):
    args = ['train.py', '--cfg_file', 'cfgs/custom_models/vn_centerpoint_pillar.yaml',
            '--batch_size', str(BATCH_SIZE * gpus), '--epochs', str(EPOCHS), '--workers', '2', '--extra_tag', 'TAG'EXTRA_ARGS]
    if PRETRAINED:
        args += ['--pretrained_model', PRETRAINED]
    if gpus > 1:
        return [sys.executable, '-m', 'torch.distributed.run', '--nproc_per_node', str(gpus), '--master_port', '29511'] + args + ['--launcher', 'pytorch']
    return [sys.executable] + args

def run_training(gpus):
    with open('/kaggle/working/train.log', 'w') as log:
        proc = subprocess.Popen(train_cmd(gpus), stdout=log, stderr=subprocess.STDOUT, env=RUN_ENV)
        while proc.poll() is None:
            time.sleep(300)
            tail = open('/kaggle/working/train.log', 'rb').read()[-4000:].decode(errors='ignore')
            lines = [l.strip() for l in tail.replace('\\r', '\\n').splitlines() if l.strip()]
            print(time.strftime('%H:%M'), lines[-1][:200] if lines else '...', flush=True)
    return proc

GPUS = torch.cuda.device_count()
print('GPU:', GPUS)
proc = run_training(GPUS)
run_out = Path('/kaggle/working/OpenPCDet/output/custom_models/vn_centerpoint_pillar')
if proc.returncode != 0 and GPUS > 1 and not list(run_out.glob('*/ckpt/checkpoint_epoch_*.pth')):
    # multi-GPU failed before the first epoch was saved: same run on one GPU
    print(open('/kaggle/working/train.log', errors='ignore').read()[-3000:])
    print('Chạy nhiều GPU lỗi trước epoch đầu: chạy lại trên 1 GPU')
    shutil.copy('/kaggle/working/train.log', '/kaggle/working/train_multi_gpu_failed.log')
    shutil.rmtree(run_out, ignore_errors=True)
    proc = run_training(1)
!grep -E "==> Done \\(loaded|Not updated|==> Loading parameters from checkpoint" /kaggle/working/train.log | head -n 20
!tail -n 60 /kaggle/working/train.log
assert proc.returncode == 0, 'Huấn luyện lỗi: xem log ở trên (đầy đủ trong /kaggle/working/train.log)'
""".replace("'TAG'EXTRA_ARGS", f"'{tag}'" + EXTRA_TRAIN_ARGS[platform])),
        code("""
# keep the useful outputs (checkpoints are large: only the last two)
import re, shutil
out = Path('/kaggle/working/OpenPCDet/output/custom_models/vn_centerpoint_pillar/TAG')
RESULTS.mkdir(parents=True, exist_ok=True)
# checkpoint_epoch_<n>.pth by epoch number (the ckpt dir also holds latest_model.pth, without a number)
ckpts = sorted(out.glob('ckpt/checkpoint_epoch_*.pth'), key=lambda f: int(f.stem.rsplit('_', 1)[1]))
assert ckpts, 'Không có checkpoint: bước huấn luyện chưa chạy xong'
for ckpt in ckpts[-2:]:
    shutil.copy(ckpt, RESULTS)
for log in out.rglob('*.txt'):
    shutil.copy(log, RESULTS / log.name)
for f in out.rglob('result.pkl'):
    epoch = next((part for part in f.parts if part.startswith('epoch_')), 'eval')
    shutil.copy(f, RESULTS / f'{epoch}_result.pkl')
shutil.copy('/kaggle/working/train.log', RESULTS)
print('Kết quả ở', RESULTS)
for f in sorted(RESULTS.iterdir()):
    print(f'  {f.name}  {f.stat().st_size / 2**20:.1f} MB')
!grep -hE "AP|recall" "{RESULTS}"/*.txt "{RESULTS}"/train.log | tail -n 40
""".replace("/TAG'", f"/{tag}'")),
    ]


def localize(cell: dict, platform: str) -> dict:
    cell["source"] = [line.replace("/kaggle/working", WORK[platform]) for line in cell["source"]]
    return cell


for platform, name in (("kaggle", "train_vn_centerpoint.ipynb"), ("colab", "train_vn_centerpoint_colab.ipynb")):
    notebook = {
        "cells": [localize(cell, platform) for cell in build_cells(platform)],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
            **({"accelerator": "GPU", "colab": {"gpuType": "T4", "provenance": []}} if platform == "colab" else {}),
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    target = HERE / name
    target.write_text(json.dumps(notebook, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", target)
