import fs from 'node:fs';
import process from 'node:process';

const [, , siteUrlArg, fileArg, keyArg] = process.argv;
const siteUrl = String(siteUrlArg || '').replace(/\/$/, '');
const file = String(fileArg || '');
const key = String(keyArg || '');
const token = process.env.R2_MULTIPART_TOKEN || process.env.ADMIN_TOKEN || '';
const partSizeMb = Number(process.env.R2_PART_SIZE_MB || 80);
const partSize = Math.floor(partSizeMb * 1024 * 1024);

if (!siteUrl || !file || !key || !token) {
  throw new Error('Usage: node scripts/upload-r2-multipart.mjs <site-url> <file> <r2-key> (R2_MULTIPART_TOKEN or ADMIN_TOKEN required)');
}
if (!fs.existsSync(file)) throw new Error(`File not found: ${file}`);
if (!Number.isFinite(partSizeMb) || partSizeMb < 5 || partSizeMb > 90) throw new Error('R2_PART_SIZE_MB must be between 5 and 90.');

const stat = fs.statSync(file);
const endpoint = `${siteUrl}/api/admin/r2-multipart`;
const query = (params) => new URLSearchParams(params).toString();
const authHeaders = { authorization: `Bearer ${token}` };

async function readJson(response) {
  const text = await response.text();
  let data;
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { raw: text.slice(0, 500) };
  }
  if (!response.ok) throw new Error(`HTTP ${response.status}: ${JSON.stringify(data)}`);
  return data;
}

async function jsonRequest(url, options = {}) {
  return readJson(await fetch(url, {
    ...options,
    headers: { ...authHeaders, ...(options.headers || {}) }
  }));
}

let uploadId = '';
try {
  const created = await jsonRequest(`${endpoint}?${query({ action: 'create', key })}`, { method: 'POST' });
  uploadId = String(created.upload_id || '');
  if (!uploadId) throw new Error('R2 multipart create did not return upload_id.');
  console.log(`R2_MULTIPART_START=${(stat.size / 1024 ** 3).toFixed(3)} GiB PART_SIZE_MB=${partSizeMb}`);

  const parts = [];
  let partNumber = 1;
  for (let start = 0; start < stat.size; start += partSize, partNumber += 1) {
    const end = Math.min(stat.size, start + partSize);
    const length = end - start;
    const body = fs.createReadStream(file, { start, end: end - 1 });
    const response = await fetch(`${endpoint}?${query({ key, upload_id: uploadId, part_number: partNumber })}`, {
      method: 'PUT',
      headers: { ...authHeaders, 'content-type': 'application/octet-stream', 'content-length': String(length) },
      body,
      duplex: 'half'
    });
    const result = await readJson(response);
    parts.push({ part_number: partNumber, etag: result.etag });
    console.log(`R2_PART=${partNumber} BYTES=${length} PROGRESS=${Math.round((end / stat.size) * 100)}%`);
  }

  const completed = await jsonRequest(`${endpoint}?${query({ action: 'complete', key, upload_id: uploadId })}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ parts })
  });
  console.log(`R2_MULTIPART_PASS=${completed.key} SIZE=${completed.size} ETAG=${completed.etag}`);
} catch (error) {
  if (uploadId) {
    try {
      await jsonRequest(`${endpoint}?${query({ key, upload_id: uploadId })}`, { method: 'DELETE' });
      console.error('R2_MULTIPART_ABORTED=true');
    } catch (abortError) {
      console.error(`R2_MULTIPART_ABORT_FAILED=${abortError.message}`);
    }
  }
  throw error;
}
