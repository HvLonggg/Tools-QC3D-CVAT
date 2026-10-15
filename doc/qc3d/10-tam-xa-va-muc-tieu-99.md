# Tầm xa và mục tiêu 99% trong 30 m

## 1. Đo theo mục tiêu

`eval_predictions.py` có mục **"Target within 30 m"**: trong 30 m, khớp tâm trong 2 m, cho mỗi class:
- độ đúng (precision) và độ phủ (recall) ở ngưỡng tin cậy 0,3;
- ngưỡng tin cậy cần để đạt **đúng 99%**, và độ phủ còn lại ở ngưỡng đó.

Điểm xuất phát, model v1 (1 lần quét, 2 scene PHENIKAA giữ riêng, `--groups`):

| Nhóm | Đúng / phủ ở ngưỡng 0,3 | Đạt đúng 99% thì còn phủ |
| --- | --- | --- |
| Car | 92,8% / 72,1% | 66,9% (ngưỡng 0,52) |
| Xe 2 bánh | 85,5% / 76,4% | 37,8% (ngưỡng 0,73) |
| Xe lớn | 23,6% / 98,2% | ~1% |
| Pedestrian | 5,6% / 47,1% | ~1% |

Một phần "đoán sai" là vật có thật nhưng PHENIKAA chưa gán nhãn. Ví dụ: trong 47 người đi bộ model tin ≥ 0,5 mà không có nhãn, 17 có hình dạng giống người. Nên **làm sạch nhãn** cũng nâng số đo lên.

## 2. Cộng dồn nhiều lần quét

`build_train_set.py --sweeps 4 --sweep-min-range 20`:
- ghép 3 lần quét trước (0,3 s) vào frame hiện tại bằng ego pose, mỗi điểm có thêm cột độ trễ thời gian;
- chỉ thêm điểm xa hơn 20 m, vì vật ở gần đã dày điểm;
- dữ liệu chỉ tăng khoảng 2 lần thay vì 4 lần.

PHENIKAA: ego pose là ma trận 3×4 frame → frame đầu scene; công thức ghép `inv(T_hiện tại) @ T_trước` đã kiểm (độ trùng khớp giữa hai lần quét tăng từ 24% lên 55%). nuScenes: dùng các lần quét phụ trong `sweeps/LIDAR_TOP`.

Số điểm trên xe Car (PHENIKAA):

| Khoảng cách | 1 quét | 4 quét | Xe < 10 điểm |
| --- | --- | --- | --- |
| 20–30 m | 334 | 1.074 (×3,2) | 2% → 0% |
| 30–40 m | 118 | 402 (×3,4) | 10% → 1% |
| 40–54 m | 30 | 111 (×3,7) | 27% → 7% |

**Lúc train, notebook bỏ ngẫu nhiên các lần quét cũ** ở một nửa số mẫu, nên model vẫn chạy được với 1 lần quét. Điều này cần cho QC, vì job CVAT thường không có ego pose. Lúc kiểm định luôn dùng đủ 4 lần quét.

## 3. Thêm nhãn cho vật ở xa (người duyệt)

1. `propose_labels.py`:
   - model tìm vật xa hơn 30 m chưa có nhãn, tin ≥ 0,5;
   - phải thấy cùng vật ở ít nhất 2 trong 4 frame liền kề (ghép bằng ego pose, cho phép vật di chuyển tới 15 m/s);
   - không đề xuất Pedestrian, vì model chưa đáng tin với class này.
2. Kết quả ghi vào `label_proposed/` (chỉ đề xuất) và `label_review/` (nhãn gốc + đề xuất).
3. `phenikaa_to_cvat.py --label-dir label_review` → tạo task trên CVAT → người duyệt sửa/xoá đề xuất.
4. Export job dạng "Datumaro 3D 1.0" → `cvat_to_phenikaa.py` ghi `label_verified/`.
5. `build_train_set.py` tự dùng `label_verified/` thay cho nhãn gốc ở các frame đó.

**Đo với model v1** (2 scene giữ riêng, giấu nhãn > 30 m):
- tìm lại được **48%** số nhãn bị giấu (9.850 nhãn);
- **95% đề xuất đúng**; 5% không khớp nhãn nào, một phần có thể là vật thật chưa gán.

Người duyệt chỉ còn xác nhận thay vì vẽ từ đầu khoảng một nửa số vật ở xa.

## 4. Lộ trình tới 99% trong 30 m

| Bước | Việc | Vì sao |
| --- | --- | --- |
| 1 | Train v2 trên dữ liệu 4 lần quét (`train_vn_v4`), 4 nhóm class, 30 epoch | Nhiều điểm hơn; class khớp với dữ liệu thật |
| 2 | Duyệt đề xuất nhãn (mục 3) và sửa nhãn PHENIKAA bằng QC (R09/R10/R12), rồi dựng lại dữ liệu và train lại | Nhãn sạch: model học đúng, số đo phản ánh thật |
| 3 | Chạy lại `propose_labels.py` bằng model mới; lặp bước 2 | Mỗi vòng model tốt hơn thì đề xuất nhiều và đúng hơn |
| 4 | Thêm dữ liệu VN: 3 scene PHENIKAA thiếu LiDAR, dữ liệu công ty | Nhiều cảnh, nhiều kiểu xe hơn |
| 5 | Train lâu hơn, đoán trên cả bản lật của cảnh (test-time augmentation), kết hợp vài model | Mỗi cách thường thêm vài điểm % |
| 6 | Trong QC: chọn ngưỡng tin cậy theo `eval_predictions.py` để độ đúng ≥ 99%; theo dõi cùng một vật qua nhiều frame | QC chỉ cảnh báo khi model thật sự chắc |

**Cần nói rõ:**
- Ở mức **đúng 99%**, model v1 hiện chỉ còn bắt được 67% xe con trong 30 m. Để vừa đúng 99% vừa bắt đủ vật, cần cả dữ liệu nhiều hơn lẫn nhãn sạch hơn: các bước 2–4 là bắt buộc, không chỉ train lại.
- Mỗi vòng nên đo lại bằng `eval_predictions.py --source phx --groups`. Xem mục "Target within 30 m" để biết còn cách 99% bao xa.
