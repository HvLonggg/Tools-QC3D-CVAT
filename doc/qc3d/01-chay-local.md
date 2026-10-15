# QC 3D AutoFix: chạy trên CVAT local

Phạm vi: Tổng quan, hàng đợi review, guideline, kiểm tra và AutoFix trong job 3D.
Áp dụng cho CVAT v2.74.1 chạy bằng Docker Desktop trên Windows.

## Chạy cấu hình nhẹ (cập nhật 12/10/2026)

```powershell
# Chỉ xem / gán nhãn job hiện có, chạy QC và AutoFix:
powershell -NoProfile -File utils\qc3d\start_local.ps1

# Khi cần tạo task hoặc nhập / xuất dữ liệu:
powershell -NoProfile -File utils\qc3d\start_local.ps1 -Mode data

# Quay lại cấu hình nhẹ sau khi nhập / xuất hoàn tất:
powershell -NoProfile -File utils\qc3d\start_local.ps1 -Mode light
```

Mở **http://localhost:8070/qc3d**. Cấu hình nhẹ chạy server và worker chunks với một
process mỗi dịch vụ. Worker import/export/annotation/utils, Grafana, webhooks, quality reports,
consensus và Nuclio/Mask2Former chỉ bật khi có nhu cầu. ClickHouse/Vector vẫn chạy cho event log;
ClickHouse giới hạn 0,5 CPU để các tác vụ nền không chiếm hết CPU. Không xóa dữ liệu hay volume.

Script chỉ dừng các container CVAT phụ có tên xác định; không dừng dự án khác.
Worker nhập/xuất chưa bật thì yêu cầu tương ứng sẽ chờ trong hàng đợi. Bật chế độ `data` để xử lý.
Chế độ `full` bật các worker và dashboard tùy chọn của CVAT; không tự bật model Nuclio riêng.

Build tiết kiệm RAM có thể dùng image cache `cvat-ui-build:qc3d`, chép source mới vào `/tmp`,
rồi chạy `node --max-old-space-size=1800 utils/qc3d/build_ui.cjs` từ `/tmp`.
Script minify tuần tự, không tạo worker theo tất cả CPU. Dừng server/chunks trong khi build trên máy 8 GB,
rồi bật lại sau khi build. Đóng gói bundle đã build ở `cvat-ui/dist` bằng:

```powershell
docker build --network none -f utils/qc3d/Dockerfile.ui-runtime -t cvat/ui:qc3d .
powershell -NoProfile -File utils\qc3d\start_local.ps1
```

Các mục bên dưới là hướng dẫn triển khai ban đầu; ưu tiên cấu hình nhẹ ở trên cho máy này.

## 0. Cấu hình máy đã đo (09/10/2026)

| Mục | Giá trị | Ảnh hưởng |
| --- | --- | --- |
| RAM máy | 7,7 GB | Đủ chạy CVAT; build UI cần tắt bớt ứng dụng |
| RAM cấp cho Docker | 3,7 GB | Thiếu cho bước build UI, cần tăng lên 5 GB |
| Ổ C: trống | 11,2 GB | Sát ngưỡng; nên dọn 9,3 GB build cache |
| GPU | Intel UHD (không có NVIDIA) | Chạy QC rule được; **không train được model 3D trên máy này** |

## 1. Tăng RAM cho Docker (làm 1 lần)

Tạo file `C:\Users\<user>\.wslconfig`:

```ini
[wsl2]
memory=5GB
swap=8GB
processors=8
```

Sau đó chạy `wsl --shutdown` rồi mở lại Docker Desktop. Khi build, nên đóng bớt trình duyệt và các ứng dụng nặng.

## 2. Sửa Redis bị lỗi file AOF

Triệu chứng: `cvat_redis_inmem` liên tục ở trạng thái `Restarting`, log báo `Bad file format reading the append only file`.
Nguyên nhân thường gặp: máy tắt đột ngột khi Redis đang ghi. Cách sửa: sao lưu file rồi cắt phần cuối bị hỏng.

```powershell
docker stop cvat_redis_inmem
docker run --rm -v cvat_cvat_inmem_db:/data redis:7.2.11-alpine sh -c "cp -a /data/appendonlydir /data/appendonlydir.bak-20261009 && cd /data/appendonlydir && yes | redis-check-aof --fix appendonly.aof.manifest"
docker start cvat_redis_inmem
docker ps --filter name=cvat_redis_inmem
```

Kết quả đúng: trạng thái `Up`, không còn `Restarting`.

## 3. Giải phóng tài nguyên

```powershell
# Tắt model 2D Mask2Former và Nuclio (dự án 3D chưa dùng); bật lại bằng docker start
docker stop nuclio-nuclio-pth-facebookresearch-mask2former-cityscapes-panoptic nuclio nuclio-local-storage-reader

# Xóa build cache cũ (khoảng 9,3 GB, chỉ là cache)
docker builder prune -f
```

## 4. Build UI và khởi động CVAT có QC 3D

```powershell
cd C:\Users\HoangVanLong\cvat-day2

# Dừng mọi container để dồn RAM cho bước build (khoảng 15–30 phút lần đầu)
docker stop $(docker ps -q)

docker compose -f docker-compose.yml -f docker-compose.qc3d.yml build cvat_ui
docker compose -f docker-compose.yml -f docker-compose.qc3d.yml up -d
```

Backend không cần build lại: `docker-compose.qc3d.yml` mount `cvat/apps/qc3d`, `cvat/settings/base.py` và `cvat/urls.py` vào `cvat_server`. Code trong image đã được đối chiếu và trùng khớp với repo.

## 5. Kiểm tra (tiêu chí hoàn thành Sprint 1)

| # | Kiểm tra | Lệnh / thao tác | Kết quả đúng |
| --- | --- | --- | --- |
| 1 | Bảng QC đã tạo | `docker exec cvat_server python manage.py showmigrations qc3d` | `[X] 0001_initial` |
| 2 | API chạy | Đăng nhập CVAT, mở `http://localhost:8070/api/qc3d/summary` | JSON có `tasks_3d`, `jobs_3d`, `roles` |
| 3 | Nút trên thanh điều hướng | Mở `http://localhost:8070` | Có nút **QC 3D** sau **Models** |
| 4 | Các trang | Bấm QC 3D, chuyển qua 4 tab | Không lỗi, Tổng quan hiện số liệu |
| 5 | Menu job | Trang Jobs, menu `⋮` của một job 3D | Có mục **QC 3D**; job 2D thì không có |
| 6 | Phân quyền | Đăng nhập user thường | Chỉ thấy task/job mình được phép |

## 6. Gán vai trò QC

Cổng: máy này có dịch vụ Windows `MTAgentService` chiếm cổng 8080, nên `docker-compose.qc3d.yml` đưa CVAT ra cổng **8070**.

Nếu chưa có tài khoản admin, tạo bằng lệnh sau (tự nhập mật khẩu, không ghi mật khẩu vào tài liệu):

```powershell
docker exec -it cvat_server bash -ic "python3 ~/manage.py createsuperuser"
```

Admin mở `http://localhost:8070/admin/qc3d/qcmembership/`, chọn user và vai trò (`lead`, `reviewer`, `qa`, `ml_engineer`).

## 7. Quay về CVAT gốc

```powershell
docker compose -f docker-compose.yml up -d
```

Lệnh này dùng lại image gốc, không mount code QC 3D. Bảng `qc3d_qcmembership` vẫn còn trong DB nhưng không ảnh hưởng gì đến CVAT.
