#!/usr/bin/env node

import { promises as fs } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';

const DEFAULT_URL = 'https://surrit.com/b954ce82-44cc-4fe6-ab38-032f4bbce067/842x480/video.m3u8';
const DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36';

function printHelp() {
  console.log(`CineZero HLS downloader

Tải một HLS playlist (.m3u8), tùy chọn tải segment và tùy chọn ghép thành MP4.
Mặc định không ghép MP4; dùng --manifest-only nếu chỉ cần playlist nhẹ.

Usage:
  node scripts/download-m3u8.mjs [options]

Options:
  --url <url>             URL manifest (mặc định là URL Surrit đã cấu hình)
  --manifest-file <path>  Đọc manifest đã lưu từ trình duyệt, không gọi URL manifest
  --output-dir <dir>     Thư mục đích (mặc định: content/encoded)
  --name <name>          Tên đầu ra (mặc định: video)
  --merge                Tuỳ chọn: ghép segment thành <name>.mp4 bằng FFmpeg
  --manifest-only        Chỉ lưu .m3u8, giữ URL segment từ CDN, không tải segment
  --mp4 <path>            Đường dẫn MP4 đầu ra; tự bật --merge
  --ffmpeg <path>        Đường dẫn executable FFmpeg (mặc định: ffmpeg)
  --concurrency <n>       Số segment tải song song (mặc định: 8)
  --retries <n>           Số lần thử lại mỗi request (mặc định: 4)
  --timeout <ms>          Timeout mỗi request (mặc định: 30000)
  --referer <url>         Gửi Referer khi CDN yêu cầu
  --origin <url>          Gửi Origin khi CDN yêu cầu
  --header "K: V"         Header bổ sung; có thể lặp lại
  --force                 Tải lại các segment đã có
  --help                  Hiển thị trợ giúp

Ví dụ:
  node scripts/download-m3u8.mjs --manifest-only \
    --output-dir "content/encoded" --name "video"

  node scripts/download-m3u8.mjs --url "https://example.com/video.m3u8" \
    --referer "https://example.com/" --origin "https://example.com" --merge

Tool không vượt qua CAPTCHA, Cloudflare hoặc quyền truy cập của CDN. Nếu máy
chạy tool nhận 403/407, cần chạy trên mạng/phiên có quyền truy cập hợp lệ.
`);
}

function parsePositiveInt(value, name) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`${name} phải là số nguyên dương.`);
  }
  return parsed;
}

function parseHeader(value) {
  const separator = value.indexOf(':');
  if (separator <= 0) throw new Error(`Header không hợp lệ: ${value}`);
  const name = value.slice(0, separator).trim();
  const headerValue = value.slice(separator + 1).trim();
  if (!name || !headerValue) throw new Error(`Header không hợp lệ: ${value}`);
  return [name, headerValue];
}

function parseArgs(argv) {
  const options = {
    url: DEFAULT_URL,
    manifestFile: undefined,
    outputDir: 'content/encoded',
    name: 'video',
    concurrency: 8,
    retries: 4,
    timeout: 30_000,
    ffmpeg: 'ffmpeg',
    referer: undefined,
    origin: undefined,
    headers: {},
    merge: false,
    manifestOnly: false,
    mp4: undefined,
    force: false,
  };

  const valueOptions = new Map([
    ['--url', 'url'],
    ['--manifest-file', 'manifestFile'],
    ['--output-dir', 'outputDir'],
    ['--name', 'name'],
    ['--ffmpeg', 'ffmpeg'],
    ['--referer', 'referer'],
    ['--origin', 'origin'],
    ['--mp4', 'mp4'],
  ]);

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === '--help' || argument === '-h') {
      options.help = true;
      continue;
    }
    if (argument === '--merge') {
      options.merge = true;
      continue;
    }
    if (argument === '--manifest-only') {
      options.manifestOnly = true;
      continue;
    }
    if (argument === '--force') {
      options.force = true;
      continue;
    }
    if (argument === '--header') {
      const raw = argv[++index];
      if (!raw) throw new Error('--header cần một giá trị "Tên: Giá trị".');
      const [name, value] = parseHeader(raw);
      options.headers[name] = value;
      continue;
    }
    if (argument === '--concurrency' || argument === '--retries' || argument === '--timeout') {
      const raw = argv[++index];
      if (!raw) throw new Error(`${argument} cần một giá trị.`);
      const key = argument.slice(2);
      options[key] = parsePositiveInt(raw, argument);
      continue;
    }
    if (valueOptions.has(argument)) {
      const raw = argv[++index];
      if (!raw) throw new Error(`${argument} cần một giá trị.`);
      options[valueOptions.get(argument)] = raw;
      if (argument === '--mp4') options.merge = true;
      continue;
    }
    throw new Error(`Tham số không được hỗ trợ: ${argument}`);
  }

  if (!options.name || options.name.includes('..') || /[\\/]/.test(options.name)) {
    throw new Error('--name chỉ được chứa tên file, không chứa đường dẫn.');
  }
  if (options.manifestOnly && options.merge) {
    throw new Error('--manifest-only không thể dùng cùng --merge.');
  }
  return options;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function safeBasename(remoteUrl, fallback) {
  try {
    const pathname = decodeURIComponent(new URL(remoteUrl).pathname);
    const basename = path.posix.basename(pathname);
    if (basename && basename !== '.' && basename !== '/') return basename;
  } catch {
    // Fall through to the deterministic fallback name.
  }
  return fallback;
}

function sanitizeFilename(filename) {
  const sanitized = filename
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_')
    .replace(/\s+/g, ' ')
    .trim();
  return sanitized || 'segment.bin';
}

function parseAttributeList(value) {
  const attributes = {};
  const pattern = /([A-Z0-9-]+)=((?:"[^"]*")|[^,]*)/g;
  let match;
  while ((match = pattern.exec(value)) !== null) {
    const raw = match[2].trim();
    attributes[match[1]] = raw.startsWith('"') && raw.endsWith('"')
      ? raw.slice(1, -1)
      : raw;
  }
  return attributes;
}

function replaceUriAttribute(line, localReference) {
  return line.replace(/URI="[^"]*"/, `URI="${localReference}"`);
}

function parseMasterVariants(text, baseUrl) {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const variants = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line.startsWith('#EXT-X-STREAM-INF:')) continue;
    const attributes = parseAttributeList(line.slice('#EXT-X-STREAM-INF:'.length));
    let uriLine;
    for (let next = index + 1; next < lines.length; next += 1) {
      const candidate = lines[next].trim();
      if (!candidate || candidate.startsWith('#')) continue;
      uriLine = candidate;
      index = next;
      break;
    }
    if (!uriLine) continue;
    variants.push({
      url: new URL(uriLine, baseUrl).href,
      bandwidth: Number.parseInt(attributes.BANDWIDTH || '0', 10) || 0,
      width: Number.parseInt((attributes.RESOLUTION || '0x0').split('x')[0], 10) || 0,
      height: Number.parseInt((attributes.RESOLUTION || '0x0').split('x')[1], 10) || 0,
    });
  }
  return variants;
}

function chooseVariant(variants) {
  return [...variants].sort((left, right) => {
    const leftPixels = left.width * left.height;
    const rightPixels = right.width * right.height;
    return (rightPixels - leftPixels) || (right.bandwidth - left.bandwidth);
  })[0];
}

function parseMediaPlaylist(text, baseUrl, outputDir, segmentsDirName, referenceMode = 'local') {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  if (!lines.some((line) => line.trim() === '#EXTM3U')) {
    throw new Error('Nội dung tải về không phải HLS manifest hợp lệ (#EXTM3U bị thiếu).');
  }

  const resources = [];
  const localLines = [];
  let pendingDuration = null;

  const addResource = (uri, type, duration = null) => {
    const remoteUrl = new URL(uri, baseUrl).href;
    const resourceIndex = resources.length;
    const originalName = sanitizeFilename(safeBasename(remoteUrl, `${type}-${resourceIndex}.bin`));
    const filename = `${String(resourceIndex).padStart(6, '0')}-${originalName}`;
    const localReference = referenceMode === 'remote'
      ? remoteUrl
      : `${segmentsDirName}/${filename}`;
    const localPath = path.join(outputDir, segmentsDirName, filename);
    const resource = {
      index: resourceIndex,
      type,
      duration,
      remoteUrl,
      localReference,
      localPath,
    };
    resources.push(resource);
    return resource;
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      localLines.push('');
      continue;
    }
    if (line.startsWith('#EXTINF:')) {
      const rawDuration = line.slice('#EXTINF:'.length).split(',', 1)[0];
      const duration = Number.parseFloat(rawDuration);
      pendingDuration = Number.isFinite(duration) ? duration : null;
      localLines.push(rawLine);
      continue;
    }
    if (line.startsWith('#EXT-X-MAP:') || line.startsWith('#EXT-X-KEY:')) {
      const attributes = parseAttributeList(line.slice(line.indexOf(':') + 1));
      if (attributes.URI) {
        const resource = addResource(attributes.URI, line.startsWith('#EXT-X-MAP:') ? 'map' : 'key');
        localLines.push(replaceUriAttribute(rawLine, resource.localReference));
      } else {
        localLines.push(rawLine);
      }
      continue;
    }
    if (line.startsWith('#')) {
      localLines.push(rawLine);
      continue;
    }

    const resource = addResource(line, 'segment', pendingDuration);
    localLines.push(resource.localReference);
    pendingDuration = null;
  }

  return {
    localText: `${localLines.join('\n').replace(/\n+$/, '')}\n`,
    resources,
  };
}

function createHeaders(options) {
  const headers = new Headers({
    accept: '*/*',
    'user-agent': DEFAULT_USER_AGENT,
  });
  if (options.referer) headers.set('referer', options.referer);
  if (options.origin) headers.set('origin', options.origin);
  for (const [name, value] of Object.entries(options.headers)) headers.set(name, value);
  return headers;
}

function createHttpClient(options) {
  const headers = createHeaders(options);

  async function read(url, mode) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), options.timeout);
    try {
      const response = await fetch(url, {
        headers,
        redirect: 'follow',
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status} ${response.statusText} khi tải ${url}`);
      }
      return mode === 'text'
        ? await response.text()
        : Buffer.from(await response.arrayBuffer());
    } catch (error) {
      if (error?.name === 'AbortError') {
        throw new Error(`Timeout sau ${options.timeout}ms khi tải ${url}`);
      }
      throw error;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  return {
    fetchText: (url) => read(url, 'text'),
    fetchBytes: (url) => read(url, 'bytes'),
  };
}

async function writeAtomic(filePath, data) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
  const temporaryPath = `${filePath}.part`;
  await fs.writeFile(temporaryPath, data);
  await fs.rm(filePath, { force: true });
  await fs.rename(temporaryPath, filePath);
}

async function downloadResource(resource, client, options) {
  if (!options.force) {
    try {
      const existing = await fs.stat(resource.localPath);
      if (existing.isFile() && existing.size > 0) return { skipped: true };
    } catch {
      // The resource is not present; download it below.
    }
  }

  let lastError;
  for (let attempt = 1; attempt <= options.retries; attempt += 1) {
    try {
      const bytes = await client.fetchBytes(resource.remoteUrl);
      await writeAtomic(resource.localPath, bytes);
      return { skipped: false, bytes: bytes.length };
    } catch (error) {
      lastError = error;
      if (attempt < options.retries) await sleep(Math.min(500 * (2 ** (attempt - 1)), 8_000));
    }
  }
  throw new Error(`${resource.remoteUrl}: ${lastError?.message || 'tải thất bại'}`);
}

async function downloadResources(resources, client, options) {
  let cursor = 0;
  let completed = 0;
  const worker = async () => {
    while (true) {
      const currentIndex = cursor;
      cursor += 1;
      if (currentIndex >= resources.length) return;
      const resource = resources[currentIndex];
      const result = await downloadResource(resource, client, options);
      completed += 1;
      if (completed === 1 || completed === resources.length || completed % 25 === 0) {
        const state = result.skipped ? 'đã có' : 'xong';
        console.log(`[${completed}/${resources.length}] ${state}: ${path.basename(resource.localPath)}`);
      }
    }
  };

  const workerCount = Math.min(options.concurrency, resources.length);
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
}

function isImageSequence(resources) {
  const segments = resources.filter((resource) => resource.type === 'segment');
  return segments.length > 0 && segments.every((resource) => {
    const extension = path.extname(resource.localPath).toLowerCase();
    return ['.jpg', '.jpeg', '.png', '.webp', '.bmp'].includes(extension);
  });
}

function concatPathForFfmpeg(filePath) {
  return filePath.replace(/\\/g, '/').replace(/'/g, "'\\''");
}

async function createImageConcatFile(resources, outputPath) {
  const segments = resources.filter((resource) => resource.type === 'segment');
  const lines = [];
  segments.forEach((resource, index) => {
    lines.push(`file '${concatPathForFfmpeg(resource.localPath)}'`);
    if (Number.isFinite(resource.duration) && index < segments.length - 1) {
      lines.push(`duration ${resource.duration}`);
    }
  });
  if (segments.length > 0) lines.push(`file '${concatPathForFfmpeg(segments.at(-1).localPath)}'`);
  await fs.writeFile(outputPath, `${lines.join('\n')}\n`, 'utf8');
}

function runFfmpeg(ffmpeg, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(ffmpeg, args, { stdio: 'inherit', windowsHide: true });
    child.once('error', (error) => reject(new Error(`Không chạy được FFmpeg: ${error.message}`)));
    child.once('exit', (code, signal) => {
      if (code === 0) resolve();
      else reject(new Error(`FFmpeg thất bại (code=${code ?? 'null'}, signal=${signal ?? 'none'}).`));
    });
  });
}

async function mergeToMp4(manifestPath, resources, outputPath, options) {
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  if (isImageSequence(resources)) {
    const concatPath = path.join(path.dirname(manifestPath), `${options.name}.concat.txt`);
    await createImageConcatFile(resources, concatPath);
    await runFfmpeg(options.ffmpeg, [
      '-y', '-f', 'concat', '-safe', '0', '-i', concatPath,
      '-fps_mode', 'vfr', '-pix_fmt', 'yuv420p', '-c:v', 'libx264',
      '-movflags', '+faststart', outputPath,
    ]);
    return;
  }

  await runFfmpeg(options.ffmpeg, [
    '-y', '-protocol_whitelist', 'file,crypto,data', '-allowed_extensions', 'ALL',
    '-i', manifestPath, '-c', 'copy', '-movflags', '+faststart', outputPath,
  ]);
}

async function loadMediaPlaylist(url, sourceText, client) {
  let currentUrl = url;
  let currentText = sourceText;
  for (let depth = 0; depth < 4; depth += 1) {
    const variants = parseMasterVariants(currentText, currentUrl);
    if (variants.length === 0) return { url: currentUrl, text: currentText };
    const selected = chooseVariant(variants);
    console.log(`Master playlist → chọn variant ${selected.width}x${selected.height || '?'} (${selected.bandwidth || 'n/a'} bps)`);
    currentUrl = selected.url;
    currentText = await client.fetchText(currentUrl);
  }
  throw new Error('Master playlist lồng quá sâu, dừng để tránh vòng lặp.');
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (options.help) {
    printHelp();
    return;
  }

  const outputDir = path.resolve(process.cwd(), options.outputDir);
  const manifestPath = path.join(outputDir, `${options.name}.m3u8`);
  const sourceManifestPath = path.join(outputDir, `${options.name}.source.m3u8`);
  const segmentsDirName = `${options.name}-segments`;
  const defaultMp4Path = path.join(outputDir, `${options.name}.mp4`);
  const mp4Path = options.mp4 ? path.resolve(process.cwd(), options.mp4) : defaultMp4Path;
  const client = createHttpClient(options);

  await fs.mkdir(outputDir, { recursive: true });
  const sourceText = options.manifestFile
    ? await fs.readFile(path.resolve(process.cwd(), options.manifestFile), 'utf8')
    : await client.fetchText(options.url);
  console.log(`Manifest: ${options.manifestFile ? path.resolve(process.cwd(), options.manifestFile) : options.url}`);
  await fs.writeFile(sourceManifestPath, sourceText, 'utf8');

  const mediaPlaylist = await loadMediaPlaylist(options.url, sourceText, client);
  const referenceMode = options.manifestOnly ? 'remote' : 'local';
  const parsed = parseMediaPlaylist(mediaPlaylist.text, mediaPlaylist.url, outputDir, segmentsDirName, referenceMode);
  if (!options.manifestOnly) await downloadResources(parsed.resources, client, options);
  await fs.writeFile(manifestPath, parsed.localText, 'utf8');

  console.log(`Manifest local: ${manifestPath}`);
  console.log(`Segment count: ${parsed.resources.length}`);
  console.log(`Source manifest: ${sourceManifestPath}`);
  if (options.manifestOnly) console.log('Mode: manifest-only (không tải segment, không tạo MP4)');
  if (options.merge) {
    await mergeToMp4(manifestPath, parsed.resources, mp4Path, options);
    console.log(`MP4: ${mp4Path}`);
  }
}

export { parseArgs, parseMasterVariants, parseMediaPlaylist };

const entrypoint = process.argv[1] ? pathToFileURL(path.resolve(process.argv[1])).href : '';
if (entrypoint === import.meta.url) {
  main().catch((error) => {
    console.error(`LỖI: ${error.message}`);
    process.exitCode = 1;
  });
}
