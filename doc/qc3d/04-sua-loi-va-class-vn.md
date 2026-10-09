# Sprint 3: class giao thông VN, phát hiện và sửa lỗi annotator

Mục tiêu: phát hiện đúng các lỗi annotator hay mắc trên cuboid 3D, đề xuất bản sửa dựa trên point cloud, reviewer bấm **Áp dụng vào CVAT** (có **Hoàn tác**).

## 1. Bộ class giao thông Việt Nam (`qc/taxonomy.py`, version `vn-2026.10.1`)

| Class | Tên | Bao gồm | Dài (m) | Rộng (m) | Cao (m) |
| --- | --- | --- | --- | --- | --- |
| Car | Ô tô con | Sedan, SUV, MPV, bán tải, taxi, van ≤ 9 chỗ | 2,5–6,2 | 1,4–2,3 | 1,2–2,5 |
| Truck | Xe tải | Tải nhẹ đến nặng, xe ben, đầu kéo, chuyên dụng | 4,5–13 | 1,7–2,8 | 2,0–4,3 |
| Bus | Xe khách / buýt | Xe buýt, xe khách ≥ 16 chỗ | 6,5–13 | 2,0–2,7 | 2,6–4,2 |
| Rider | Người đi xe máy / xe đạp | Xe máy, xe điện, xe đạp kèm người | 1,1–2,6 | 0,35–1,3 | 1,0–2,2 |
| Pedestrian | Người đi bộ | Người đứng / đi bộ | 0,2–1,3 | 0,2–1,3 | 0,9–2,1 |

Đề xuất, **chờ QA xác nhận**. Project `QC3D-PHENIKAA` đã thêm label `Truck`, `Bus` (type cuboid).
Hướng mở rộng khi có guideline: tách `Motorbike` / `Bicycle`, thêm xe ba gác, xe cứu thương, container.

## 2. Lỗi annotator và cách xử lý

| Lỗi | Rule | Phát hiện bằng | Đề xuất sửa |
| --- | --- | --- | --- |
| Box lệch khỏi vật | R05, R10 | Ít điểm so với khoảng cách; mặt hướng cảm biến hở so với cụm điểm | Tìm cụm điểm của vật (phân cụm BEV, loại điểm của vật khác), neo box vào mặt nhìn thấy, giữ kích thước |
| Box rộng / cao hơn vật | R10 | Khe hở ở mặt hướng cảm biến > max(0,35 m; 10%); đỉnh box cao hơn điểm cao nhất > max(0,4 m; 25%) | Co về kích thước trung bình class, không nhỏ hơn cụm điểm |
| Box lơ lửng / chìm | R09 | Đáy box lệch mặt đường đo tại chỗ > 0,3 m | Đặt đáy box lên mặt đường |
| Box đè nhau | R02 | IoU 3D > 0,3; trùng tâm ≤ 5 cm | Xoá bản trùng |
| Sai class | R11 | Kích thước giống class khác hơn rõ rệt (log-likelihood chênh ≥ 4) | Đổi class (nếu project có class đó) |
| Kích thước sai guideline | R06 | Ngoài khoảng của class | Người quyết |

Vì LiDAR chỉ thấy mặt quay về cảm biến, bản sửa không co box theo cụm điểm mà neo vào mặt nhìn thấy và giữ kích thước theo class.

## 3. Đo trên tập lỗi chèn (PHENIKAA frame 41, 10 seed × 5 lỗi/loại = 500 lỗi)

| Loại lỗi | Phát hiện | IoU trước sửa | IoU sau sửa | Đạt IoU ≥ 0,7 |
| --- | --- | --- | --- | --- |
| Lệch (dịch 1–3 m) | 96% | 0,00 | 0,69 (trung vị 0,81) | 76% |
| Rộng hơn vật (0,6–1 m) | 94% | 0,66 | 0,75 | 74% |
| Lơ lửng 0,5–1 m | 100% | 0,39 | 0,90 | 94% |
| Chìm 0,5–1 m | 100% | 0,40 | 0,88 | 100% |
| Sai class | 100% | — | class đúng 49/50 | — |
| Trùng lặp | 100% | — | xoá đúng 50/50 | — |
| **Tổng (10 loại lỗi)** | **98,8% (494/500)** | | | |

Cảnh báo sai mới trên đối tượng không bị chèn lỗi: 0. Lệnh: `.venv-qc3d\Scripts\python utils\qc3d\eval_injected.py --data D:\dataset\cvat\residential`.

Trên nhãn gốc PHENIKAA: R11 chỉ ra 8 "Car" thực chất là Truck/Bus; R09 chỉ ra 8 box lệch mặt đường (7 minor).

### Một bản sửa cho mỗi đối tượng (rules 2026.10.4)

Các rule hình học (R05, R06, R09, R10, R11) của cùng một đối tượng dùng chung **một** đề xuất, theo nguyên tắc sửa tối thiểu: chỉ đổi phần rule chỉ ra là sai (class → kích thước không hợp class → vị trí theo mặt nhìn thấy → đáy lên mặt đường). R11 dùng cả kích thước cụm điểm: cụm điểm cỡ xe máy thì không thể là Truck dù box vẽ to.

Ca thực tế (job 23, frame 1): xe máy gán `Truck`, box 1,87 × 1,92 × 2,05 m, lơ lửng 0,3 m → đề xuất `Rider`, rộng 0,85 m, cao 1,69 m, đáy chạm đường; ô tô gán `Truck` → đề xuất `Car`.

## 4. Ghi vào CVAT

| API | Quyền | Hành vi |
| --- | --- | --- |
| `POST /api/qc3d/issues/{id}/fix` | lead, reviewer | Ghi bản sửa qua `LabeledDataSerializer` + `patch_job_data` (như API annotation của CVAT); lưu shape trước sửa; issue → Lỗi thật |
| `POST /api/qc3d/issues/{id}/revert` | lead, reviewer | Khôi phục đúng shape trước sửa (tạo lại nếu đã xoá) |
| `GET /api/qc3d/taxonomy` | mọi user | Bộ class VN |

Bảo vệ dữ liệu: nếu shape trên CVAT khác ảnh chụp lúc chạy QC → 409, yêu cầu chạy lại QC. Track: sửa đúng keyframe tại frame đó; đổi class áp cho cả track (CVAT lưu class theo track); không tự xoá track.

Đã kiểm tra trên job 23: Car → Truck ghi vào CVAT; áp lại → 409; hoàn tác → shape giống hệt ban đầu; shape bị đổi sau QC → 409; user không có vai trò → 403.

## 5. Giới hạn

- Box rộng hơn vật: chỉ thấy được ở mặt hướng cảm biến; mặt khuất cần nhiều frame (track) hoặc model.
- Ngưỡng trên đo trên 1 frame (114 đối tượng); cần đo lại trên nhiều sequence trước khi tự động áp dụng.
