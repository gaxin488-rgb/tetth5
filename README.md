# CineZero — kiểm tra video và đối chiếu báo cáo nhân vật

Tài liệu này mô tả quy trình kiểm tra video Yosuga no Sora tập 02–12 trên PC trước khi đưa WebVTT lên R2. Quy trình không encode lại video, không gắn phụ đề trực tiếp vào hình và không hiển thị tên nhân vật trong phụ đề.

## Mục tiêu kiểm tra

Mỗi cue thoại phải được đối chiếu giữa ba nguồn:

- video MP4 đã encode trong `D:\web\CineZero_V2_D_web\content\encoded`;
- VTT tiếng Việt nguồn `content\generated-subtitles\yosuga-no-sora-NN.vi.vtt`;
- báo cáo có tên nhân vật `content\generated-subtitles\yosuga-no-sora-NN.vi.named.segments.json` và VTT sạch tương ứng `...vi.named.vtt`.

Báo cáo phải cho phép kiểm tra người nói theo các trường:

| Trường | Ý nghĩa |
| --- | --- |
| `character_id` | ID ổn định của nhân vật, dùng để đối chiếu với `character_registry`. |
| `character_name` | Tên hiển thị của nhân vật. Đây là nhãn làm việc nếu cue còn `needs_review=true`. |
| `character_age` | Tuổi cụ thể nếu hồ sơ có dữ liệu. Có thể là `null` với nhân vật trưởng thành chưa biết tuổi. |
| `character_age_band` | Nhóm tuổi, ví dụ `teen`, `adult`, `unknown`. |
| `character_gender` | Giới tính theo hồ sơ nhân vật; không suy ra chỉ từ giọng nói. |
| `character_role` | Vai trò trong tuyến truyện, ví dụ `student`, `teacher`, `maid`. |
| `candidate_score` | Điểm tương đồng giọng nói của ứng viên đứng đầu; không phải xác suất đúng. |
| `candidate_margin` | Khoảng cách giữa ứng viên đứng đầu và ứng viên kế tiếp; margin càng lớn thì lựa chọn càng tách biệt. |
| `alternatives` | Danh sách ứng viên thay thế, mỗi phần tử có `character_id`, `character_name` và `score`. |
| `match_status` | Cách nhãn được chọn: `reviewed_rule`, `high_voice_candidate`, `route_constrained_candidate` hoặc `machine_candidate`. |
| `needs_review` | `true` nghĩa là phải nghe/xem lại cue trước khi coi tên là chính xác. |

`candidate_score` và `candidate_margin` chỉ là bằng chứng âm thanh. Diarization không tự biết `SPEAKER_00` là Haruka hay Sora; muốn xác nhận danh tính cần đối chiếu giọng mẫu, hình ảnh, ngữ cảnh câu thoại và tuyến nhân vật.

## Chuẩn bị

Chạy trong PowerShell:

```powershell
Set-Location D:\web\CineZero_V2_D_web

$Py = if (Test-Path .\.venv-subtitles\Scripts\python.exe) {
  (Resolve-Path .\.venv-subtitles\Scripts\python.exe).Path
} else {
  'python'
}
```

Cần có Python, FFmpeg/`ffprobe` trong `PATH` và các file video gốc. `HF_TOKEN` chỉ cần khi chạy lại diarization hoặc tạo voice audit; script kiểm tra báo cáo không gọi API và không cần in/ghi token.

## 1. Tạo lại báo cáo có tên nhân vật (khi cần)

Bước này không bắt buộc nếu các file `*.vi.named.segments.json` đã có sẵn. Nếu tạo lại, dùng voice audit local đã chạy trên video:

```powershell
$VoiceAudit = 'C:\Users\Admin\AppData\Local\Temp\cinezero-speaker-check\voice-context4-all.json'

& $Py .\scripts\build-named-episode-reports.py `
  --voice-audit $VoiceAudit `
  --character-profile .\profiles\yosuga-no-sora-01.json `
  --subtitle-dir .\content\generated-subtitles `
  --output-dir .\content\generated-subtitles `
  --video-dir .\content\encoded `
  --episodes 02 03 04 05 06 07 08 09 10 11 12
```

Không đưa `HF_TOKEN`, file cache Hugging Face hoặc video gốc vào Git. Nhãn của tập 02–12 hiện là nhãn làm việc; dòng `reviewed_rule` là cue đã được xác nhận theo rule/video, còn `context_rule` là mapping ngữ cảnh và vẫn phải nghe lại.

## 2. Chạy kiểm tra tự động

Lệnh dưới đây kiểm tra đồng thời:

- đủ 11 báo cáo tập 02–12 và đủ các trường nhân vật;
- video gốc, VTT nguồn và VTT named có tồn tại;
- số cue của báo cáo, VTT nguồn và VTT named;
- số cue bắt đầu từ 1 và tăng liên tục;
- `start < end`, timestamp hợp lệ; cue đi lùi chỉ được chấp nhận khi chồng lên một lớp phụ đề nguồn khác;
- timestamp báo cáo khớp timestamp VTT nguồn;
- timestamp VTT named khớp VTT nguồn và không có thẻ `<v Name>`;
- cue cuối không vượt thời lượng video đo bằng `ffprobe`;
- `character_id` có trong `character_registry` và nằm trong `route_context.route_cast`;
- điểm, margin và danh sách `alternatives` có đúng kiểu dữ liệu.

```powershell
$Csv = '.\content\generated-subtitles\yosuga-no-sora-character-check-02-12.csv'
$Summary = '.\content\generated-subtitles\yosuga-no-sora-character-check-02-12.summary.json'

& $Py .\scripts\check-character-reports.py `
  --reports-dir .\content\generated-subtitles `
  --episodes 02 03 04 05 06 07 08 09 10 11 12 `
  --csv-output $Csv `
  --summary-output $Summary
```

Kết quả đạt cấu trúc là:

```text
EPISODES=11
ISSUES=0
```

`ISSUES=0` chỉ có nghĩa là dữ liệu, cue, timestamp, video và route không có lỗi cấu trúc. Các cue chồng lớp đã được xác nhận từ nguồn sẽ được ghi ở `timestamp_check.intentional_overlapping_cues_preserved`, không bị coi là lỗi. Nó không có nghĩa tất cả tên nhân vật đã được nghe xác nhận.

Có thể kiểm tra source code và cú pháp project thêm bằng:

```powershell
npm run check
```

## 3. Đọc báo cáo tổng hợp

Mở file CSV bằng Excel hoặc lọc nhanh trong PowerShell:

```powershell
Import-Csv .\content\generated-subtitles\yosuga-no-sora-character-check-02-12.csv |
  Where-Object { $_.needs_review -eq 'True' } |
  Select-Object -First 30 episode,cue,start,end,character_id,character_name,candidate_score,candidate_margin,alternatives,match_status,text |
  Format-Table -Wrap
```

Khi nghe kiểm tra một cue:

1. lấy `episode`, `start`, `end` và `text` từ CSV;
2. mở đúng video trong `D:\web\CineZero_V2_D_web\content\encoded` và nhảy đến `start`;
3. nghe giọng, xem người đang nói trên hình và kiểm tra ngữ cảnh trước/sau cue;
4. so sánh `character_id` hiện tại với `alternatives`;
5. nếu sửa, cập nhật báo cáo theo ID trong `character_registry`, rồi chạy lại lệnh kiểm tra.

Để xem thời lượng một video bằng `ffprobe`:

```powershell
$Video = 'D:\web\CineZero_V2_D_web\content\encoded\yosuga-no-sora-02-720p.mp4'
& ffprobe -v error -show_entries format=duration `
  -of default=noprint_wrappers=1:nokey=1 $Video
```

Tên file một số tập có khoảng trắng trước `.mkv`; khi viết script nên dùng wildcard hoặc lấy đường dẫn bằng `Get-ChildItem`, không tự nối tên file cứng.

## 4. Kiểm tra số cue và timestamp

Trong `*.summary.json`, mỗi tập có các trường:

- `cues`: số cue trong báo cáo;
- `source_vtt_cues`: số cue trong VTT tiếng Việt nguồn;
- `named_vtt_cues`: số cue trong VTT named;
- `video_duration_seconds`: thời lượng video do `ffprobe` trả về;
- `max_report_end_seconds`: thời điểm kết thúc cue muộn nhất;
- `timestamp_check`: kết quả khớp VTT và nằm trong thời lượng video.

Điều kiện cần đạt cho mỗi tập:

```text
cues == source_vtt_cues == named_vtt_cues
timestamp_check.duration_checked == true
timestamp_check.report_matches_source_vtt == true
timestamp_check.named_vtt_matches_source_vtt == true
timestamp_check.report_within_video == true
```

Không tự sửa timestamp bằng cách kéo dài cue để che lỗi. Nếu cue lệch so với tiếng nói, phải mở video, xác định lại điểm bắt đầu/kết thúc rồi sửa nguồn hoặc tạo lại VTT.

## 5. Kiểm tra nhân vật bị gán ngoài tuyến

Mỗi báo cáo có danh sách nhân vật được phép trong:

```json
"route_context": {
  "route_cast": ["akira", "haruka", "kazuha", "kozue", "motoka", "nao", "ryouhei", "sora", "teacher", "yahiro"]
}
```

Nếu một cue có `character_id` không nằm trong `route_cast`, checker tạo lỗi `character_outside_route_cast` và cộng vào `outside_route_counts`. Xem nhanh các tập có lỗi:

```powershell
& $Py -c "import json; d=json.load(open(r'.\content\generated-subtitles\yosuga-no-sora-character-check-02-12.summary.json', encoding='utf-8')); [print(e['episode'], e['outside_route_counts']) for e in d['episodes'] if e['outside_route_counts']]"
```

Sau đó lọc CSV theo `episode` và `cue`, nghe lại video và chọn một trong ba hướng:

- sửa `character_id` về nhân vật trong tuyến;
- bổ sung nhân vật vào `route_cast` nếu thật sự xuất hiện trong tập;
- đánh dấu `needs_review=true` nếu chưa đủ bằng chứng.

`route_cast` là danh sách cho phép, không phải bằng chứng rằng mọi nhân vật trong danh sách đều có thoại ở tập đó. Vì vậy vẫn phải kiểm tra các đoạn có điểm giọng thấp, margin thấp và các nhân vật xuất hiện ít.

## 6. Ngưỡng đọc điểm giọng

Pipeline hiện phân loại điểm tham khảo như sau:

- `high`: `score >= 0.70` và `margin >= 0.20`;
- `medium`: `score >= 0.50` và `margin >= 0.08`;
- thấp hơn hai mức trên: `low`.

Đây là ngưỡng để ưu tiên người kiểm tra, không phải quyết định tự động cuối cùng. Khi `margin` nhỏ, cần nghe cả ứng viên đứng đầu và ứng viên thay thế. Nếu `character_age` là `null` nhưng `character_age_band` là `adult`, đó là dữ liệu hồ sơ chưa có tuổi cụ thể, không phải lỗi timestamp hay lỗi schema.

## 7. Điều kiện được phép đưa lên web/R2

Chỉ publish khi:

```text
ISSUES=0
needs_review=0 đối với các cue muốn công bố tên chính xác
cue/timestamp khớp video
không còn character_outside_route_cast
VTT named không chứa <v ...> hoặc [Tên nhân vật]
```

VTT hiển thị trên web phải giữ dạng phụ đề thoại tiếng Việt sạch, không gắn tên người nói vào nội dung. Báo cáo JSON/CSV dùng cho kiểm tra nội bộ; video và VTT không được đẩy lên GitHub nếu không cần thiết. Sau khi đạt các điều kiện trên mới upload VTT lên R2 và cập nhật track trong D1/website.

## 8. Trích ảnh tại timestamp và mapping nhân vật

Sau bước checker, dùng script local để lấy ảnh đầu cue, giữa cue và cuối cue an toàn. Mặc định script chỉ lấy các cue đang có lỗi timestamp/VTT; cách này tránh tạo hàng nghìn ảnh không cần thiết:

```powershell
$EvidenceDir = '.\content\generated-subtitles\video-evidence'

& $Py .\scripts\build-video-evidence.py `
  --reports-dir .\content\generated-subtitles `
  --output-dir $EvidenceDir `
  --summary .\content\generated-subtitles\yosuga-no-sora-character-check-02-12.summary.json `
  --character-profile .\profiles\yosuga-no-sora-01.json `
  --mode issues `
  --max-cues 100
```

Script trả exit code `1` nếu còn lỗi timestamp/VTT hoặc không tạo được ảnh; đó là tín hiệu phải kiểm tra trước khi publish.

Các chế độ:

- `--mode issues`: chỉ cue có lỗi timestamp, VTT hoặc route;
- `--mode review`: tất cả cue `needs_review=true`;
- `--mode all --max-cues 0`: mọi cue của mọi report; chỉ dùng khi thật sự cần;
- `--cues 07:91 12:158`: chọn chính xác cue cần mở lại.

Kết quả nằm trong `content\generated-subtitles\video-evidence`:

- `review.html`: bảng xem ảnh, timestamp, câu thoại, ứng viên và link tìm thông tin nhân vật;
- `evidence.csv`: danh sách cue để lọc bằng Excel;
- `evidence-index.json`: kết quả kiểm tra, đường dẫn video, timestamp, ảnh và lỗi;
- `*-character-mapping.json`: mapping nhân vật có `character_id`, tên, tuổi/nhóm tuổi, giới tính, vai trò, nguồn và ảnh tham chiếu;
- `episode-NN\cue-NNNN\start.jpg`, `mid.jpg`, `end.jpg`: ảnh chụp tại timestamp.

Mở bảng bằng:

```powershell
Start-Process (Resolve-Path .\content\generated-subtitles\video-evidence\review.html)
```

Ảnh chỉ là bằng chứng hình ảnh. Với mỗi cue, vẫn phải mở video và nghe đúng khoảng `start`–`end`:

```powershell
$Video = 'D:\web\CineZero_V2_D_web\content\encoded\yosuga-no-sora-07-720p.mp4'
& ffplay -ss 465.15 -t 2.0 -autoexit $Video
```

Trong `*-character-mapping.json`, chỉ điền `source_urls` sau khi tìm được nguồn phù hợp, điền các giá trị đã xác minh và đổi `mapping_status` thành `verified`. Link tìm kiếm trong `review.html` chỉ là điểm bắt đầu; phải đọc và đối chiếu nguồn trước khi đưa tuổi, giới tính, vai trò hoặc tên vào mapping chính thức. Quy trình này dùng trình duyệt và nguồn web miễn phí, không cần API trả phí.

## 9. Chẩn đoán diễn biến truyện cho cue chưa rõ người nói

Khi voice score thấp hoặc có nhiều nhân vật cạnh tranh, chạy thêm tầng chẩn đoán ngữ cảnh:

```powershell
$StoryDir = '.\content\generated-subtitles\story-diagnosis'

& $Py .\scripts\diagnose-story-context.py `
  --reports-dir .\content\generated-subtitles `
  --output-dir $StoryDir `
  --character-profile .\profiles\yosuga-no-sora-01.json `
  --pronoun-rules .\config\pronoun-rules.vi.json `
  --evidence-index .\content\generated-subtitles\video-evidence\evidence-index.json `
  --mode unresolved `
  --window 4
```

Script dùng các tín hiệu local miễn phí: câu trước/sau, khoảng cách timestamp, chuyển lượt thoại, nhân vật đã được rule xác nhận trong cùng cảnh, tên nhân vật được gọi trong câu, tuyến được phép, quan hệ trong profile, nhóm tuổi/vai trò và quy tắc xưng hô. Kết quả nằm trong:

- `story-context-diagnosis.json`: đầy đủ chẩn đoán theo cue và tóm tắt từng scene;
- `story-context-diagnosis.csv`: bảng lọc bằng Excel;
- `story-context-review.html`: trang xem câu thoại, ứng viên, ngữ cảnh, ảnh evidence và link nghiên cứu.

Các trường quan trọng gồm `recommended_character_id`, `recommendation_status`, `context_margin`, `addressed_character`, `plot_signals`, `translation_context`, `nearby_dialogue` và `candidate_alternatives`. `context_supported` chỉ là đề xuất có bằng chứng ngữ cảnh; `ambiguous` phải giữ trung tính. Mọi dòng đều có `do_not_auto_apply=true`: không tự đổi `character_id` hoặc ghi đè VTT. Sau khi nghe video và đối chiếu ảnh/nguồn, người kiểm tra mới cập nhật mapping/report rồi chạy lại checker.

## Trạng thái bộ dữ liệu hiện tại

Lần chạy checker hiện tại của tập 02–12 có 11 tập và 3.175 cue, trong đó còn 3.110 cue `needs_review`. Checker đang báo 2 lỗi timestamp không tăng dần trong nguồn: tập 07 cue 91 bắt đầu ở `465.15` sau cue 90 kết thúc ở `468.44`, và tập 12 cue 158 bắt đầu ở `1035.89` sau cue trước bắt đầu ở `1036.44`. Cần mở video kiểm tra hai đoạn này rồi sắp xếp/sửa timestamp trước khi coi bộ dữ liệu đạt.

Evidence pack hiện đã trích 6 ảnh cho 2 cue lỗi, không lỗi tạo ảnh. Ảnh giúp kiểm tra bối cảnh nhân vật nhưng không thay thế việc nghe âm thanh; người trong khung hình có thể là người nghe chứ không phải người đang nói.

Lần chẩn đoán ngữ cảnh hiện tại xử lý 3.110 cue: 1.272 cue có ngữ cảnh hỗ trợ, 777 cue có ứng viên yếu và 1.061 cue vẫn mơ hồ. Có 101 cue được đề xuất đổi nhãn; tất cả vẫn giữ `do_not_auto_apply=true` để người kiểm tra nghe lại trước.

Vì vậy hiện tại chưa được coi là danh tính nhân vật đã xác nhận và chưa nên publish tên nhân vật như dữ liệu chính thức. README này cố ý giữ tiêu chí đó để tránh nhầm giữa “checker không có lỗi cấu trúc” và “nhận diện nhân vật chính xác”.
