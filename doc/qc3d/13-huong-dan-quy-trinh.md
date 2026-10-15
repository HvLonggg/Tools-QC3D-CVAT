# Hướng dẫn quy trình duyệt và nghiệm thu job 3D

Dành cho người gán nhãn, reviewer (QC), QA và lead. Chi tiết kỹ thuật và API xem `12-nghiem-thu-api.md`.

## 1. Các bước

| Bước | Ai | Làm ở đâu | Kết quả |
| --- | --- | --- | --- |
| Gán nhãn xong → **Nộp để QC** | Người gán nhãn (hoặc lead) | Trang job (QC 3D → Trạng thái job → bấm job) | Chờ QC |
| **Bắt đầu QC** → Chạy QC → duyệt lỗi, sửa / autofix | Reviewer | Hàng đợi review, trang job, panel QC Check 3D | Đang QC |
| **QC đạt** hoặc **Trả lại** (ghi lý do) | Reviewer | Hàng đợi review (ngăn bên phải) hoặc trang job | Chờ nghiệm thu / Bị trả lại |
| **Bắt đầu nghiệm thu** → chấm từng frame mẫu | QA | Nghiệm thu QA | Đang nghiệm thu |
| **Đạt nghiệm thu** hoặc **Không đạt** (ghi lý do) | QA (lead được duyệt vượt) | Nghiệm thu QA | Đạt / Bị trả lại |
| **Chốt job** | Lead | Trang job | Đã chốt |
| **Mở lại** (ghi lý do) | Lead | Trang job | Đang gán nhãn |

Hệ thống chặn bước sai, nút bị khóa có ghi lý do khi rê chuột:

**QC đạt** cần đủ ba điều kiện:
- đã chạy QC cho **toàn bộ job** (không phải 1 frame);
- kết quả QC chưa cũ: nhãn, guideline và bộ rule chưa đổi sau lần chạy;
- không còn lỗi **nghiêm trọng** đang mở.

**Đạt nghiệm thu** cần:
- đã chấm đủ mọi frame mẫu;
- tỷ lệ box lỗi ≤ ngưỡng;
- số lỗi nghiêm trọng còn mở ≤ ngưỡng.

Nếu chưa đạt, chỉ lead được duyệt vượt: phải tick xác nhận và ghi lý do, lịch sử đánh dấu "duyệt vượt".

Mọi bước được ghi vào **Lịch sử quy trình** của job: ai làm, lúc nào, ghi chú, số lỗi QC lúc đó, tỷ lệ lỗi mẫu.

## 2. Các trang

- **Trạng thái job**
  - Dải 8 trạng thái: bấm một trạng thái để lọc.
  - Hai chế độ:
    - Kanban: kéo ngang để xem đủ 8 cột;
    - Bảng: sắp xếp theo trạng thái, lỗi, thời gian.
  - Thẻ job hiện:
    - lỗi QC còn mở (đỏ: nghiêm trọng, cam: cần xử lý, xanh: lưu ý);
    - tỷ lệ lỗi mẫu nghiệm thu;
    - người phụ trách;
    - thời gian từ bước cuối.
- **Hàng đợi review**
  - Hai tab: "Cần QC" (đang QC xếp trước, rồi job chờ lâu nhất) và "Đã trả lại".
  - Bấm một job để mở ngăn bên phải:
    - các bước quy trình;
    - tóm tắt QC;
    - nút Bắt đầu QC / QC đạt / Trả lại;
    - lối tắt mở báo cáo QC và trình gán nhãn.
- **Nghiệm thu QA**
  - Bên trái: job chờ hoặc đang nghiệm thu.
  - Bên phải:
    - tiêu chí của guideline;
    - tiến độ chấm;
    - tỷ lệ box lỗi so với ngưỡng (vạch giữa thanh là ngưỡng);
    - bảng frame mẫu: bấm "Frame N" để mở đúng frame trên CVAT (tab mới), nhập số box đã kiểm, số box lỗi, ghi chú, bấm **Lưu** (hoặc Enter);
    - kết luận Đạt / Không đạt hiện khi chấm xong.
- **Trang job** (báo cáo QC)
  - Khối quy trình: trạng thái, nút hành động, 4 bước.
  - **Bản đồ tổng quan job**: cả job trên một bản đồ nhìn từ trên xuống.
    - Đường xanh: quỹ đạo xe, có nhãn frame `fN`. Chấm nhạt: vật thể theo class. Chấm đậm: lỗi QC theo mức độ (rỗng = đã xử lý).
    - Cuộn để phóng, kéo để di chuyển. Lọc theo mức độ, "Chỉ lỗi chưa xử lý".
    - Bấm một lỗi để mở đúng frame và chọn đúng box trên CVAT.
    - Vị trí được **ước lượng** từ chính các box: so với ego pose thật, lệch 1–9% quãng đường.
  - Bên dưới: danh sách lỗi, thông tin lần QC, guideline, lịch sử quy trình.
- **Tổng quan**: biểu đồ phễu (số job mỗi bước) và chỉ số chờ / đạt / bị trả lại / tỷ lệ đạt.
- **Guideline → tab Nghiệm thu**: chỉnh các tiêu chí sau (để trống thì dùng mặc định):
  - tỷ lệ frame mẫu: mặc định 10%;
  - số frame tối thiểu: mặc định 5;
  - tỷ lệ box lỗi tối đa: mặc định 2%;
  - lỗi nghiêm trọng còn mở tối đa: mặc định 0.
- **Cảnh 3D (panel QC Check 3D → Hiển thị)**: bật "Vòng khoảng cách 10/20/30 m và lưới mặt đường" để ước lượng khoảng cách khi duyệt. Vòng và lưới đặt ở độ cao mặt đường quanh xe.

## 3. Quyền

| Vai trò | Được làm |
| --- | --- |
| Người gán nhãn của job | Nộp để QC |
| reviewer | Bắt đầu QC, QC đạt, Trả lại |
| qa | Bắt đầu nghiệm thu, chấm mẫu, Đạt / Không đạt (khi đạt tiêu chí) |
| lead | Mọi bước, duyệt vượt, Chốt, Mở lại |

Vai trò gán ở **QC 3D → Quản trị & quyền**. Admin CVAT có mọi quyền.
