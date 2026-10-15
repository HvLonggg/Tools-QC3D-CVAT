# Guideline: viết hoặc import guide, QC của job chạy theo guide đó

Guideline quyết định QC kiểm những gì:

- class và khoảng kích thước (dùng cho R06 kích thước sai và R11 nghi sai class)
- ngưỡng của từng rule
- rule nào bật, rule nào tắt

Mỗi lần chạy QC đều lưu lại bản chụp guideline đã dùng, nên kết quả cũ vẫn tái lập được khi guideline thay đổi.

## 1. Job dùng guideline nào

Lấy nguồn cụ thể nhất có sẵn, theo thứ tự:

1. Guideline QC gán cho **task**
2. **Guide CVAT của task**: nút *Edit guide* trên trang task, nếu có bảng class
3. Guideline QC gán cho **project**
4. Guide CVAT của project
5. Guideline QC đặt **mặc định**
6. Bộ class VN dựng sẵn (`qc/taxonomy.py`)

Trang QC của job hiển thị:

- guideline đang áp dụng và nguồn của nó
- label của task chưa có trong guideline (QC không kiểm kích thước của label đó)
- cảnh báo **guideline đã đổi sau lần QC trước**: khi đó bấm *Chạy QC* để kiểm lại

## 2. Cách tạo guideline (menu QC 3D → Guideline)

| Cách | Làm gì |
| --- | --- |
| Viết mới | Bắt đầu từ bộ class VN; sửa bảng class, ngưỡng, bật/tắt rule, nội dung guide |
| Import file | `.json`, `.yaml`, `.csv`, `.md`, `.txt`, `.docx` (đọc bảng trong Word) |
| Lấy từ guide CVAT | Đọc guide đã viết trên task / project CVAT |
| Viết thẳng trong guide CVAT | Không cần làm gì thêm: QC tự đọc (mục 1, bước 2 và 4) |

Sau đó chọn project / task để gán và bấm **Lưu và áp dụng**. Mỗi lần nội dung thay đổi, số bản tăng thêm 1.
Quyền sửa: vai trò `lead` hoặc `qa` (và admin). Mọi user đều xem được.

## 3. Mẫu guide (Markdown, dán vào Edit guide của CVAT hoặc lưu thành `.md`)

```markdown
| Class | Tên | Bao gồm | Dài (m) | Rộng (m) | Cao (m) | Điểm ở 10 m |
| --- | --- | --- | --- | --- | --- | --- |
| Car | Ô tô con | Sedan, SUV, bán tải | 2,5–6,2 | 1,4–2,3 | 1,2–2,5 | 300 |
| Truck | Xe tải | Tải, ben, đầu kéo | 4,5–13 | 1,7–2,8 | 2–4,3 | 450 |
| Rider | Xe máy kèm người | Xe máy, xe điện, xe đạp | 1,1–2,6 | 0,35–1,3 | 1–2,2 | 100 |

overlap_iou: 0,25
ground_tolerance: 0,3
R08: tắt
```

Quy ước khi viết:

- Cột **Class** phải trùng tên label trên CVAT.
- Khoảng kích thước viết `2,5–6,2` hoặc `2.5-6.2`. Cũng có thể tách thành hai cột `Dài min` / `Dài max`.
- Các cột không bắt buộc: Tên, Bao gồm, Điểm ở 10 m, Tốc độ tối đa (m/s). Class đã có trong bộ VN mà để trống các cột này thì lấy giá trị của bộ VN.
- Ngưỡng viết dạng `tên_ngưỡng: giá_trị`. Danh sách ngưỡng và ý nghĩa có ở tab *Ngưỡng* trong trình soạn.
- Tắt một rule bằng `R08: tắt` (hoặc `off`). Không được tắt R01.

## 4. Viết guide bằng câu văn (biên dịch ngôn ngữ tự nhiên)

Trong trình soạn, tab *Hướng dẫn (ngôn ngữ tự nhiên)* → **Biên dịch thành quy tắc**. File `.md`, `.txt`, `.docx` và guide CVAT cũng được biên dịch khi import. Không có bảng class thì dùng bộ class VN mặc định và chỉnh theo các câu.

| Câu viết | Thành quy tắc |
| --- | --- |
| Ô tô con dài từ 3 đến 5,5 m, rộng 1,5-2,1 m. | Car: dài 3–5,5, rộng 1,5–2,1 (R06, R11, Autofix) |
| Xe tải cao tối đa 4 m. | Truck: cao ≤ 4 |
| Đáy box lệch mặt đường không quá 10 cm. | `ground_tolerance` = 0,1 (R09) |
| Khe hở giữa box và vật không quá 20 cm. | `face_gap` = 0,2 (R10) |
| Đỉnh box cao hơn vật không quá 30 cm. | `height_gap` = 0,3 (R10) |
| Hai box chồng lấn tối đa 20%. | `overlap_iou` = 0,2 (R02) |
| Chỉ kiểm tra trong bán kính 50 m. | `fit_max_distance` = 50 |
| Không kiểm tra vận tốc. | tắt R08 |
| Không tự sửa người đi bộ. | Autofix không sửa Pedestrian |
| Autofix không đổi class. | Autofix không sửa class (để QC duyệt) |

Bộ biên dịch theo luật cố định, không đoán:
- Mỗi câu được hiểu sẽ hiện lại kèm giá trị đã đặt, để QA kiểm tra trước khi lưu.
- Câu có số mà không hiểu thì báo "Chưa hiểu câu", không áp dụng.
- Tên class nhận cả tên CVAT, tên tiếng Việt và cách gọi thường gặp ("xe con", "xe khách", "người đi xe máy"…).

## 5. Autofix theo guideline

Autofix (tab QC Check → Autofix) **sửa trực tiếp box đang có**: vị trí, kích thước, class, góc. Nó không tạo box mới và không tự xoá box.

Mỗi bản sửa phải tự qua lại QC theo guideline của job (`qc/verify.py`) thì mới được ghi:
- Kích thước nằm trong khoảng của class (mới).
- Đáy chạm mặt đường đo tại chỗ.
- Ôm sát các mặt LiDAR nhìn thấy, chứa ≥ 95% điểm của vật.
- Class khớp cụm điểm LiDAR, và rõ ràng hơn mọi class khác.
- Không đè lên box khác.
- Dời ≤ 1,5 m. Đổi kích thước > 30% chỉ khi đưa box về gần cỡ điển hình của class.

Bản sửa không qua kiểm chứng thì để QC duyệt tay và hiện lý do. Đổi class của cả track hay xoá box cũng luôn để QC duyệt.

Cách sửa box rộng quá: chỉ kéo mặt LiDAR nhìn thấy về sát điểm, giữ mặt phía xa (LiDAR không thấy nên không có bằng chứng để dời).

Guideline quyết định Autofix được làm gì:
- `autofix.fix`: bật/tắt từng loại sửa (chiều đứng, chiều ngang, class, góc).
- `autofix.exclude_classes`: các class Autofix không được sửa.
- Các ngưỡng `autofix_*` ở tab *Ngưỡng*.
- Mỗi job còn có thể loại trừ thêm class.

Nếu nhãn, guideline hoặc bộ rule đã đổi sau lần QC trước, Autofix chạy lại QC trước khi sửa.

Đo trên lỗi chèn vào nhãn PHENIKAA (10 lần × 5 lỗi/loại, `utils/qc3d/eval_injected.py`), chỉ tính bản sửa Autofix tự ghi:

| Lỗi | Tự sửa | IoU với box gốc ≥ 0,7 | Làm tệ hơn |
| --- | --- | --- | --- |
| Dời box 1–3 m | 10/38 (còn lại để duyệt) | 100% | 0 |
| Box nới rộng về phía cảm biến | 46/47 | 96% | 2 (lệch ~0,03) |
| Lơ lửng | 50/50 | 100% | 0 |
| Chìm | 46/50 | 100% | 0 |
| Sai class | 50/50 | 49/50 đúng class | 0 |

Ca sai class còn lại là xe cao 2,55 m, vượt giới hạn chiều cao Car trong guideline nên bị chuyển thành Truck: sửa đúng theo guideline, khác nhãn gốc.

## 6. API

| API | Quyền | Việc |
| --- | --- | --- |
| `GET /api/qc3d/guidelines` | mọi user | Danh sách, mẫu, ngưỡng, rule, project/task gán được |
| `POST /api/qc3d/guidelines` | lead, qa | Tạo và gán (`projects`, `tasks`) |
| `GET/PUT/DELETE /api/qc3d/guidelines/{id}` | xem: mọi user; sửa/xoá: lead, qa | |
| `POST /api/qc3d/guidelines/parse` | lead, qa | Đọc file (`filename`, `content_base64`) hoặc guide CVAT (`task_id` / `project_id`); chưa lưu; `compiled` = câu đã hiểu |
| `POST /api/qc3d/guidelines/compile` | lead, qa | Áp câu văn (`text`) vào guideline (`content`); chưa lưu |
| `POST /api/qc3d/jobs/{id}/autofix` | lead, reviewer | `dry_run: true` xem trước; ghi bản sửa đã kiểm chứng |
| `GET /api/qc3d/jobs/{id}/guideline` | xem được job | Guideline áp dụng, nguồn, label lệch, đã đổi sau lần QC chưa |

## 7. Đã kiểm tra

- 7 unit test (`tests/test_guideline.py`):
  - import Markdown, CSV, DOCX
  - chặn guideline sai (min > max, tắt R01, ngưỡng ngoài khoảng)
  - guideline đổi kết quả R06
  - R11 gợi ý đúng class mới của guideline
- Kiểm tra đầu-cuối trên job 23 qua API thật, 18/18 đạt:
  - Gán guideline cho task thì R08 tắt và R06 tăng từ 11 lên 14 lỗi, do guide giới hạn Car ≤ 5,5 m.
  - Guide CVAT của task được tự áp dụng.
  - Phân quyền: user không có vai trò bị 403, chưa đăng nhập bị 401.
  - Xoá guideline thì job quay về bộ VN (86 vấn đề, như trước).
- `tests/test_autofix.py` (9 test):
  - bản sửa lơ lửng được kiểm chứng
  - box rộng giữ mặt xa
  - chặn bản sửa đè box khác, dời/co giãn quá giới hạn, class mơ hồ
  - kế hoạch Autofix chỉ ghi bản sửa đã kiểm chứng, đúng loại guideline cho phép
  - guide viết bằng câu văn điều khiển QC và Autofix
  - hồi quy IoU > 1 khi hai box cùng hướng
- Endpoint `guidelines/compile` và `jobs/23/autofix` (dry run) chạy qua API thật trên server CVAT local. QC tự chạy lại khi bộ rule đổi, lần gọi sau không chạy lại.
