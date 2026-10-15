# Dữ liệu huấn luyện và model phát hiện 3D

Mục tiêu: model phát hiện 3D theo bộ class giao thông VN, dùng để QC 3D so sánh với nhãn của annotator (bắt thiếu box, thừa box, sai class, box lệch).

## 1. Nguồn dữ liệu

| Nguồn | Lấy ở đâu | Dùng | Giấy phép |
| --- | --- | --- | --- |
| PHENIKAA Mini (12 scene, 2.400 frame có nhãn) | Google Drive của PHENIKAA-X, tải bằng rclone (chỉ đọc) | Dữ liệu đường VN; 2 scene giữ riêng làm tập kiểm định | Theo điều khoản PHENIKAA-X (nghiên cứu) |
| nuScenes-mini (10 scene, 404 frame) | nuscenes.org (đăng ký tài khoản) | Thêm Truck, Bus, xe 2 bánh đỗ | CC BY-NC-SA 4.0, phi thương mại |
| Model CenterPoint-PointPillar huấn luyện trên toàn bộ nuScenes | OpenPCDet model zoo | Điểm khởi đầu (fine-tune) | Code Apache 2.0; trọng số học từ nuScenes |

PARKING AREA, ROUNDABOUT, SUPER CROWED không có LiDAR ngay trên Drive gốc; RAINY IN INTERSECTION và RUST HOUR chỉ có một phần. Dùng được 1.543 frame PHENIKAA. Muốn đủ: hỏi `opendataset@phenikaa-x.com`.

Tải PHENIKAA (chỉ LiDAR, nhãn, ego-pose; bỏ camera):

```powershell
rclone config create gdrive drive scope=drive.readonly       # đăng nhập Google một lần, quyền chỉ đọc
rclone copy gdrive: D:\dataset\phenikaa_mini --drive-root-folder-id 1G490BK62MlmYhMMlapE_Smm6JgO3WmnJ `
  --include "*/lidar/**" --include "*/label/**" --include "*/ego_pose/**" --include "*/Summary.txt" `
  --transfers 24 --checkers 32 --drive-pacer-min-sleep 10ms --drive-pacer-burst 200
```

## 2. Bộ class và chuẩn hoá (`utils/qc3d/build_train_set.py`)

Class: Car, Truck, Bus, Rider (xe 2 bánh có người), Motorbike (xe 2 bánh đỗ), Pedestrian.

| Chuẩn hoá | Lý do |
| --- | --- |
| Mặt đường đưa về z = -1,8 m, một độ dịch cho mỗi nguồn (trung vị đáy box trong 30 m) | Cảm biến lắp ở độ cao khác nhau; ước lượng theo từng frame sai ~0,35 m |
| Điểm: x, y, z, intensity 0-255, cột thời gian (0) | Giống nuScenes, để dùng lại trọng số nuScenes |
| Bỏ điểm và box xa hơn 54 m | Cùng phạm vi cho hai nguồn |
| Bỏ box hỏng (kích thước ≤ 0, NaN) | PHENIKAA có 71 box như vậy |
| PHENIKAA "Car" cỡ xe tải / xe buýt → Truck / Bus | PHENIKAA không có class Truck/Bus; tiêu chí giống rule R11 (chênh log-likelihood kích thước ≥ 4). Khoảng 8,6% Car → Truck (trung vị 6,0 × 2,4 × 2,8 m), 2% → Bus (10,1 × 2,9 × 3,5 m) |
| nuScenes motorcycle/bicycle: có người → Rider, không người → Motorbike | Đúng định nghĩa class VN |

Chạy toàn bộ (kiểm tra tải về, tạo bộ dữ liệu, QC mẫu nhãn PHENIKAA, nén cho Kaggle, báo cáo):

```powershell
.venv-qc3d\Scripts\python utils\qc3d\run_after_download.py --phenikaa D:\dataset\phenikaa_mini `
  --nuscenes D:\dataset\nuscenes --out D:\dataset\train_vn_v3 --zip D:\dataset\train_vn_kaggle_v3.zip `
  --report D:\dataset\pipeline_report.md
```

`utils/qc3d/overnight.ps1` chạy cùng các bước không cần người: giữ máy thức (không đổi power plan), chờ tải xong, tải bù, xử lý, rồi hẹn tắt máy sau 10 phút (`shutdown /a` để huỷ).

## 3. Kiểm tra nhãn PHENIKAA bằng QC 3D

Kiểm mẫu cho thấy nhãn PHENIKAA cần xem lại trước khi coi là chuẩn: box Car cỡ Truck/Bus (đã xử lý ở mục 2), box không ôm sát vật (R10), một phần box lệch mặt đường > 0,3 m trong 40 m (R09). Báo cáo chi tiết: `D:\dataset\pipeline_report.md`.

Phân tích R09 dẫn tới 2 sửa trong QC (rules 2026.10.6):

- Đo mặt đường bỏ điểm trong chân box: với vật gần, điểm thân xe nhiều hơn điểm đường và làm mặt đường "cao lên".
- R09 chỉ xét box trong 40 m (như R10): xa hơn hầu như không có điểm mặt đường để đo.

Trên mẫu nhãn PHENIKAA, tỉ lệ box bị R09 giảm từ 16,7% xuống 8,1%. Khi đo đúng (phân vị 10% của vành 1-3 m quanh xe), đáy box Car PHENIKAA trong 30 m lệch mặt đường trung vị +0,03 m, 92% trong ±0,3 m. Bài thử lỗi chèn giữ nguyên: phát hiện 98,8%, không cảnh báo sai mới, lơ lửng / chìm 100%.

## 4. Huấn luyện trên Kaggle (GPU miễn phí)

Notebook: `utils/qc3d/kaggle/train_vn_centerpoint.ipynb` (sinh từ `make_notebook.py`; cấu hình `vn_dataset.yaml`, `vn_centerpoint_pillar.yaml`).

1. Kaggle dataset `train-vn` (Private) → New Version → upload `train_vn_kaggle_v3.zip`.
2. Notebook → File → Import Notebook → Accelerator GPU T4 x2, Internet On → Run All (~30 phút với nuScenes-mini; lâu hơn với PHENIKAA).
3. Tải `results/epoch_20_result.pkl` và `checkpoint_epoch_20.pth`.
4. Đánh giá: `python utils/qc3d/eval_predictions.py --result epoch_20_result.pkl --labels D:/dataset/train_vn_v3/labels`.

Google Colab (`train_vn_centerpoint_colab.ipynb`, cùng nội dung, sinh từ cùng `make_notebook.py`):

1. Upload `train_vn_kaggle_v3.zip` vào Google Drive, thư mục `My Drive/qc3d`.
2. colab.research.google.com → File → Upload notebook → chọn file `.ipynb` → Runtime → Change runtime type → T4 GPU.
3. Runtime → Run all, cho phép truy cập Drive. Giữ tab mở; cứ 5 phút in một dòng tiến độ.
4. Bị ngắt: Run all lại, học tiếp từ checkpoint trên Drive (`qc3d/output`). Kết quả: `qc3d/results`.

Notebook tự xử lý môi trường Kaggle / Colab (Python 3.13, CUDA 12.8): biên dịch CUDA ops tại chỗ, thay `spconv` bằng stub + bộ chia pillar numpy (`voxel_generator.py`, khớp từng giá trị với bản tham chiếu), sửa API cũ của PyTorch/numpy, và chạy thử 1-2 phút (preflight: 1 bước huấn luyện, lưu/đọc checkpoint, 1 lượt dự đoán) trước khi huấn luyện thật.

## 5. Kết quả đã đo (tập kiểm định nuScenes-mini, 81 frame, khớp theo tâm 2 m)

| | Học từ đầu (40 epoch) | Fine-tune từ nuScenes (20 epoch) |
| --- | --- | --- |
| Car AP | 63,1% | 82,6% |
| Car < 20 m: recall / precision | 79% / 88% | 95% / 95% |
| Bus AP | 12,9% | 88,1% |
| Pedestrian AP | 43,0% | 73,2% |
| Car lệch hướng trung vị | 24,7° | 6,1° (20% ngược 180°) |

Chưa có PHENIKAA trong lần đo này; lần huấn luyện tới dùng `train_vn_kaggle_v3.zip`.

## 6. Việc tiếp theo

1. Huấn luyện với bộ v3 (PHENIKAA + nuScenes), đánh giá riêng trên 2 scene PHENIKAA giữ riêng.
2. Gộp nhiều lần quét (multi-sweep) để giảm lỗi ngược hướng.
3. Rule R12 trong QC 3D: so dự đoán model với nhãn (K-fold, ngưỡng theo class đo trên tập vàng).
4. Tập vàng 300-500 frame do QA kiểm hai lần để đo độ chính xác thật.
