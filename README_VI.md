# CineZero UX/UI V2

## Mở xem ngay trên Windows

Mở giao diện xem thử bằng `D:\web\MO_WEB.bat` hoặc `D:\web\preview\index.html`. Bản V2 hỗ trợ `file://`, CSS, ảnh, JavaScript và dữ liệu mẫu khi bấm trực tiếp file.

> Website production vẫn nên deploy lên Cloudflare bằng script; chế độ mở file chỉ dùng để xem trước giao diện.

# CineZero — Web phim cloud 0đ

Bộ source này được thiết kế để giải nén vào:

```text
D:\web
```

## Đã có sẵn
- Giao diện dark cinema responsive.
- Trang chủ, duyệt phim, chi tiết, xem phim, tìm kiếm, yêu thích.
- Player HTML5 và lưu tiến độ trên trình duyệt.
- Worker API, D1 schema, R2 streaming có Range request.
- Trang quản trị `/admin.html`.
- Script nén video bằng FFmpeg và upload R2.
- Auto subtitle WebVTT local 0đ: nhận diện ngôn ngữ, người nói, speech/music/noise và lọc lời nhạc mà không encode lại video. Xem [docs/AUTO_SUB.md](docs/AUTO_SUB.md).
- GitHub Actions chỉ kiểm tra source; Cloudflare Workers Builds deploy website. Nhận diện subtitle chạy trên PC để có thể kiểm tra lời thoại trước khi publish.
- Chế độ deploy ngay không cần D1/R2.
- GitHub Actions để tự deploy.

Kiến trúc production: PC nhận diện và kiểm tra phụ đề → GitHub lưu source/chạy kiểm tra → Cloudflare Workers Builds deploy → Cloudflare Worker/D1 phục vụ website/API → Cloudflare R2 lưu video và WebVTT.

## Cài vào D:\web
Giữ source trong `D:\web\CineZero_V2_D_web`. Bản preview nằm riêng trong `D:\web\preview`; các gói ZIP nằm trong `D:\web\releases`.

Chạy các lệnh `npm` và `scripts\*.ps1` từ `D:\web\CineZero_V2_D_web`. Không đặt video, cache hoặc file tạm vào thư mục gốc.

## Chạy triển khai đầu tiên

```powershell
cd D:\web\CineZero_V2_D_web
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\01-deploy-ngay.ps1
```

Bản này dùng dữ liệu tại `public\data\movies.json`, nhưng website thật chạy trên Cloudflare chứ không chạy local.

## Bật dữ liệu thật

```powershell
.\scripts\02-tao-cloud-full.ps1
```

Sau khi hoàn tất:
- D1 lưu metadata phim.
- R2 lưu video/poster/phụ đề.
- `/admin.html` thêm và xóa phim.
- `/media/<video_key>` stream video từ R2.

## Lưu ý nội dung
Chỉ upload nội dung bạn sở hữu, được cấp phép, public domain hoặc Creative Commons. Video demo trong dữ liệu mẫu là video kiểm thử CC0 của MDN; thay bằng nội dung của bạn khi đưa website vào sử dụng.

## Không đưa vào Git
- Video gốc hoặc video đã nén.
- `wrangler.full.jsonc` nếu bạn muốn giữ ID tài nguyên riêng tư khỏi repository public.
- `.dev.vars`, token và thông tin bí mật.
