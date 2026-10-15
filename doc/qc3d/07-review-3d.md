# Kiểm chứng lỗi trên cảnh 3D

- Mở báo cáo job từ QC 3D. Bấm **Frame**, **Nhãn** hoặc **Mở trong CVAT** để
  tới đúng frame, mở tab **Kiểm tra 3D**, chọn và phóng tới cuboid của lỗi.
- Trong tab **Duyệt**, bấm thẻ đối tượng hoặc nút trước/sau để kiểm tra liên tiếp.
  Camera chờ cloud tải xong; các góc Top / Side / Front cũng theo đối tượng đang chọn.
- Kết hợp bộ lọc **mức độ**, **class**, **trạng thái**, **frame hiện tại** và tìm kiếm.
- Bật **Cảnh báo & thông số trên cảnh 3D** để thấy box và nhãn cảnh báo:
  đỏ = nghiêm trọng; cam = cần xử lý; xanh = lưu ý. Đối tượng đã sửa hoặc đã chọn
  giữ nguyên không còn cảnh báo trên bản đồ.
- Nhãn của đối tượng đang chọn có kích thước hiện tại, thông số từ báo cáo QC
  (số điểm / ngưỡng, IoU, độ hở, vận tốc hoặc độ lệch mặt đường khi có dữ liệu).
  Thông số QC là số đo của lần chạy gần nhất; chạy lại QC sau khi sửa nhãn thủ công.
- Trong tab **Hiển thị**, chọn **Kiểm chứng vật thể** để tô màu theo độ cao và
  làm mờ điểm nền quanh vật. **Điểm rõ hơn** tăng cỡ điểm và độ sáng. Có thể chỉnh
  cỡ điểm, tương phản, màu gốc / độ cao / cường độ riêng và trở về mặc định.
- Chế độ tập trung dùng cuboid có xoay và một vùng đệm 0,35 m quanh nó. Số **điểm gốc
  trong box** là số điểm hiện tại ở bên trong cuboid, gồm cả điểm mặt đường; số điểm
  trong QC có thể khác vì rule loại điểm sát mặt đường. Không tạo thêm điểm LiDAR.
- Các thay đổi về camera, màu và hiển thị không sửa tọa độ annotation. **Áp dụng sửa**
  ghi bản sửa vào CVAT; **Hoàn tác** khôi phục đối tượng. Xem trước AutoFix trước khi áp dụng.

Khi nhiều rule cùng báo lỗi một đối tượng, màu thể hiện mức độ cao nhất còn cần xử lý.
Chỉ nhãn của đối tượng đang chọn mở rộng thông số để giữ cảnh dễ đọc; tối đa 100 nhãn
hiển thị cùng lúc. Bộ đệm màu được cấp phát khi cần; tọa độ point cloud vẫn được chia sẻ.
