# Triển khai CineZero

## A. Deploy ngay, chưa dùng D1/R2
Mở PowerShell trong thư mục dự án và chạy:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\01-deploy-ngay.ps1
```

Sau khi đăng nhập Cloudflare, Wrangler trả về URL dạng:

```text
https://cinezero-web.<tai-khoan>.workers.dev
```

Website chạy online từ Cloudflare. Máy tính có thể tắt sau khi deploy.

## B. Bật database D1 và kho video R2

```powershell
.\scripts\02-tao-cloud-full.ps1
```

Script tạo D1, R2, schema, dữ liệu mẫu, secret quản trị rồi deploy. Khi Wrangler tạo D1, sao chép `database_id` vào `wrangler.full.jsonc` theo hướng dẫn trên màn hình.

## C. Upload video

```powershell
.\scripts\encode-video.ps1 -InputFile "D:\phim\video-goc.mkv" -Slug "ten-phim"
.\scripts\upload-r2.ps1 -File ".\content\encoded\ten-phim-720p.mp4" -Key "movies/ten-phim/movie-720p.mp4"
```

Sau đó mở `/admin.html`, thêm hoặc sửa phim với:

```text
video_key = movies/ten-phim/movie-720p.mp4
```

## D. Cập nhật website

```powershell
.\scripts\03-deploy-cap-nhat.ps1
```

## E. GitHub tự deploy
Thêm hai repository secrets:
- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ACCOUNT_ID`

Mỗi lần push nhánh `main`, GitHub Actions sẽ kiểm tra và deploy. Với bản full, workflow cần dùng file cấu hình đã chứa D1 binding nhưng tuyệt đối không commit secret quản trị.
