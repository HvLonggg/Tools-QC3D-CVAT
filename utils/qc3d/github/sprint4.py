# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Create the Sprint 4 labels, milestone and issues on GitHub (needs `gh auth login` once):
  python utils/qc3d/github/sprint4.py [--repo HvLonggg/Tools-QC3D-CVAT] [--dry-run]
Issues that already exist (same title) are skipped, so the script can be run again.
"""

import argparse
import json
import shutil
import subprocess

GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
MILESTONE = "Sprint 4 – Duyệt, nghiệm thu, trạng thái job"
CONTRACT = "doc/qc3d/12-nghiem-thu-api.md"

LABELS = {
    "task": ("1d76db", "Việc trong sprint"),
    "bug": ("d73a4a", "Lỗi"),
    "area:backend": ("5319e7", "API, model, QC engine"),
    "area:ui": ("0e8a16", "Giao diện plugin QC3D, canvas 3D"),
    "owner:claude": ("c5def5", "Tech lead / backend (Claude Code)"),
    "owner:codex": ("bfd4f2", "Frontend (Codex)"),
    "priority:high": ("b60205", "Làm trước"),
}

TEST_UI = (
    "\n\n## Test bắt buộc\n- `tsc` theo AGENTS.md mục 5: 0 lỗi\n"
    "- Ảnh chụp màn hình trong PR\n- Chữ tiếng Việt, đúng thuật ngữ; không thêm thư viện mới"
)

ISSUES = [
    ("BE-1/2/3: Backend luồng duyệt, nghiệm thu, bảng trạng thái, bản đồ tổng quan",
     ["task", "area:backend", "owner:claude", "priority:high"],
     f"Làm đúng hợp đồng `{CONTRACT}` (mục 1–4).\n\n## Tiêu chí nghiệm thu\n"
     "- [ ] Model `QcWorkflowEvent`, `QcAcceptance` + migration\n"
     "- [ ] `GET/POST jobs/{id}/workflow`, `PUT jobs/{id}/acceptance/frames/{frame}`, `GET workflow/board`, "
     "`GET jobs/{id}/overview`\n- [ ] Kiểm tra quyền và điều kiện ở server (403/400/409 + lý do tiếng Việt)\n"
     "- [ ] Tiêu chí nghiệm thu trong guideline, có mặc định\n- [ ] Test backend đạt; chạy thử toàn luồng qua API\n\n"
     "## Cách kiểm tra\n`pytest -q cvat/apps/qc3d/tests`; chạy toàn luồng trên job 24 qua API (nộp → … → chốt → mở lại)."),
    ("UI-1: Trang Trạng thái job (/qc3d/board)",
     ["task", "area:ui", "owner:codex"],
     f"Dùng `GET workflow/board` (`{CONTRACT}` mục 4.4).\n\n## Tiêu chí nghiệm thu\n"
     "- [ ] Mục menu \"Trạng thái job\"\n- [ ] Kanban 8 cột theo thứ tự quy trình, có số lượng; thẻ job: tên task, #job, "
     "số frame, người gán nhãn, lỗi QC còn mở theo mức độ, tỷ lệ lỗi nghiệm thu, hành động cuối\n"
     "- [ ] Chế độ bảng (sắp xếp được) và bộ lọc: trạng thái, task, người, tìm kiếm\n"
     "- [ ] Bấm thẻ/dòng → trang job `/qc3d/jobs/:id`\n- [ ] Trạng thái rỗng, đang tải, lỗi rõ ràng; vừa màn hình 1366 px\n\n"
     "## Cách kiểm tra\nMở `/qc3d/board`: job 23, 24 hiện đúng cột; đổi trạng thái job 24 ở trang job → board cập nhật."
     + TEST_UI),
    ("UI-2: Trang Nghiệm thu QA (/qc3d/qa)",
     ["task", "area:ui", "owner:codex", "priority:high"],
     f"Thay `PlannedPage`. API: `jobs/{{id}}/workflow`, `acceptance/frames/{{frame}}` (`{CONTRACT}` mục 2–4).\n\n"
     "## Tiêu chí nghiệm thu\n- [ ] Danh sách job Chờ / Đang nghiệm thu (lấy từ board)\n"
     "- [ ] Khu chấm mẫu: danh sách frame mẫu, nút mở frame trên CVAT (`/tasks/{task}/jobs/{job}?frame=N`), "
     "nhập số box đã kiểm, số lỗi, ghi chú; lưu từng frame\n"
     "- [ ] Tỷ lệ lỗi hiện tại so với ngưỡng (thanh tiến độ, màu đạt/không đạt), số frame còn lại\n"
     "- [ ] Nút theo `actions`: Bắt đầu nghiệm thu, Đạt, Không đạt (ghi chú bắt buộc); `needs_override` → ô xác nhận "
     "duyệt vượt + lý do\n- [ ] Lỗi server hiện đúng `detail`\n\n## Cách kiểm tra\n"
     "Job 24 ở Chờ nghiệm thu → Bắt đầu → chấm đủ mẫu (một lần có lỗi vượt ngưỡng để thấy Không đạt) → Đạt." + TEST_UI),
    ("UI-3: Trang job – quy trình, lịch sử, bản đồ tổng quan",
     ["task", "area:ui", "owner:codex", "priority:high"],
     f"API: `jobs/{{id}}/workflow`, `jobs/{{id}}/overview` (`{CONTRACT}` mục 4.1, 4.2, 4.5).\n\n## Tiêu chí nghiệm thu\n"
     "- [ ] Thanh bước: Gán nhãn → QC → Nghiệm thu → Chốt, bước hiện tại nổi bật; trạng thái Bị trả lại rõ ràng\n"
     "- [ ] Nút hành động theo `actions` (ẩn nếu không có; disabled kèm `reason` dạng tooltip; hỏi ghi chú khi "
     "`note_required`)\n- [ ] Dòng thời gian lịch sử: ai, khi nào, hành động, ghi chú, số liệu chính\n"
     "- [ ] Tóm tắt QC: lần chạy, còn mở theo mức độ, đã sửa, miễn; cảnh báo khi QC cũ\n"
     "- [ ] Bản đồ tổng quan (SVG hoặc canvas, không thư viện mới): quỹ đạo xe, vật (chấm nhỏ theo class), "
     "vấn đề QC (màu theo mức độ, ẩn/hiện theo mức độ và trạng thái), zoom/kéo, tooltip; bấm vấn đề → mở "
     "`/tasks/{task}/jobs/{job}?frame=F&qcFocus=UID&qcFrame=F`; ghi rõ \"vị trí ước lượng\"\n\n"
     "## Cách kiểm tra\nJob 23: bản đồ hiện ~84 điểm quỹ đạo, ~2.000 vật, các vấn đề; bấm 1 vấn đề → mở đúng frame "
     "và chọn đúng box." + TEST_UI),
    ("UI-4: Hàng đợi review – hành động duyệt",
     ["task", "area:ui", "owner:codex"],
     f"`/qc3d/review`: thêm Bắt đầu QC / QC đạt / Trả lại (ghi chú) theo `actions` (`{CONTRACT}` mục 2).\n\n"
     "## Tiêu chí nghiệm thu\n- [ ] Mỗi job hiện trạng thái quy trình và các nút được phép\n"
     "- [ ] QC đạt bị khoá kèm lý do khi QC cũ / chưa chạy toàn job / còn lỗi nghiêm trọng\n"
     "- [ ] Trả lại bắt buộc ghi chú; sau hành động danh sách cập nhật\n\n## Cách kiểm tra\n"
     "Job 24: Nộp → Bắt đầu QC → thử QC đạt khi QC cũ (bị khoá) → Chạy QC → QC đạt." + TEST_UI),
    ("UI-5: Hoàn thiện biểu đồ trang QC3D",
     ["task", "area:ui", "owner:codex"],
     "Trang Tổng quan, Phân tích, Chất lượng của tôi (chart.js có sẵn).\n\n## Tiêu chí nghiệm thu\n"
     "- [ ] Bảng màu thống nhất, có chú giải; mức độ lỗi: nghiêm trọng #d9384a, cần xử lý #a86400, lưu ý #2875b5\n"
     "- [ ] Trạng thái rỗng / đang tải cho mọi biểu đồ; tooltip có đơn vị; số định dạng vi-VN\n"
     "- [ ] Responsive 1366–1920 px; không tràn chữ\n"
     "- [ ] Thêm: phễu quy trình (số job mỗi trạng thái, từ `workflow/board`) và tỷ lệ đạt nghiệm thu\n\n"
     "## Cách kiểm tra\nMở 3 trang, so ảnh trước/sau trong PR." + TEST_UI),
    ("UI-6: Cảnh 3D khi duyệt – dễ nhìn hơn",
     ["task", "area:ui", "owner:codex"],
     "`cvat-canvas3d` + tab Hiển thị của panel QC Check 3D.\n\n## Tiêu chí nghiệm thu\n"
     "- [ ] Nhãn trên box dễ đọc (nền mờ, cỡ chữ theo khoảng cách, không che nhau khi đông)\n"
     "- [ ] Lưới mặt đất và vòng khoảng cách 10/20/30 m quanh xe, bật/tắt ở tab Hiển thị (lưu localStorage)\n"
     "- [ ] Chú giải màu điểm theo chế độ tô màu đang chọn\n- [ ] Không giảm FPS rõ rệt trên job 23 (ghi số FPS trước/sau)\n"
     "- [ ] Giữ nguyên `configure` hiện có (`qcMarkers`, `pointColorMode`, …)\n\n## Cách kiểm tra\n"
     "Job 23 frame 0: bật/tắt lưới, vòng khoảng cách; đổi chế độ tô màu; ảnh trước/sau." + TEST_UI),
    ("UI-7: Guideline – tiêu chí nghiệm thu",
     ["task", "area:ui", "owner:codex"],
     f"Trình soạn guideline: nhập `acceptance` (`{CONTRACT}` mục 3; giới hạn ở `qc/guideline.py` ACCEPTANCE_LIMITS).\n\n"
     "## Tiêu chí nghiệm thu\n- [ ] 4 ô: tỷ lệ frame mẫu (%), số frame tối thiểu, tỷ lệ lỗi tối đa (%), lỗi nghiêm trọng "
     "còn mở tối đa; hiện mặc định\n- [ ] Lưu được; lỗi giới hạn hiện đúng thông báo server\n\n"
     "## Cách kiểm tra\nSửa guideline mặc định: tỷ lệ mẫu 20% → bắt đầu nghiệm thu job 24 → mẫu 5 frame (min)." + TEST_UI),
    ("INT-1: Tích hợp Sprint 4 – review PR, build, kiểm tra toàn luồng",
     ["task", "area:backend", "owner:claude"],
     "## Tiêu chí nghiệm thu\n- [ ] Review và merge các PR UI vào `develop`\n"
     "- [ ] Build image UI, deploy local, `tsc` 0 lỗi, `pytest` đạt\n"
     "- [ ] Chạy toàn luồng trên CVAT local bằng trình duyệt: gán nhãn → QC → nghiệm thu → chốt; ảnh chụp\n"
     "- [ ] Cập nhật tài liệu; PR `develop` → `main` cho PO nghiệm thu sprint"),
]


def gh(*args, check=True):
    return subprocess.run([GH, *args], capture_output=True, text=True, encoding="utf-8", check=check)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="HvLonggg/Tools-QC3D-CVAT")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.dry_run:
        for title, labels, body in ISSUES:
            print(f"- {title} [{', '.join(labels)}] ({len(body)} ký tự)")
        return
    for name, (color, description) in LABELS.items():
        gh("label", "create", name, "--repo", args.repo, "--color", color, "--description", description, "--force")
    milestones = json.loads(gh("api", f"repos/{args.repo}/milestones?state=all").stdout)
    if not any(m["title"] == MILESTONE for m in milestones):
        gh("api", f"repos/{args.repo}/milestones", "-f", f"title={MILESTONE}")
    existing = {i["title"] for i in json.loads(gh("issue", "list", "--repo", args.repo, "--state", "all",
                                                  "--limit", "200", "--json", "title").stdout)}
    for title, labels, body in ISSUES:
        if title in existing:
            print("đã có:", title)
            continue
        out = gh("issue", "create", "--repo", args.repo, "--title", title, "--body", body,
                 "--milestone", MILESTONE, *sum((["--label", label] for label in labels), []))
        print(out.stdout.strip())


if __name__ == "__main__":
    main()
