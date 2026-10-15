# Model phát hiện 3D trong QC (rule R12) và lần huấn luyện v2

## 1. Chạy model không cần GPU

Model CenterPoint-Pillar đã huấn luyện chạy bằng numpy ngay trong server CVAT:
- `qc/checkpoint.py` đọc file `.pth` của OpenPCDet mà không cần PyTorch; đọc được cả định dạng zip lẫn định dạng cũ.
- `qc/detector.py` dựng lại từng lớp mạng:
  - PillarVFE → PointPillarScatter → BaseBEVBackbone → CenterHead;
  - giải mã top-K;
  - NMS xoay trên BEV.

**Kiểm chứng:** trên 3 frame kiểm định (2 PHENIKAA, 1 nuScenes), cả 278 box trùng **từng box** với `result.pkl` do OpenPCDet chạy GPU trên Colab tạo ra: cùng class, sai số kích thước 0,0000.

**Tốc độ:** khoảng 4,5 giây mỗi frame trên laptop, khoảng 6 giây trong Docker.

Cài trọng số một lần, file `.npz` 21 MB không đưa vào git:

```powershell
.venv-qc3d\Scripts\python utils\qc3d\install_model.py --pth checkpoint_epoch_20.pth --key vn-cp-pillar-e20
docker restart cvat_server
```

## 2. Dùng trong QC

1. Mở job 3D → tab QC → khung **"Model AI so với nhãn (R12)"** → **Chạy model**. Model chạy nền và lưu kết quả từng frame vào `QcDetection`. API: `GET/POST /api/qc3d/jobs/<id>/detect`; lệnh tương đương: `manage.py qc3d_detect --job <id>`.
2. Chạy xong, bấm **Chạy lại QC**: rule **R12** so model với nhãn.

**Mặt đường:** model học với mặt đường ở z = −1,8 m. Mỗi job được đưa về mức này bằng trung vị đáy box trong 30 m, giống cách chuẩn hoá dữ liệu train. Job có ít nhãn thì đo từ điểm LiDAR.

R12 **chỉ dùng phần model đáng tin**: Car và Rider trong 40 m, là vùng model đúng 85–96% trên đường VN.

| Cảnh báo | Điều kiện | Đề xuất sửa |
| --- | --- | --- |
| Có thể thiếu nhãn | model tin ≥ 0,6, không có box nào trong 1,5 m | Không (QC xem tay, không tự tạo box) |
| Sai class | model tin ≥ 0,4 thấy class khác trên một box, và không thấy class của nhãn ở đó | Đổi class, qua bước kiểm chứng Autofix |

Frame có hơn 8 vật thiếu nhãn (`model_missing_per_frame`) được gộp thành **một cảnh báo cho cả frame**, ví dụ "Frame chưa gán nhãn đủ: model thấy 40 vật không có box (12 Car, 28 Rider)". Job 23 (1,45 nhãn/frame) từ 2.133 cảnh báo lẻ còn 83 cảnh báo theo frame.

Ngưỡng đổi được trong guideline (tab Ngưỡng): `model_max_distance`, `model_missing_score`, `model_class_score`, `model_missing_per_frame`.

Kiểm tra hệ toạ độ trên job 23: model bắt lại 32/34 box Car/Rider đã có nhãn trong 40 m, lệch độ cao trung vị 4 mm.

**Đo trên 120 frame của 2 scene PHENIKAA giữ riêng:**
- Bắt nhãn bị xoá (Car/Rider trong 40 m): 48%.
- Bắt nhãn đổi Car ↔ Rider: 74%.
- Trên nhãn gốc: 0,69 cảnh báo/frame. Một phần có thể là nhãn PHENIKAA thiếu thật: trong 47 dự đoán người đi bộ tin cậy không có nhãn, 17 có hình dạng giống người.

## 3. Kết quả model v1 (epoch 20) trên đường VN

AP khớp theo tâm 2 m:

| Class | PHENIKAA | nuScenes | Ghi chú |
| --- | --- | --- | --- |
| Car | 68,4 | 80,2 | |
| Rider | 78,5 | 21,9 | nuScenes chỉ có 14 rider |
| Pedestrian | 10,3 | 71,2 | Trên PHENIKAA báo đè lên người lái xe máy |
| Truck | 1,3 | 46,8 | 144/149 xe tải gần bị gọi là Bus |
| Bus | 0 | 89,6 | PHENIKAA val không có xe buýt gần |
| Motorbike | — | 16,3 | Chỉ 108 box để train |
| **Gộp Truck+Bus (xe lớn)** | **60,2** | 58,6 | Bắt được 100% xe lớn trong 20 m |
| **Gộp Rider+Motorbike (xe 2 bánh)** | **78,4** | 22,9 | |

## 4. Dữ liệu đã đủ chưa (train_vn_v3)

| Class | Box để train | Trong 20 m | Đánh giá |
| --- | --- | --- | --- |
| Car | 29.712 | 7.079 | Dư |
| Rider | 49.176 | 15.761 | Dư |
| Pedestrian | 9.702 | 3.373 | Đủ |
| Truck | 3.003 | 636 | Tạm |
| Bus | 579 | 72 | Mỏng |
| Motorbike | 108 | 49 | **Không đủ** |

PHENIKAA không có nhãn Truck/Bus thật: hai class này được suy ra từ kích thước, nên model không thể học cách tách tốt hơn chính quy tắc suy ra. Motorbike quá ít để thành một class riêng.

## 5. Huấn luyện v2

Model học **4 nhóm** mà dữ liệu đủ dày: Car, LargeVehicle (Truck+Bus), TwoWheeler (Rider+Motorbike), Pedestrian. QC tách nhóm thành class chi tiết bằng chính quy tắc của guideline (`detector.split_group`):
- Truck / Bus theo kích thước, như rule R11;
- Rider / Motorbike theo chiều cao 1,25 m.

Những điểm khác so với v1:
- **Không upload lại dữ liệu:** notebook gộp tên nhãn khi đọc (vn_dataset.yaml `CLASS_GROUPS`).
- **30 epoch** thay vì 20, vì loss v1 vẫn còn giảm ở epoch 20.
- Khởi tạo từ nuScenes:

| Nhóm v2 | Lấy trọng số từ đầu ra nuScenes |
| --- | --- |
| LargeVehicle | truck |
| TwoWheeler | motorcycle |

- Thư mục kết quả riêng `.../colab_v2`, không học tiếp nhầm từ checkpoint v1.

**Các bước:**
1. Colab: Upload notebook `train_vn_centerpoint_colab.ipynb` (bản mới) → Run all. Dữ liệu trên Drive giữ nguyên. Thời gian khoảng 4 giờ.
2. Chấm điểm:

```powershell
.venv-qc3d\Scripts\python utils\qc3d\eval_predictions.py --result result.pkl --labels D:\dataset\train_vn_v3\labels --source phx --groups
```

3. Tốt hơn v1 thì cài vào CVAT:

```powershell
.venv-qc3d\Scripts\python utils\qc3d\install_model.py --pth checkpoint_epoch_30.pth --key vn-cp-pillar-v2
docker restart cvat_server
```

Server tự dùng model mới nhất đã cài.

### Các vòng đã chạy (Kaggle, 2 GPU T4)

AP 2 m trên 2 scene PHENIKAA giữ riêng (`--source phx --groups`):

| Model | Key trong CVAT | Car | Xe lớn | Xe 2 bánh | Người đi bộ | Ghi chú |
| --- | --- | --- | --- | --- | --- | --- |
| v1 (epoch 20) | `vn-cp-pillar-e20` | 68,4 | 60,2 | 78,4 | 10,3 | 1 lần quét |
| v3 (30 epoch) | `vn-cp-pillar-v2` | 69,1 | 62,9 | 82,4 | 16,0 | 4 lần quét, 4 nhóm class, nhãn xa đề xuất |
| **v5 (15 epoch, học tiếp v3)** | **`vn-cp-pillar-v5`** (đang dùng) | **75,9** | **73,3** | **85,6** | 15,6 | Dời độ cao từng vùng (đường cao thấp khác nhau); giới hạn 200 box mỗi nhóm |

So với v3, v5:
- bắt được 46% xe ở nền thấp trong 30 m (v3: 25%);
- giảm số lần đoán xe con thành xe lớn từ 94 xuống 20.

Mỗi model có key riêng, vì CVAT lưu kết quả model của từng frame theo key. Model mới vì vậy được chạy lại trên mọi frame. Muốn quay về v3: đổi tên hoặc xoá `vn-cp-pillar-v5.npz`, rồi `docker restart cvat_server`.

## 6. Việc tiếp theo để model tốt hơn nữa

- **Xe máy đỗ (Motorbike):** cần nhãn thật. Cách rẻ nhất là gán nhãn ngay trên CVAT vài trăm xe đỗ ở PHENIKAA, R12 sẽ gợi ý chỗ có xe.
- **Nhãn PHENIKAA còn thiếu** (người đi bộ, xe): dùng R12 để tìm, sửa, rồi train lại. Nhãn sạch hơn thì model tốt hơn.
- **Thiếu LiDAR:** 3 scene PHENIKAA chưa có LiDAR (PARKING AREA, ROUNDABOUT, SUPER CROWED); xin thêm từ `opendataset@phenikaa-x.com`.

## 7. Sửa box và thêm box mới (Duyệt, Autofix)

**Một bản sửa đầy đủ cho mỗi đối tượng.** Mọi lỗi của một box (vị trí, kích thước, góc, class, lơ lửng/chìm, khác model) dùng chung một đề xuất, nên bấm "Áp dụng sửa" một lần là sửa hết.

**Đề xuất được chọn từ nhiều phương án** (`rules._consolidate_proposals`), mỗi phương án được kiểm chứng theo guideline (`qc/verify.py`), phương án ít lỗi nhất được chọn:

| Phương án | Khi nào thắng |
| --- | --- |
| Theo điểm LiDAR (neo mặt LiDAR thấy, cắt mặt thừa, đặt đáy lên đường) | Mặc định: chính xác nhất khi box còn ôm phần lớn vật |
| Theo model (vị trí, kích thước, hướng của box model) | Khi cách theo điểm không qua kiểm chứng: box vẽ sai chỗ, sai cỡ, sai class |
| Chỉ đổi class | Lỗi chỉ ở class (R11 / R12) |
| Xoá (QC duyệt) | Box gần như không có điểm LiDAR và không phương án nào sửa được |

**Quy tắc an toàn khi dùng model:**
- Giữ hướng của nhãn khi model chỉ lệch 180°, vì model hay nhầm đầu và đuôi xe.
- Class lấy theo guideline: dùng class của model với Car/Rider; class khác thì quyết theo kích thước.
- Chỉ đổi class khi tâm vật model thấy **nằm trong box đã gán**: một box bị dời sang vật bên cạnh không được lấy class của vật đó.
- Giới hạn dời 1,5 m chỉ tính theo mặt phẳng ngang; hạ box xuống mặt đường được kiểm bằng phép đo mặt đường.

**Thêm box mới** cho vật model thấy mà chưa có nhãn (đề xuất của R12):
- Đặt đáy box lên mặt đường đo tại chỗ.
- Kích thước phải nằm trong khoảng của guideline.
- Không đè lên nhãn có sẵn (IoU ≤ 0,05).
- Có ít nhất 10 điểm LiDAR.
- Box nào chưa đạt thì bỏ và ghi lý do.
- Hoàn tác sẽ xoá đúng các box vừa thêm.

Tab Autofix:
- "Áp dụng cho class: Tất cả / Chọn class".
- Công tắc "Thêm box mới".
- Xem trước số box sẽ sửa và số box sẽ thêm.

**Đo trên lỗi chèn** (có model, 10 lần × 5 lỗi/loại), chỉ tính bản Autofix tự ghi:

| Lỗi | Tự sửa | Khớp box gốc ≥ 0,7 |
| --- | --- | --- |
| Dời box 1–3 m | 13/41 (trước: 10/38) | 100% |
| Box nới rộng | 48/48 | 94% |
| Lơ lửng | 50/50 | 96% |
| Chìm | 47/50 | 98% |
| Sai class | 50/50 | 49/50 đúng class |

**Job 23:**
- Box "Pedestrian" 2,7×2,4×1,0 m lơ lửng 3,4 m được sửa thành Car 4,85×2,11×1,69 m đặt trên mặt đường, và qua kiểm chứng.
- Box rác 1×1×1 m được đề xuất xoá.
- Thêm box trên frame 0 với class Rider bị loại trừ: thêm đúng 12 Car; hoàn tác xoá đúng 12 box đó.
