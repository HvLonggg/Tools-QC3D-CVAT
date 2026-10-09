# Dữ liệu PHENIKAA Mini: kiểm tra và nhập vào CVAT

Nguồn: [phenikaa-dataset/phenikaa-devkit](https://github.com/phenikaa-dataset/phenikaa-devkit), giấy phép CC BY-NC-SA 4.0 (chỉ dùng nghiên cứu / prototype).
Bản tải: `D:\dataset` (Mini Dataset + Calib Information, 788 MB), kiểm tra ngày 09/10/2026.

## 1. Bản Mini có gì

| Cảnh | LiDAR (.laz) | Nhãn | Ego-pose | Camera |
| --- | --- | --- | --- | --- |
| RESIDENTIAL AREA | 84 frame | 2 file | 96 file | 1.201 ảnh |
| COMPLEX CITY | 1 frame | — | — | 1.965 ảnh |
| NIGHT TIME | 1 frame | — | — | 0 |
| COMPLEX URBAN | — | — | — | 1.969 ảnh |

Chỉ **1 frame** có cả LiDAR và nhãn (`1761297455-299725294`, 114 cuboid: Car 51, Rider 47, Pedestrian 16).
File nhãn `1761297455-399725437` không có LiDAR tương ứng (bản Mini thiếu frame 455.4–455.6).

Kết luận: bản Mini đủ để dựng và kiểm thử pipeline, QC rule, giao diện; **không đủ để train hay đánh giá model**.

## 2. Định dạng đã xác minh

| Thành phần | Định dạng | Cách xác minh |
| --- | --- | --- |
| Point cloud | LAZ, khoảng 256 nghìn điểm/frame, x y z intensity ring timestamp, tầm ±245 m | Đọc bằng `laspy` |
| Nhãn | `x y z dài rộng cao yaw class`, 1 dòng/box | Đếm cột: 229/229 dòng có 8 cột |
| `z` | Tâm box | Đáy box trung bình −0,11 m, khớp mặt đường −0,08 m; box gần có trung vị 768 điểm/box |
| `yaw` | Rad, quanh trục z, ánh xạ thẳng sang CVAT `rotation = [0, 0, yaw]` | Đo bằng số chưa phân biệt được dấu (ô tô gần đều yaw ≈ 0 hoặc π). Kiểm tra bằng mắt trên CVAT ngày 09/10/2026 (task 25, frame 41): box ôm sát cụm điểm, chiều dài dọc thân xe, kể cả xe trên đoạn đường cong |
| Ego-pose | Ma trận 3×4 cảm biến → thế giới | 12 số/file |

## 3. Phát hiện cho QC (đầu vào cho rule R05)

Box ở xa thường không có điểm LiDAR, nên ngưỡng "ít điểm" phải theo khoảng cách, không dùng 1 ngưỡng chung.

| Class | 0–30 m | 30–60 m | 60–100 m | > 100 m |
| --- | --- | --- | --- | --- |
| Car (box rỗng / tổng) | 0/9 | 0/9 | 3/16 | 12/17 |
| Rider | 0/10 | 3/11 | 18/22 | 0/4 |
| Pedestrian | 0/1 | 7/10 | 5/5 | — |

Số liệu chi tiết: `report.json` do bộ chuyển đổi sinh ra.

## 4. Chuyển đổi

```powershell
.venv-qc3d\Scripts\python utils\qc3d\phenikaa_to_cvat.py --scene "D:\dataset\Mini Dataset\RESIDENTIAL AREA" --out D:\dataset\cvat\residential
```

Kết quả trong `D:\dataset\cvat\residential`: `pointclouds.zip` (84 file PCD, 155 MB), `annotations_datumaro3d.zip`, `ego_poses.json`, `report.json`.
File nhãn đã được kiểm tra bằng Datumaro trong `cvat_server`: đọc được 1 item, 114 `Cuboid3d`, 3 label.

## 5. Nhập vào CVAT

1. **Projects → + → Create a new project**: tên `QC3D-PHENIKAA`, thêm 3 label `Car`, `Rider`, `Pedestrian`.
2. Trong project: **+ → Create a new task**: tên `phenikaa-residential`, kéo thả `pointclouds.zip`. Mở **Advanced configuration**, đặt **Chunk size = 4**, bấm **Submit & Open**. CVAT tự nhận dạng task 3D.
3. Đợi task xử lý xong, vào **Actions → Upload annotations**, chọn định dạng **Datumaro 3D 1.0**, chọn `annotations_datumaro3d.zip`.
4. Mở job, đi tới **frame 41**: thấy 114 cuboid trên point cloud.

Tiêu chí đạt: box ô tô gần nằm trùng cụm điểm; hướng box trùng hướng xe (đây là bước xác nhận dấu `yaw`).

Lỗi thường gặp:

| Thông báo | Nguyên nhân | Cách xử lý |
| --- | --- | --- |
| `Could not create the task · Exception: No image found` | Ở bước tạo task đã chọn nhầm `annotations_datumaro3d.zip` (chỉ chứa nhãn, không có point cloud) | Tạo lại task với `pointclouds.zip`; nhãn upload sau ở bước 3 |
| Mở job báo `Could not receive image data` (500), log có `redis.exceptions.TimeoutError` | CVAT tự đặt chunk size 72 → mỗi chunk khoảng 290 MB (72 frame × 4 MB), ghi cache bị timeout | Tạo lại task với **Chunk size = 4** (khoảng 16 MB/chunk) |
| Nhãn không gán được label | Project thiếu label hoặc sai chính tả | Label phải đúng `Car`, `Rider`, `Pedestrian` |

Kết quả thực tế ngày 09/10/2026: task 25 `phenikaa-residential` (project 5, job 23), 3D, 84 frame, chunk size 4; import đạt 114 cuboid tại frame 41 (Car 51, Rider 47, Pedestrian 16); chunk 0 và chunk chứa frame 41 tải về HTTP 200, khoảng 16 MB mỗi chunk.
