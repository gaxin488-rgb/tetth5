# Auto subtitle CineZero — local, miễn phí

Pipeline này tạo phụ đề rời WebVTT trên chính máy xử lý. Video gốc không bị encode lại và không bị gắn chữ trực tiếp vào hình.

Mục tiêu của pipeline:

- WhisperX tự nhận diện ngôn ngữ, chuyển giọng nói thành văn bản và căn timestamp.
- WhisperX/pyannote diarization tách người nói, sau đó VTT gắn nhãn `[Người nói 1]`, `[Người nói 2]`.
- inaSpeechSegmenter phân biệt vùng `speech`, `music`, `noise` và `noEnergy`. Giọng hát được xem là `music`, nên lời bài hát bị loại; lời thoại nói trên nền nhạc vẫn có thể giữ lại.
- Chỉ file `.vtt` được upload lên R2 và đăng ký trong D1. Player nạp bằng HTML5 `<track>`.

Tất cả model chạy local, không gọi API AI trả phí và không cần khóa API AI. WhisperX là dự án mã nguồn mở hỗ trợ ASR, word timestamps và diarization; inaSpeechSegmenter là dự án mã nguồn mở dùng để tách speech/music/noise: [WhisperX](https://github.com/m-bain/whisperX), [inaSpeechSegmenter](https://github.com/ina-foss/inaSpeechSegmenter).

## Phân vai hệ thống

- PC: tải video, nhận diện ngôn ngữ/người nói, lọc nhạc và âm thanh ngoài; bạn kiểm tra `.vtt` và `.segments.json` trước khi phát hành.
- GitHub: lưu mã nguồn và chạy kiểm tra; Cloudflare Workers Builds nhận commit để deploy website; không chạy nhận diện video.
- Cloudflare Worker/D1: phục vụ website, API và thông tin track subtitle.
- Cloudflare R2: lưu video gốc, video đã nén và file WebVTT rời.

## Chuẩn bị

Cần:

- Windows có Node.js 20+, Python 3.13 hoặc thấp hơn và FFmpeg trong `PATH`.
- Máy có đủ CPU/RAM hoặc GPU. Model càng lớn thì nhận dạng tốt hơn nhưng chạy lâu hơn.
- Cloudflare Worker/D1/R2 chỉ cần nằm trong hạn mức miễn phí của tài khoản. Việc nhận dạng diễn ra local nên không phát sinh phí theo phút từ API.

Chạy tại thư mục source deploy:

```powershell
cd D:\web\CineZero_V2_D_web
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\setup-free-subtitles.ps1
```

Script tạo `.venv-subtitles` và cài package miễn phí từ `requirements-free.txt`. Lần chạy đầu tiên model sẽ được tải về cache local.

## Diarization người nói miễn phí

Để nhận diện người nói, cần một Hugging Face read token miễn phí vì model pyannote yêu cầu quyền tải model. Không cần thẻ và không có phí API:

1. Tạo tài khoản Hugging Face.
2. Chấp nhận điều khoản model `pyannote/speaker-diarization-community-1`.
3. Tạo token quyền `read` và chỉ đặt trong terminal hiện tại:

```powershell
$env:HF_TOKEN = 'hf_token_cua_ban'
```

Nếu không muốn dùng diarization, chạy thêm `--no-diarize`; phụ đề vẫn có nhận diện ngôn ngữ và lọc speech/music/noise nhưng không có người nói.

## Rule nhân vật và xưng hô tiếng Việt

Diarization chỉ biết các cụm giọng như `SPEAKER_00`, `SPEAKER_01`; nó không thể tự biết đó là Haruka, Sora hay nhân vật nào. Bộ rule local dùng profile để ánh xạ ID giọng sang nhân vật, lưu tuổi/giới tính/vai trò đã kiểm tra, rồi chọn xưng hô theo quan hệ và người đang được nói tới.

Profile mẫu cho video hiện tại nằm ở `profiles/yosuga-no-sora-01.json`. Sau lần chạy có diarization, mở `segments.json`, nghe từng `SPEAKER_XX`, rồi điền:

```json
"speaker_map": {
  "SPEAKER_00": "haruka",
  "SPEAKER_01": "sora"
}
```

Nếu một cảnh có nhiều người, thêm người nghe theo khoảng thời gian:

```json
"scene_targets": [
  {"start": 34.4, "end": 90, "speaker": "SPEAKER_00", "listener": "sora"}
]
```

Chạy lại pipeline, không dùng `--no-diarize`:

```powershell
npm run subtitle:auto -- `
  --input "D:\phim\video-goc.mkv" `
  --slug "yosuga-no-sora-01" `
  --character-profile ".\profiles\yosuga-no-sora-01.json" `
  --pronoun-rules ".\config\pronoun-rules.vi.json"
```

Kết quả sẽ có tên nhân vật trong VTT khi bật nhãn người nói, còn `.segments.json` có thêm `character_name`, `character_age`, `character_gender`, `character_age_band`, `pronouns` và `needs_review`. Cue chưa map được nhân vật hoặc chưa biết người nghe sẽ bị đánh dấu để kiểm tra, không tự đoán âm thầm.

Tuổi và giới tính trong profile là dữ liệu người dùng xác nhận; không nên suy ra chắc chắn chỉ từ giọng nói. Rule chỉ tự động chọn xưng hô khi có đủ quan hệ, tuổi/vai trò; quan hệ đặc biệt như anh-em song sinh nên ghi trong `relations` để ưu tiên kết quả thủ công.

## Tạo phụ đề local

```powershell
npm run subtitle:auto -- --input "D:\phim\video-goc.mkv" --slug "hanh-trinh-sao-bang"
```

Mặc định dùng model `small`, phù hợp hơn cho CPU. Có thể chọn:

```powershell
# CPU nhẹ hơn, thường kém chính xác hơn
npm run subtitle:auto -- --input "D:\phim\video-goc.mkv" --slug "hanh-trinh-sao-bang" --model base --device cpu

# Máy có CUDA
npm run subtitle:auto -- --input "D:\phim\video-goc.mkv" --slug "hanh-trinh-sao-bang" --model medium --device cuda
```

Kết quả nằm trong:

- `content\generated-subtitles\<slug>.<language>.vtt`: track phụ đề rời.
- `content\generated-subtitles\<slug>.<language>.segments.json`: báo cáo vùng speech/music/noise và các câu bị loại để kiểm tra thủ công.

## Upload và đăng ký track

Đặt URL site và `ADMIN_TOKEN` của ứng dụng trong terminal, sau đó chạy pipeline:

```powershell
$env:CINEZERO_SITE_URL = 'https://cinezero-web.example.workers.dev'
$env:ADMIN_TOKEN = 'token-admin-cua-ban'

# upload-r2.ps1 sẽ kiểm tra giới hạn R2 9 GB trước khi upload

npm run subtitle:auto -- `
  --input "D:\phim\video-goc.mkv" `
  --slug "hanh-trinh-sao-bang" `
  --site-url $env:CINEZERO_SITE_URL `
  --admin-token $env:ADMIN_TOKEN
```

Khi upload file đã duyệt bằng `scripts/upload-r2.ps1`, script gọi `/api/admin/storage` và chặn nếu tổng dung lượng sau upload vượt 9 GB. Không dùng `npx wrangler r2 object put` trực tiếp nếu muốn giữ giới hạn này; `-SkipStorageGuard` chỉ dùng sau khi đã tự kiểm tra dung lượng.

Để nhận email khi có phát sinh phí, vào Cloudflare `Manage Account > Billing > Billable Usage > Create budget alert` và đặt ngưỡng rất thấp (ví dụ `0.01 USD`). Budget alert chỉ gửi cảnh báo, không tự dừng dịch vụ.

Hoặc upload video và tự tạo track trong một lệnh:

```powershell
.\scripts\upload-r2.ps1 `
  -File '.\content\encoded\hanh-trinh-sao-bang-720p.mp4' `
  -Key 'movies/hanh-trinh-sao-bang/movie-720p.mp4' `
  -AutoSubtitle `
  -Slug 'hanh-trinh-sao-bang' `
  -SiteUrl $env:CINEZERO_SITE_URL
```

Người xem bật/tắt phụ đề bằng nút `CC`. Video gốc và chất lượng hình ảnh không thay đổi.

## Kiểm tra trước khi publish

Để kiểm tra lời thoại, không truyền `CINEZERO_SITE_URL` và `ADMIN_TOKEN` khi chạy auto-sub. Sau khi sửa/duyệt file VTT, upload file đó lên R2:

```powershell
.\scripts\upload-r2.ps1 `
  -File '.\content\generated-subtitles\hanh-trinh-sao-bang.vi.vtt' `
  -Key 'subtitles/hanh-trinh-sao-bang/vi.vtt' `
  -ContentType 'text/vtt; charset=utf-8'
```

Mở `/admin.html`, nhập `ADMIN_TOKEN`, chọn phim, nhập mã ngôn ngữ `vi` và R2 key `subtitles/hanh-trinh-sao-bang/vi.vtt`, rồi đăng ký track. VTT được cache ngắn nên khi sửa lại sẽ cập nhật nhanh hơn video.

## Giới hạn cần biết

Nhận diện giọng hát không tuyệt đối trong cảnh vừa hát vừa nói, thoại chồng tiếng hoặc nhạc rất lớn. Hãy xem `segments.json` trước khi phát hành. WhisperX cũng ghi nhận diarization có thể sai ở các đoạn chồng giọng; khi cần, có thể sửa WebVTT bằng tay mà không cần xử lý lại video.
