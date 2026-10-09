# Sprint 2: QC engine (R01–R08)

Mục tiêu: bấm **Chạy QC** trên một job 3D, hệ thống liệt kê vấn đề kèm bằng chứng. Reviewer mở đúng frame/object trong CVAT, đánh dấu **Lỗi thật** hoặc **Giữ nguyên** (bắt buộc ghi lý do). Quyết định được giữ khi chạy lại QC.

## 1. Thành phần

| Thành phần | File | Ghi chú |
| --- | --- | --- |
| Hình học | `cvat/apps/qc3d/qc/geometry.py` | Cuboid, đếm điểm trong box, IoU 3D, đọc PCD; chỉ dùng numpy |
| Rule | `cvat/apps/qc3d/qc/rules.py` | R01–R08, cấu hình `DEFAULT_CONFIG`, version `RULES_VERSION` |
| Chèn lỗi (tập A) | `cvat/apps/qc3d/qc/inject.py` | 6 loại lỗi, có seed để tái lập |
| Dịch vụ | `cvat/apps/qc3d/services.py` | Job CVAT → cuboid → chạy rule → lưu `QcRun`, `QcIssue` |
| API | `cvat/apps/qc3d/views.py` | Xem bảng dưới |
| Giao diện | `cvat-ui/plugins/qc3d/src/ts/pages/` | Tổng quan, Review (hàng đợi), trang job |
| Đánh giá | `utils/qc3d/eval_injected.py` | Đo recall trên tập A |
| Test | `cvat/apps/qc3d/tests/test_rules.py` | 10 test, không cần Django |

| API | Quyền |
| --- | --- |
| `POST /api/qc3d/jobs/{id}/run` | Xem được job + vai trò lead / reviewer / qa |
| `GET /api/qc3d/jobs/{id}/report` | Xem được job |
| `POST /api/qc3d/issues/{id}/decision` `{status, reason}` | Xem được job + vai trò lead / reviewer / qa; `waived` bắt buộc `reason` |

## 2. Rule

| Mã | Kiểm tra | Mức | Cần |
| --- | --- | --- | --- |
| R01 | Thiếu class, toạ độ không hữu hạn, kích thước ≤ 0 | blocker | — |
| R02 | Trùng lặp (tâm ≤ 5 cm, cùng class, cùng size); chồng lấn IoU 3D > 0,3 | major | — |
| R03 | Class ngoài schema của task | major | — |
| R04 | Roll/pitch > 15°; yaw ngoài (−π, π] (tự sửa được) | minor | — |
| R05 | Ít điểm theo khoảng cách: `max(3, base × min(4, (10/d)²))`; box lệch (điểm sát ngoài > 50% điểm trong) | major (≤ 60 m) / minor | Point cloud |
| R06 | Kích thước ngoài guideline class; lệch > 15% trung vị track | major | — |
| R07 | Keyframe nhảy so với nội suy 2 keyframe lân cận, toạ độ thế giới | major | Track + ego-pose |
| R08 | Vận tốc giữa 2 keyframe vượt ngưỡng class (nghi đổi ID) | major | Track + ego-pose + timestamp |

Thiếu đầu vào thì rule báo **chưa kiểm tra** kèm lý do, không bao giờ báo "đạt".

## 3. Kết quả đo (09/10/2026, PHENIKAA residential frame 41, 114 đối tượng)

Lệnh: `.venv-qc3d\Scripts\python utils\qc3d\eval_injected.py --data D:\dataset\cvat\residential --seeds 10 --per-kind 5`

| Loại lỗi chèn | Rule | Recall |
| --- | --- | --- |
| Dịch box ra khỏi cụm điểm | R05 | 92,0% (46/50) |
| Phóng to ×1,8–2,5 | R06 | 98,0% (49/50) |
| Nghiêng 20–40° | R04 | 100% (50/50) |
| Bản sao trùng | R02 | 100% (50/50) |
| Kích thước = 0 | R01 | 100% (50/50) |
| Class lạ | R03 | 100% (50/50) |
| **Tổng** | | **98,3% (295/300)** — đạt cổng ≥ 95% |

Cảnh báo sai mới trên đối tượng không bị chèn lỗi: **0**.

Lỗi sót: box bị dịch 2,4–2,7 m rơi đúng vào cụm điểm của vật khác (xe bên cạnh, tường). Cần đối chiếu theo thời gian (R07) hoặc model để bắt.

Trên nhãn gốc (chưa chèn lỗi) QC đưa 59 vấn đề cho người xem:

- R06 × 7: "Car" dài tới 9,99 m, cao 4,19 m (xe buýt / xe tải). **Cần QA quyết**: thêm class Bus/Truck hay nới ngưỡng Car.
- R05 × 52: 41 minor (> 60 m, ít điểm là bình thường), 11 major (nhóm người đi bộ ở khoảng 48 m có 0 điểm LiDAR, khả năng nhãn vẽ theo camera).

Trên CVAT: chạy QC job 23 mất 1,7 giây, kết quả khớp với lần đo local.

## 4. Giới hạn

- R07/R08 đã có code và test (dữ liệu tổng hợp) nhưng chưa chạy trên dữ liệu thật: bản Mini chỉ có 1 frame có nhãn, chưa có track.
- Chạy QC đồng bộ trong request; job lớn (hàng trăm frame) cần chuyển sang hàng đợi RQ.
- Ngưỡng kích thước là đề xuất, cần QA xác nhận theo guideline.
