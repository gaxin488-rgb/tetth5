# Nen video truoc khi luu R2

Cloudflare Worker khong phai noi phu hop de encode video lon. Tool nay chay
FFmpeg tren may local, kiem tra file dau ra, sau do moi upload len R2. Vi vay
file goc khong bi xoa va storage guard van duoc ap dung truoc khi ghi R2.

## Cai dat

Dam bao ffmpeg va ffprobe co trong PATH:

    ffmpeg -version
    ffprobe -version

## Nen local

Lenh mac dinh dung H.264, CRF 18, preset slow, giu nguyen do phan giai va
frame rate. CRF cang thap thi chat luong cang cao va file cang lon.

    cd D:\web\CineZero_V2_D_web
    .\scripts\compress-video.ps1 -InputFile 'D:\video\episode-01.mkv' -OutputFile '.\content\encoded\episode-01.mp4'

Muon tiet kiem R2 nhieu hon, dung H.265. H.265 thuong nho hon H.264 o chat
luong cam nhan tuong duong, nhung khong duoc ho tro dong deu tren moi browser
hoac thiet bi:

    .\scripts\compress-video.ps1 -InputFile 'D:\video\episode-01.mkv' -OutputFile '.\content\encoded\episode-01-hevc.mp4' -Codec h265 -Crf 22 -Preset slow

Neu muon ha video 1080p/4K xuong toi da 720p de giam them dung luong, chi dung
-MaxHeight 720 khi chap nhan thay doi do phan giai. Tool khong upscale video
nho hon gia tri nay.

    .\scripts\compress-video.ps1 -InputFile 'D:\video\episode-01.mkv' -MaxHeight 720

-Audio aac mac dinh encode audio AAC 160 kbps de player web tuong thich.
Dung -Audio copy neu muon giu nguyen audio stream va biet chac audio codec do
chay tot trong MP4/browser:

    .\scripts\compress-video.ps1 -InputFile 'D:\video\episode-01.mkv' -Audio copy

## Nen roi upload mot lenh

compress-upload-r2.ps1 giu file nen local, goi storage guard, roi upload de
len dung Key tren R2. Khi thay the cung key, guard da tru dung luong object
cu truoc khi tinh dung luong moi.

    $env:CINEZERO_SITE_URL = 'https://your-site.example'
    $env:ADMIN_TOKEN = 'token-cua-admin'
    .\scripts\compress-upload-r2.ps1 -InputFile 'D:\video\episode-01.mkv' -Key 'movies/slug/episode-01.mp4' -Codec h264 -Crf 18 -Preset slow

Neu dung H.265 cho kho luu tru rieng:

    .\scripts\compress-upload-r2.ps1 -InputFile 'D:\video\episode-01.mkv' -Key 'movies/slug/episode-01-hevc.mp4' -Codec h265 -Crf 22 -Preset slow

## Upload file lon qua multipart

Wrangler CLI gioi han lenh put truc tiep o 300 MiB. Worker co endpoint admin
multipart de upload file lon theo cac part nho, sau do ghep lai thanh mot object
R2. Endpoint nay dung cung ADMIN_TOKEN cua dashboard:

    $env:CINEZERO_SITE_URL = 'https://your-site.example'
    $env:ADMIN_TOKEN = 'token-cua-admin'
    $env:R2_MULTIPART_TOKEN = $env:ADMIN_TOKEN
    node scripts/upload-r2-multipart.mjs $env:CINEZERO_SITE_URL 'D:\video\episode-01.mp4' 'movies/slug/episode-01.mp4'

Co the giam/tang part bang `$env:R2_PART_SIZE_MB` trong khoang 5-90; mac dinh
la 80 MiB. Tool khong xoa file goc va neu loi se abort multipart dang do.

Chi dung -SkipStorageGuard khi endpoint guard khong co san va ban da tu kiem
tra quota R2. -Force chi cho phep ghi de file nen local; file goc van duoc giu
nguyen.

## “Giu nguyen chat luong” nghia la gi?

Nen lossy khong the vua giam dang ke dung luong vua giu nguyen tung pixel.
Thiet lap mac dinh giu nguyen do phan giai/frame rate va dung CRF chat luong
cao, nen phu hop voi muc tieu giu chat luong cam nhan duoc. Neu can giong
bit-for-bit, co the dung -Crf 0 voi codec phu hop, thuong se khong giam dung
luong va khong nen dung cho muc tieu tiet kiem R2. Gia tri CRF mac dinh tu dong
cua tool la -1, khong phai 0.

Sau moi lan chay, tool in ra SOURCE_SIZE, OUTPUT_SIZE, REDUCTION, codec va do
phan giai dau ra de ban biet chinh xac muc tiet kiem truoc khi publish.
