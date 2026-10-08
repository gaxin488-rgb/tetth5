# HLS m3u8 downloader

Tool: `scripts/download-m3u8.mjs`

Run from `D:\web\CineZero_V2_D_web`.

## Keep the lightweight `.m3u8` playlist

```powershell
npm run media:download -- --manifest-only `
  --output-dir "content\encoded" `
  --name "video"
```

This writes `video.m3u8` and keeps the CDN segment URLs. It does not download
segments and does not create an MP4.

## Use a manifest saved from the browser

When Surrit returns `403` to Node but the browser can show a cached response,
save the DevTools **Response** as a text file and run:

```powershell
npm run media:download -- `
  --manifest-file "C:\path\to\842x480.m3u8" `
  --url "https://surrit.com/b954ce82-44cc-4fe6-ab38-032f4bbce067/842x480/video.m3u8" `
  --manifest-only `
  --output-dir "content\encoded" `
  --name "video"
```

`--url` is used as the base URL for relative segment names in the saved
manifest. The tool does not read browser cookies or browser cache.

## Download segments for local/offline playback

```powershell
npm run media:download -- `
  --output-dir "content\encoded" `
  --name "video"
```

This writes `video.m3u8`, `video.source.m3u8`, and the `video-segments\` folder.
No MP4 is created unless `--merge` is explicitly added.

## Optional MP4 conversion

```powershell
npm run media:download -- --merge
```

Use `--url`, `--referer`, `--origin`, `--header`, `--concurrency`, `--retries`,
`--timeout`, and `--force` to customize the request. The tool does not bypass
CAPTCHA or Cloudflare; the request must be run from a network/session with
valid access.
