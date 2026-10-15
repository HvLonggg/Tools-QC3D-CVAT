# Luồng duyệt và nghiệm thu job 3D — hợp đồng API (Sprint 4)

Backend làm đúng hợp đồng này, frontend dựa vào nó. Đổi API thì cập nhật file này trong cùng PR.

Tất cả endpoint nằm dưới `/api/qc3d/`, xác thực bằng phiên CVAT như các API QC3D hiện có. Trong ví dụ, giá trị chỉ minh họa.

## 1. Quy trình 3 cấp

```
Gán nhãn ──nộp──▶ Chờ QC ──bắt đầu──▶ Đang QC ──đạt──▶ Chờ nghiệm thu ──lấy mẫu──▶ Đang nghiệm thu ──đạt──▶ Đạt nghiệm thu ──chốt──▶ Đã chốt
   ▲                 │                     │                                                │
   └──── trả lại ────┴─────────────────────┘◀──────────────── không đạt ────────────────────┘
```

| status | Hiển thị | CVAT stage / state |
| --- | --- | --- |
| `in_progress` | Đang gán nhãn | annotation / new, in progress |
| `returned` | Bị trả lại | (mọi stage) / rejected |
| `waiting_review` | Chờ QC | annotation / completed |
| `in_review` | Đang QC | validation / new, in progress |
| `waiting_acceptance` | Chờ nghiệm thu | acceptance / new |
| `in_acceptance` | Đang nghiệm thu | acceptance / in progress |
| `accepted` | Đạt nghiệm thu | acceptance / completed |
| `signed_off` | Đã chốt | acceptance / completed, và có sự kiện `sign_off` sau lần `accept` cuối |

Trạng thái lưu trên **stage/state gốc của CVAT**: màn hình job của CVAT vẫn đúng. QC3D lưu thêm lịch sử (ai, khi nào, ghi chú, số liệu) và mẫu nghiệm thu.

## 2. Hành động

| action | Từ status | Sang status | Ai được làm | Điều kiện |
| --- | --- | --- | --- | --- |
| `submit` | in_progress, returned | waiting_review | người gán nhãn của job, lead | — |
| `start_review` | waiting_review | in_review | reviewer, lead | — |
| `review_pass` | in_review | waiting_acceptance | reviewer, lead | có lần QC **toàn job** còn hiệu lực (không cũ), 0 vấn đề **nghiêm trọng** còn mở |
| `review_return` | waiting_review, in_review | returned | reviewer, lead | `note` bắt buộc |
| `start_acceptance` | waiting_acceptance | in_acceptance | qa, lead | tạo mẫu frame theo tiêu chí |
| `accept` | in_acceptance | accepted | qa, lead | mọi frame mẫu đã chấm, đạt tiêu chí; không đạt thì chỉ lead làm được, kèm `override: true` và `note` |
| `reject` | in_acceptance | returned | qa, lead | `note` bắt buộc |
| `sign_off` | accepted | signed_off | lead | — |
| `reopen` | accepted, signed_off | in_progress | lead | `note` bắt buộc |

Server luôn tự kiểm tra quyền và điều kiện. UI chỉ hiển thị các hành động có trong `actions`.

## 3. Tiêu chí nghiệm thu (trong guideline)

`content.acceptance` của guideline. Thiếu khóa nào thì dùng mặc định:

```json
{ "sample_ratio": 0.1, "min_frames": 5, "max_error_rate": 0.02, "max_open_blockers": 0 }
```

- **Mẫu:** `max(min_frames, ceil(sample_ratio × số frame))` frame, chọn ngẫu nhiên có seed (lưu lại), không vượt số frame của job.
- **Tỷ lệ lỗi:** tổng `errors` / tổng `boxes_checked` trên các frame mẫu.
- **Đạt khi:**
  - tỷ lệ lỗi ≤ `max_error_rate`;
  - số vấn đề QC nghiêm trọng còn mở ≤ `max_open_blockers`.

## 4. Endpoint

### 4.1 `GET jobs/{job_id}/workflow`

```json
{
  "job": {"id": 23, "task_id": 25, "task_name": "complex_urban", "frames": 84,
          "assignee": {"id": 5, "username": "an"}},
  "status": "in_acceptance", "status_text": "Đang nghiệm thu",
  "stage": "acceptance", "state": "in progress",
  "criteria": {"sample_ratio": 0.1, "min_frames": 5, "max_error_rate": 0.02, "max_open_blockers": 0},
  "qc": {"run_id": 70, "run_date": "2026-10-15T08:00:00Z", "whole_job": true, "stale_reason": null,
         "open": {"blocker": 0, "major": 3, "minor": 7}, "fixed": 41, "waived": 5, "total": 56},
  "acceptance": {
    "id": 3, "round": 1, "seed": 917, "frames": [4, 17, 30, 52, 66, 80, 81, 83, 9],
    "results": {"4": {"boxes_checked": 25, "errors": 0, "note": "", "by": "qa1", "date": "…"}},
    "checked_frames": 1, "boxes_checked": 25, "errors": 0, "error_rate": 0.0, "passes": null
  },
  "actions": [
    {"action": "accept", "label": "Đạt nghiệm thu", "enabled": false, "reason": "Còn 8 frame mẫu chưa chấm"},
    {"action": "reject", "label": "Không đạt", "enabled": true, "reason": ""}
  ],
  "history": [
    {"id": 12, "action": "review_pass", "from_status": "in_review", "to_status": "waiting_acceptance",
     "user": {"id": 3, "username": "reviewer1"}, "date": "…", "note": "",
     "metrics": {"run_id": 70, "open": {"blocker": 0, "major": 3, "minor": 7}}}
  ]
}
```

- `acceptance`: `null` khi chưa lấy mẫu. `passes` là `null` khi chưa chấm hết, `true`/`false` khi đã chấm hết.
- `history`: mới nhất ở đầu.

### 4.2 `POST jobs/{job_id}/workflow`

Body: `{"action": "review_return", "note": "Box xe máy frame 12-20 lệch", "override": false}`

- Thành công: trả lại đúng như `GET` (trạng thái mới).
- Lỗi: `409` khi sai trạng thái hoặc chưa đủ điều kiện, `403` khi không có quyền, `400` khi thiếu `note`. Body lỗi: `{"detail": "lý do bằng tiếng Việt"}`.

### 4.3 `PUT jobs/{job_id}/acceptance/frames/{frame}`

Body: `{"boxes_checked": 25, "errors": 1, "note": "Car #1853 thấp 0,4 m"}`

- Chỉ khi status là `in_acceptance` và frame thuộc mẫu.
- Ràng buộc: `boxes_checked` ≥ 0, 0 ≤ `errors` ≤ `boxes_checked`.
- Quyền: qa, lead.
- Trả về như `GET jobs/{id}/workflow`.

### 4.4 `GET workflow/board`

Query (đều tùy chọn): `status`, `task`, `assignee` (user id), `search`.

```json
{
  "columns": [{"status": "in_progress", "text": "Đang gán nhãn", "count": 4}, "…đủ 8 trạng thái theo thứ tự quy trình…"],
  "jobs": [
    {"id": 23, "task_id": 25, "task_name": "complex_urban", "frames": 84, "status": "in_review",
     "status_text": "Đang QC", "assignee": {"id": 5, "username": "an"},
     "qc_open": {"blocker": 0, "major": 3, "minor": 7}, "error_rate": null,
     "last_action": {"action": "start_review", "user": "reviewer1", "date": "…"}, "updated_date": "…"}
  ]
}
```

Danh sách chỉ gồm job 3D mà người dùng xem được. Trang hiển thị dạng kanban theo cột, có thêm chế độ bảng.

### 4.5 `GET jobs/{job_id}/overview`

Bản đồ tổng quan job nhìn từ trên xuống. Tọa độ là hệ của frame đầu: chuyển động xe được ước lượng từ chính các box (`qc/objects.py`), nối liên tiếp qua các frame.

```json
{
  "method": "estimated", "extent": [-60.0, -40.0, 180.0, 40.0],
  "trajectory": [{"frame": 0, "x": 0.0, "y": 0.0, "yaw": 0.0}, {"frame": 1, "x": 1.1, "y": 0.0, "yaw": 0.01}],
  "issues": [{"issue_id": 901, "uid": "s:1853", "frame": 12, "rule": "R13", "severity": "major",
              "label": "Car", "status": "open", "x": 31.2, "y": -4.0}],
  "objects": [{"frame": 12, "x": 31.2, "y": -4.0, "label": "Car"}]
}
```

- Số điểm được giới hạn: `objects` tối đa 3.000 (lấy mẫu đều), `issues` tối đa 2.000.
- Bấm vào một vấn đề sẽ mở trang gán nhãn: `/tasks/{task_id}/jobs/{job_id}?frame={frame}&qcFocus={uid}&qcFrame={frame}`. Panel QC đã hỗ trợ sẵn hai tham số `qcFocus` / `qcFrame`.

## 5. Phân việc Sprint 4

| Mã | Việc | Ai |
| --- | --- | --- |
| BE-1 | Model `QcWorkflowEvent`, `QcAcceptance` + migration; dịch vụ chuyển trạng thái; tiêu chí trong guideline | Claude |
| BE-2 | Endpoint 4.1–4.5, quyền, kiểm tra điều kiện | Claude |
| BE-3 | Test backend cho mọi chuyển trạng thái, quyền, tiêu chí, mẫu | Claude |
| UI-1 | Trang **Trạng thái job** `/qc3d/board`: kanban 8 cột + chế độ bảng, lọc, đi tới job | Claude |
| UI-2 | Trang **Nghiệm thu QA** `/qc3d/qa` (thay `PlannedPage`): danh sách chờ/đang nghiệm thu; khu chấm mẫu: frame mẫu, mở frame trên CVAT, nhập số box/lỗi/ghi chú, tỷ lệ lỗi trực tiếp so với ngưỡng, nút Đạt / Không đạt | Claude |
| UI-3 | Trang job `/qc3d/jobs/:id`: thanh bước quy trình, nút hành động theo `actions`, dòng thời gian lịch sử, tóm tắt QC, **bản đồ tổng quan** (SVG/canvas; màu theo mức lỗi; bấm để mở frame) | Claude |
| UI-4 | Hàng đợi review `/qc3d/review`: nút Bắt đầu QC / Đạt / Trả lại (kèm ghi chú) theo `actions` | Claude |
| UI-5 | Biểu đồ Tổng quan / Phân tích / Chất lượng của tôi: bảng màu thống nhất, trạng thái rỗng, responsive; thêm phễu quy trình và tỷ lệ đạt nghiệm thu | Claude |
| UI-6 | Cảnh 3D khi duyệt: nhãn trên box dễ đọc, lưới mặt đất và vòng khoảng cách 10/20/30 m (bật/tắt ở tab Hiển thị), chú giải màu | Claude |
| UI-7 | Trình soạn guideline: nhập tiêu chí nghiệm thu (mục 3) | Claude |
| INT-1 | Review PR, build và deploy, kiểm tra toàn luồng trên CVAT local, cập nhật tài liệu | Claude |

Thứ tự:
- BE-1 → BE-2 (xong) → UI-3, UI-2, UI-1, UI-4 → UI-5, UI-6, UI-7 → INT-1.
