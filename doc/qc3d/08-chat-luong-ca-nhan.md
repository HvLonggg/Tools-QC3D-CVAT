# Chất lượng làm việc cá nhân và phân quyền xem

Menu **QC 3D → Chất lượng của tôi**: mỗi người xem các job 3D mình đã gán nhãn, trạng thái review, lỗi QC trả về, ai đã QC mình và điểm chất lượng.

## 1. Ai xem được gì

| Người dùng | Trang "Chất lượng của tôi" | Xem của người khác | Menu đội QC (Hàng đợi review, Nghiệm thu QA, Quản trị) |
| --- | --- | --- | --- |
| Annotator (worker, không có vai trò QC) | Của mình | Không (API trả 403) | Ẩn |
| Reviewer | Của mình | Không | Có |
| Lead, QA | Của mình | Chọn từng người | Có |
| Admin CVAT | Của mình | Chọn từng người | Có |
| Chưa đăng nhập | Không (401) | | |

- Chi tiết lỗi của một job chỉ mở được nếu người đang xem là annotator của job đó, hoặc là lead / QA / admin. Người khác nhận 404, cùng câu trả lời dù job có tồn tại hay không, để không lộ việc của người khác.
- Annotator không thấy dữ liệu nội bộ của Autofix (đề xuất, bằng chứng điểm), chỉ thấy lỗi, kết luận và người kết luận.
- Ẩn menu chỉ để gọn giao diện. Quyền thật được kiểm tra ở API: scope `view:quality` trong `qc3d.rego` và kiểm tra vai trò trong `views.py`.

## 2. Annotator của một job là ai

CVAT chỉ lưu người được giao hiện tại; khi job chuyển sang review, người đó thành reviewer. QC 3D ghi thêm lịch sử giao việc (`QcJobAssignment`):
- Mỗi lần job đổi người hoặc đổi giai đoạn, lịch sử được ghi lại tự động.
- Annotator = người được giao cuối cùng ở giai đoạn gán nhãn.
- Migration `0006` lấy trạng thái hiện tại làm điểm bắt đầu. Job đã sang review trước khi cài bản này thì chưa biết annotator.
- Chỉ tính lỗi tìm thấy từ lúc annotator hiện tại nhận job. Lỗi và bản sửa của người làm trước không tính cho người sau.

## 3. Trạng thái review (nhìn từ phía annotator)

| Giai đoạn CVAT | Trạng thái CVAT | Hiển thị |
| --- | --- | --- |
| bất kỳ | rejected | Bị trả lại, cần sửa |
| annotation | new, in progress | Đang làm |
| annotation | completed | Chờ review |
| validation | new, chưa chạy QC | Chờ review |
| validation | new đã chạy QC, in progress | Đang review |
| validation | completed | Đã review |
| acceptance | new, in progress | Chờ nghiệm thu |
| acceptance | completed | Đã nghiệm thu |

## 4. Cách tính điểm

- **Lỗi** = lỗi QC được reviewer **xác nhận**, hoặc đã được sửa.
- Lỗi QC **giữ nguyên nhãn** (waived) là cảnh báo sai của máy, không trừ điểm.
- Lỗi **chờ duyệt** chưa tính; khi còn lỗi chờ duyệt, điểm hiện "tạm tính".
- **Điểm chất lượng** = 1 − (số đối tượng có lỗi xác nhận / số đối tượng được QC), tính trên lần QC mới nhất của mỗi job, cộng dồn các job.
- Xếp loại:

| Điểm | Xếp loại |
| --- | --- |
| ≥ 98% | Xuất sắc |
| ≥ 95% | Tốt |
| ≥ 90% | Đạt |
| < 90% | Cần cải thiện |

- **Tỉ lệ cảnh báo đúng** = lỗi xác nhận / (xác nhận + giữ nguyên). Tỉ lệ này cho thấy QC tự động có công bằng với người làm hay không.

## 5. Trang hiển thị gì

- Bốn ô số liệu: điểm chất lượng, job đã làm, lỗi xác nhận, chờ duyệt.
- Dải trạng thái review, bấm vào để lọc bảng job.
- Lỗi thường gặp theo rule, lỗi theo class, điểm theo tuần (12 tuần gần nhất).
- Bảng job: trạng thái, điểm, số lỗi (xác nhận / chờ / giữ nguyên), **người QC** kèm vai trò của từng người, QC gần nhất. Vai trò gồm: chạy QC, duyệt lỗi, sửa nhãn, review trên CVAT, nghiệm thu.
- **Xem lỗi** mở chi tiết job:
  - từng lỗi: rule, frame, class, mô tả;
  - kết luận, ai kết luận, lúc nào, lý do;
  - bản sửa và ai sửa;
  - lịch sử giao việc;
  - nút mở job trên CVAT.

## 6. API

| API | Ai gọi được | Trả về |
| --- | --- | --- |
| `GET /api/qc3d/quality` | mọi user đăng nhập | Của chính mình |
| `GET /api/qc3d/quality?user=<id>` | lead, QA, admin | Của người đó, kèm danh sách annotator |
| `GET /api/qc3d/quality/jobs/<job_id>[?user=<id>]` | annotator của job; lead, QA, admin | Chi tiết lỗi của job |

## 7. Đã kiểm tra

Kịch bản đầu-cuối trên server CVAT local, dữ liệu thử dựng trong transaction rồi rollback: 17/17 đạt.
- Job chuyển sang reviewer: annotator vẫn thấy job, trạng thái "Đang review".
- Thấy được ai duyệt và lý do.
- Người khác không thấy job (404), không xem được annotator (403).
- Reviewer không xem được người khác (403); lead xem được.
- Chưa đăng nhập bị 401.
- Lỗi và bản sửa của người làm trước không bị tính cho người sau.

Bảng trạng thái và xếp loại cũng đã được kiểm (8/8 trường hợp).

## 8. Phân tích chất lượng cho đội QC (trang Tổng quan)

`GET /api/qc3d/analytics?days=7|30|90|365[&project_id=][&task_id=]` chỉ tính trên các job 3D mà người xem có quyền thấy trong CVAT.

- **Chỉ số:**
  - điểm chất lượng;
  - lỗi phát hiện;
  - độ chính xác của QC: xác nhận / (xác nhận + giữ nguyên);
  - số lỗi chờ duyệt, kèm thời gian duyệt trung vị;
  - số bản đã sửa và số bản bị hoàn tác.
- **Biểu đồ** (chart.js, có tooltip và hiệu ứng):
  - hoạt động theo ngày: phát hiện, xác nhận, đã sửa, giữ nguyên;
  - lỗi chờ duyệt theo mức độ;
  - lỗi theo rule (xác nhận / giữ nguyên / chờ duyệt), để thấy rule nào cần chỉnh ngưỡng;
  - lỗi theo class;
  - trạng thái job;
  - các job cần chú ý.
- **Bảng theo người:**
  - chất lượng theo người gán nhãn: chỉ lead, QA, admin;
  - khối lượng review (chạy QC, quyết định, sửa nhãn): người có vai trò QC.
  - Annotator không có vai trò QC chỉ thấy số liệu tổng của job mình xem được, không thấy bảng theo người.
- **Màu cố định theo ý nghĩa** trên mọi biểu đồ:

| Ý nghĩa | Màu |
| --- | --- |
| Phát hiện | xanh dương |
| Xác nhận lỗi | cam |
| Đã sửa | xanh ngọc |
| Giữ nguyên | vàng |
| Chờ duyệt | xám |
