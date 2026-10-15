# QC nhanh hơn, kích thước theo vật, mặt đường, chọn nhãn lỗi bằng tay

## 1. Tốc độ "Chạy QC"

Kết quả đầu ra giữ nguyên. Đã kiểm trên frame thật: cùng box, cùng thứ tự, cùng class, lệch dưới 0,00001 m.

| Phần | Trước | Sau | Thay đổi |
| --- | --- | --- | --- |
| Model, 1 frame (container, máy rảnh) | 20–35 s | 2,4–3,4 s | Conv bằng một phép nhân ma trận lớn cho mỗi dải hàng (im2col). Pillar dùng phép nhân 2D. Head chỉ tính tâm/kích thước/góc ở các điểm ứng viên. |
| Luật QC, job 23 (84 frame, ~2.000 box) | 35 s (228 s khi thêm mặt phẳng đường) | ~11 s | Index lưới 4 m để cắt điểm quanh box. Phân tích điểm của mỗi box một lần mỗi lượt QC. RANSAC vector hóa. |

Kết quả model của mỗi frame được lưu lại. Lần chạy QC sau chỉ chạy model cho frame chưa có kết quả.

## 2. Kích thước theo vật qua các frame (R13)

LiDAR thường chỉ thấy mặt trước của xe, nên box hay bị vẽ ngắn hơn vật thật.

- Các box của cùng một vật được nối qua các frame:
  - Nếu job có track thì dùng track.
  - Nếu không, chuyển động của xe mình giữa hai frame được ước lượng từ chính các box: phép quay + tịnh tiến 2D, bỏ qua vật đang chạy. Sau đó ghép mỗi box với box gần nhất cùng nhóm class.
  - Kiểm trên 84 frame PHENIKAA: 13.373 box gom thành 551 vật, 0 cặp nối sai khi đối chiếu bằng ego pose thật.
- Kích thước thật của vật:
  - Dài và rộng lấy phân vị 80% qua các frame; cao lấy trung vị; tất cả giới hạn trong khoảng guideline của class.
  - Cần ít nhất 3 frame. Không tính cho Pedestrian, vì dáng người thay đổi.
- R13 báo box ngắn hơn vật quá max(0,3 m; 8%) về chiều dài, hoặc quá max(0,15 m; 8%) về chiều rộng.
- Cách sửa: giữ mặt LiDAR nhìn thấy, kéo dài phía khuất.
- Mọi bản sửa khác (theo điểm, theo model) cũng không được làm box ngắn hơn kích thước của vật.

## 3. Mặt đường (R09 và các bản sửa đặt đáy box)

- Lấy điểm thấp nhất của mỗi ô 0,5 m trong vành 1,5 m quanh box.
- Tìm mặt phẳng qua 3 điểm (RANSAC, 80 lần thử), tinh chỉnh trên các điểm khớp, rồi đọc độ cao tại tâm box.
- Mặt đường được coi là phẳng khi đồng thời:
  - độ dốc ≤ 0,08 (~4,6°);
  - ≥ 60% điểm khớp mặt phẳng;
  - độ lệch ≤ 4 cm.
- Chỗ dốc, gờ, gồ ghề: R09 vẫn báo "(mặt đường dốc / gồ ghề: cần xem tay)" nhưng **không tự kéo box**.
- Trên job 23, 94% box nằm trên mặt đường phẳng.

## 4. Model phân vân giữa 2 class

- Model có thể đặt 2 box lên một vật, ví dụ người lái xe vừa là Pedestrian vừa là xe 2 bánh.
- Khi tâm box nhỏ nằm trong lõi box lớn, chỉ giữ dự đoán chắc hơn.
- Áp dụng khi:
  - thêm box mới;
  - ghép model với nhãn.
- Khi xét sai class, QC vẫn thấy cả hai dự đoán. Vì vậy nhãn đúng với một trong hai class thì không bị báo sai.
- Box mới bị chặn nếu đè lên box có sẵn (IoU > 0,05).

## 5. Chọn nhãn lỗi bằng mắt rồi sửa bằng model (R14)

1. Tab **Duyệt** → **Chọn nhãn lỗi**:
   - bấm box lỗi trên cảnh 3D để chọn, bấm lại để bỏ;
   - box đã chọn có viền tím;
   - danh sách lưu theo job trên trình duyệt.
2. Tab **Autofix** → **Sửa N nhãn đã chọn**. Hệ thống chạy model và QC chỉ trên các frame có nhãn đã chọn. Với mỗi nhãn:
   - ưu tiên box của model (mọi class, trong 30 m);
   - sau đó đến điểm LiDAR;
   - giữ kích thước thật của vật;
   - kiểm chứng theo guideline (dời ≤ 1,5 m, đổi kích thước ≤ 30%, ≥ 95% điểm của vật nằm trong box, không đè box khác).
3. Chỉ các nhãn đã chọn được ghi. Mỗi bản sửa hoàn tác được ở tab Duyệt. Nhãn chưa sửa được có kèm lý do.

Quyền: chọn nhãn thì ai cũng dùng được; sửa cần vai trò lead hoặc reviewer.

## 6. Ảnh camera

- PHENIKAA bản mini (`D:/dataset/phenikaa_mini`) chỉ có LiDAR, nhãn và ego pose. Có file hiệu chuẩn camera (`Calib Information`) nhưng chưa có ảnh.
- Task 25 (job 23) vì vậy không có ảnh đi kèm.
- Task 26 (nuScenes) có 60 ảnh camera, xem được cạnh cảnh 3D.
- Muốn xem ảnh cùng LiDAR cho PHENIKAA: cần tải thêm thư mục ảnh camera của bộ dữ liệu, rồi tạo lại task kèm ảnh liên quan (related images).
