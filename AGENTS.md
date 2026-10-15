# Quy trình nhóm dự án QC 3D AutoFix (Tools-QC3D-CVAT)

Repo: https://github.com/HvLonggg/Tools-QC3D-CVAT. Dự án dựa trên CVAT v2.74.1.

Tài liệu này dành cho mọi thành viên trong nhóm, kể cả agent AI (Claude Code). Đọc hết trước khi làm việc.

## 1. Vai trò

| Vai trò | Ai | Phụ trách |
| --- | --- | --- |
| Chủ dự án (PO) | Hoàng Long (@HvLonggg) | Chốt yêu cầu, nghiệm thu nghiệp vụ, merge vào `main` |
| Kỹ sư dự án (backend + frontend + tích hợp) | Claude Code | Luồng nghiệp vụ, API, model, migration, giao diện plugin QC3D, biểu đồ, cảnh 3D (canvas3d), test, build/deploy, PR vào `develop` |

## 2. Nhánh và Pull Request

- `main`: bản ổn định, đã nghiệm thu. Chỉ PO merge vào.
- `develop`: nhánh tích hợp. Mọi việc vào đây qua Pull Request (PR).
- Mỗi việc làm trên nhánh riêng tách từ `develop`:
  - đặt tên `feature/<issue>-<ngắn-gọn>`, ví dụ `feature/12-qa-page`;
  - sửa lỗi dùng `fix/<issue>-...`.
- Mỗi PR:
  - đóng đúng 1 Issue (`Closes #12`);
  - điền đủ mẫu PR (mục 6);
  - được tech lead review;
  - chỉ merge khi test đạt.
- Không force-push lên `develop` hoặc `main`. Không commit trực tiếp lên hai nhánh này.
- Người hoặc agent thứ hai tham gia: làm trong worktree riêng, không dùng chung thư mục:

  ```
  git worktree add ../cvat-day2-<tên> -b feature/<issue>-<tên> develop
  ```

## 3. Phạm vi file

| Khu vực | Ghi chú |
| --- | --- |
| `cvat/apps/qc3d/**` | Backend, migration, test |
| `utils/qc3d/**`, `doc/qc3d/**` | Công cụ dữ liệu / model, tài liệu |
| `cvat-ui/plugins/qc3d/**` | Trang, panel, `api.ts`, styles |
| `cvat-canvas3d/src/**` | Giữ nguyên các hàm `configure` mà plugin đang dùng (`qcMarkers`, `pointColorMode`…) |
| `doc/qc3d/12-nghiem-thu-api.md` | Hợp đồng API: đổi API thì cập nhật file này trong cùng PR |

## 4. Quy tắc kỹ thuật

- Chữ trên giao diện bằng tiếng Việt, ngắn, rõ, đúng thuật ngữ đang dùng (Duyệt, Autofix, Nghiệm thu, Chạy QC…).
- Không thêm thư viện mới khi PO chưa đồng ý. Biểu đồ dùng `chart.js` có sẵn. UI dùng `antd` 5 có sẵn.
- Không hard-code bí mật, URL máy, ID job.
- Không làm yếu kiểm tra quyền phía server. Ẩn nút trên UI chỉ là phụ; server luôn tự kiểm tra quyền.
- Máy dev chỉ có 8 GB RAM:
  - không chạy build UI đầy đủ song song với việc khác;
  - kiểm tra kiểu dùng lệnh ở mục 5;
  - build image chỉ làm khi tích hợp, do tech lead chạy.

## 5. Test bắt buộc trước khi mở PR

Chạy từ thư mục gốc repo. Lấy đường dẫn Windows của repo bằng `pwd -W` (Git Bash).

**Frontend:** kiểm tra kiểu TypeScript, phải ra **0 lỗi**. Dùng image build có sẵn, không cần cài `node_modules`:

```bash
R=$(pwd -W)
MSYS_NO_PATHCONV=1 docker run --rm -m 2500m \
  -v "$R/cvat-ui/plugins:/tmp/cvat-ui/plugins:ro" -v "$R/cvat-ui/src:/tmp/cvat-ui/src:ro" \
  -v "$R/cvat-canvas3d/src:/tmp/cvat-canvas3d/src:ro" -w /tmp/cvat-ui --entrypoint sh cvat-ui-build:qc3d \
  -c "node --max-old-space-size=2000 /tmp/node_modules/.bin/tsc --noEmit -p . | grep 'error TS'; echo done"
```

**Backend:** toàn bộ test phải đạt:

```bash
.venv-qc3d/Scripts/python.exe -m pytest -q cvat/apps/qc3d/tests
```

**Nghiệm thu chức năng:**
- Làm theo các bước "Cách kiểm tra" ghi trong Issue, trên CVAT local `http://localhost:8070`, với job 3D có sẵn (job 23: 84 frame; job 24: 10 frame).
- Đính kèm ảnh chụp màn hình vào PR.

## 6. Mẫu báo cáo trong PR

Dùng mẫu `.github/pull_request_template.md`:
- làm gì, theo Issue nào;
- file chính đã sửa;
- đã test gì và kết quả (dán dòng kết quả `tsc` / `pytest`);
- ảnh chụp;
- điểm chưa xong hoặc rủi ro.

**Không ghi "đã test" khi chưa chạy.**

## 7. Định nghĩa hoàn thành (Definition of Done)

Một việc chỉ được tính là xong khi:
1. đạt các tiêu chí nghiệm thu trong Issue;
2. test mục 5 đạt;
3. PR được review và merge vào `develop`;
4. tech lead build, deploy lên CVAT local và kiểm tra lại;
5. tài liệu liên quan đã cập nhật.

PO nghiệm thu từng sprint trên `develop` trước khi merge vào `main`.
