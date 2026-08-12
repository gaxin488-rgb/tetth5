#!/usr/bin/env python3
"""Build a lightweight one-cue-at-a-time audio/video review dashboard."""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path


def read_evidence(index_path: Path) -> list[dict]:
    data = json.loads(index_path.read_text(encoding="utf-8"))
    rows = []
    for raw in data.get("evidence") or []:
        item = dict(raw)
        item["_frame_root"] = str(index_path.parent)
        rows.append(item)
    return rows


def relative_url(path: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(path.resolve(), base.resolve())).as_posix()
    except ValueError:
        return path.resolve().as_uri()


def prepare_item(item: dict, output_parent: Path) -> dict:
    prepared = {key: value for key, value in item.items() if not key.startswith("_")}
    prepared["video_url"] = relative_url(Path(str(item.get("video") or "")), output_parent)
    frame = next((frame for frame in item.get("frames") or [] if frame.get("label") == "mid"), None)
    if frame:
        frame_path = Path(str(item.get("_frame_root") or output_parent)) / str(frame.get("path") or "")
        prepared["frame_url"] = relative_url(frame_path, output_parent)
        prepared["frame_seconds"] = frame.get("seconds")
    else:
        prepared["frame_url"] = ""
        prepared["frame_seconds"] = None
    return prepared


HTML_TEMPLATE = r'''<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CineZero - review __CUE_COUNT__ cues</title>
<style>
body{font:15px system-ui;background:#101218;color:#edf1f7;max-width:1220px;margin:0 auto;padding:18px}
h1{margin-bottom:4px}.toolbar{position:sticky;top:0;z-index:10;background:#171a24;border:1px solid #34394a;padding:12px;border-radius:12px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
button,select,input,textarea{font:inherit;background:#0c0f16;color:#edf1f7;border:1px solid #3d4352;border-radius:8px;padding:8px}
button{cursor:pointer;background:#2e4164}button:disabled{opacity:.45;cursor:not-allowed}
.cue{border:1px solid #34394a;border-radius:14px;padding:18px;margin:16px 0;background:#171a24}
.cue header{display:flex;gap:12px;justify-content:space-between;align-items:center}h2{margin:0}.status{color:#ffc36b;font-size:12px}
.time{color:#91baff}.text{font-size:20px;white-space:pre-wrap}.cue-video{display:block;width:min(900px,100%);background:#000;margin:14px 0}
.candidate{line-height:1.8}code{color:#b9d1ff}a{color:#8fc7ff}.frame img{max-width:650px;width:100%;height:auto;border-radius:8px}
details{margin:12px 0}summary{cursor:pointer;color:#b9d1ff}.decision-character,.decision-note,.alternative-choice{display:block;width:min(700px,100%);margin-top:8px}
.decision-note{min-height:65px}.checks{display:grid;gap:5px;margin:14px 0;color:#c7d0df}.saved{outline:2px solid #58c889}.small{color:#aeb8c8;font-size:13px}.error{color:#ff8b8b}
</style></head><body>
<h1>Đối chiếu nhân vật - __CUE_COUNT__ cue</h1>
<p class="small">Chỉ cue đang chọn mới tải video và ảnh. Phát video sẽ tự nhảy đến <code>start</code> và dừng ở <code>end</code>. Muốn xác nhận phải kiểm tra đủ timestamp, âm thanh/hình ảnh, ảnh midpoint và ứng viên.</p>
<div class="toolbar">
  <label>Tập <select id="episode-filter"><option value="all">Tất cả</option></select></label>
  <label>Cue <select id="cue-select"></select></label>
  <button id="previous-button" type="button">← Trước</button>
  <button id="next-button" type="button">Sau →</button>
  <button id="unreviewed-button" type="button">Chưa kiểm tra tiếp</button>
  <button id="export-button" type="button">Xuất JSON</button>
  <span id="progress"></span>
</div>
<main id="cue-view"></main>
<script>
const cues = __CUE_PAYLOAD__;
const decisionKey = 'cinezero-cue-review-decisions-v3';
const decisions = JSON.parse(localStorage.getItem(decisionKey) || '{}');
const episodeFilter = document.querySelector('#episode-filter');
const cueSelect = document.querySelector('#cue-select');
const view = document.querySelector('#cue-view');
const progress = document.querySelector('#progress');
const episodes = [...new Set(cues.map(c => c.episode))].sort();
let currentIndex = 0;

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
}
function cueKey(c) { return String(c.episode).padStart(2, '0') + ':' + c.cue; }
function filteredCues() { return episodeFilter.value === 'all' ? cues : cues.filter(c => String(c.episode) === episodeFilter.value); }
function alternativeOptions(c) {
  return '<option value="">Chọn ứng viên thay thế</option>' + (c.alternatives || []).slice(0, 6).map(a =>
    `<option value="${esc(a.character_id)}">${esc(a.character_name || a.character_id)} (${esc(a.character_id)}; score=${esc(a.score)})</option>`
  ).join('');
}
function renderSelectors() {
  const list = filteredCues();
  cueSelect.innerHTML = list.map((c, i) => `<option value="${i}">${esc(cueKey(c))} - ${esc(c.text || '')}</option>`).join('');
  cueSelect.value = String(currentIndex);
  document.querySelector('#previous-button').disabled = currentIndex <= 0;
  document.querySelector('#next-button').disabled = currentIndex >= list.length - 1;
}
function updateProgress() {
  const list = filteredCues();
  progress.textContent = Object.keys(decisions).length + '/' + cues.length + ' đã lưu - đang xem ' + (list[currentIndex] ? cueKey(list[currentIndex]) : '-');
}
function renderCue() {
  const list = filteredCues();
  if (!list.length) { view.innerHTML = '<p class="error">Không có cue.</p>'; return; }
  if (currentIndex >= list.length) currentIndex = list.length - 1;
  if (currentIndex < 0) currentIndex = 0;
  const c = list[currentIndex];
  const saved = decisions[cueKey(c)] || {};
  const alternatives = (c.alternatives || []).slice(0, 6);
  const altList = alternatives.length
    ? '<ol>' + alternatives.map(a => `<li><b>${esc(a.character_name || a.character_id)}</b> <code>${esc(a.character_id)}</code> score=<code>${esc(a.score)}</code></li>`).join('') + '</ol>'
    : '<p>Không có ứng viên thay thế.</p>';
  const frame = c.frame_url
    ? `<img src="${esc(c.frame_url)}" alt="Ảnh midpoint tại ${esc(c.frame_seconds)} giây"><p class="small">Ảnh midpoint: ${esc(c.frame_seconds)}s</p>`
    : '<span>Không có ảnh midpoint</span>';
  view.innerHTML = `<article class="cue ${saved.decision ? 'saved' : ''}">
    <header><h2>Tập ${esc(c.episode)} - cue ${esc(c.cue)}</h2><span class="status">${esc(c.match_status)} - needs_review=${Boolean(c.needs_review)}</span></header>
    <p class="time"><b>${Number(c.start).toFixed(3)}s - ${Number(c.end).toFixed(3)}s</b> - duration ${(Number(c.end) - Number(c.start)).toFixed(3)}s</p>
    <p class="text">${esc(c.text)}</p>
    <video id="cue-video" class="cue-video" controls preload="metadata" src="${esc(c.video_url)}" data-start="${Number(c.start).toFixed(3)}" data-end="${Number(c.end).toFixed(3)}"></video>
    <p class="small">Video: <code>${esc(c.video_url)}</code></p>
    <div class="candidate"><b>Ứng viên chính:</b> ${esc(c.character_name || 'unresolved')} <code>${esc(c.character_id)}</code><br>score=<code>${esc(c.candidate_score)}</code> - margin=<code>${esc(c.candidate_margin)}</code></div>
    <details><summary>Ứng viên thay thế (${alternatives.length})</summary>${altList}</details>
    <p><a href="https://www.google.com/search?q=${encodeURIComponent(c.research_query || '')}" target="_blank" rel="noreferrer">Tìm thông tin nhân vật</a></p>
    <div class="frame">${frame}</div>
    <label>Kết quả nghe/xem:
      <select id="decision-select"><option value="">Chưa kiểm tra</option><option value="confirmed">Xác nhận ứng viên chính</option><option value="alternative">Xác nhận ứng viên thay thế</option><option value="unresolved">Chưa xác định</option></select>
    </label>
    <select id="alternative-choice" class="alternative-choice">${alternativeOptions(c)}</select>
    <input id="decision-character" class="decision-character" placeholder="character_id xác nhận nếu tự nhập" autocomplete="off">
    <textarea id="decision-note" class="decision-note" placeholder="Ghi chú: giọng, hình ảnh, ngữ cảnh"></textarea>
    <div class="checks">
      <label><input id="check-timestamp" type="checkbox"> Đã kiểm tra timestamp</label>
      <label><input id="check-video" type="checkbox"> Đã nghe/xem đúng đoạn video</label>
      <label><input id="check-frame" type="checkbox"> Đã đối chiếu ảnh/mốc hình</label>
      <label><input id="check-candidates" type="checkbox"> Đã so ứng viên chính và thay thế</label>
    </div>
    <button id="save-decision" type="button">Lưu quyết định local</button>
    <span id="save-message" class="small"></span>
  </article>`;
  document.querySelector('#decision-select').value = saved.decision || '';
  document.querySelector('#alternative-choice').value = saved.alternative_id || '';
  document.querySelector('#decision-character').value = saved.character_id || '';
  document.querySelector('#decision-note').value = saved.note || '';
  const checks = saved.checks || {};
  document.querySelector('#check-timestamp').checked = Boolean(checks.timestamp);
  document.querySelector('#check-video').checked = Boolean(checks.video_audio);
  document.querySelector('#check-frame').checked = Boolean(checks.frame);
  document.querySelector('#check-candidates').checked = Boolean(checks.candidates);
  const video = document.querySelector('#cue-video');
  video.addEventListener('loadedmetadata', () => { if (!video.dataset.cued) video.currentTime = Number(c.start); });
  video.addEventListener('play', () => { if (!video.dataset.cued) { video.dataset.cued = '1'; video.currentTime = Number(c.start); } });
  video.addEventListener('timeupdate', () => { if (video.currentTime >= Number(c.end)) { video.pause(); video.currentTime = Number(c.start); } });
  document.querySelector('#save-decision').onclick = () => {
    const decision = document.querySelector('#decision-select').value;
    const checkState = {
      timestamp: document.querySelector('#check-timestamp').checked,
      video_audio: document.querySelector('#check-video').checked,
      frame: document.querySelector('#check-frame').checked,
      candidates: document.querySelector('#check-candidates').checked
    };
    const message = document.querySelector('#save-message');
    if ((decision === 'confirmed' || decision === 'alternative') && !Object.values(checkState).every(Boolean)) { message.textContent = 'Cần tích đủ 4 mục kiểm tra trước khi xác nhận.'; return; }
    const alternativeId = document.querySelector('#alternative-choice').value;
    const typedId = document.querySelector('#decision-character').value.trim();
    if (decision === 'alternative' && !alternativeId && !typedId) { message.textContent = 'Hãy chọn hoặc nhập character_id thay thế.'; return; }
    decisions[cueKey(c)] = {decision, character_id: typedId, alternative_id: alternativeId, note: document.querySelector('#decision-note').value.trim(), checks: checkState, saved_at: new Date().toISOString()};
    localStorage.setItem(decisionKey, JSON.stringify(decisions));
    message.textContent = 'Đã lưu local.';
    renderSelectors();
    updateProgress();
  };
  updateProgress();
}
episodes.forEach(e => { const option = document.createElement('option'); option.value = e; option.textContent = 'Tập ' + e; episodeFilter.appendChild(option); });
episodeFilter.onchange = () => { currentIndex = 0; renderSelectors(); renderCue(); };
cueSelect.onchange = () => { currentIndex = Number(cueSelect.value) || 0; renderCue(); };
document.querySelector('#previous-button').onclick = () => { currentIndex--; renderSelectors(); renderCue(); };
document.querySelector('#next-button').onclick = () => { currentIndex++; renderSelectors(); renderCue(); };
document.querySelector('#unreviewed-button').onclick = () => { const index = cues.findIndex(c => !decisions[cueKey(c)]); if (index < 0) return; episodeFilter.value = 'all'; currentIndex = index; renderSelectors(); renderCue(); window.scrollTo({top: 0, behavior: 'smooth'}); };
document.querySelector('#export-button').onclick = () => { const blob = new Blob([JSON.stringify(decisions, null, 2)], {type: 'application/json'}); const link = document.createElement('a'); link.href = URL.createObjectURL(blob); link.download = 'cue-review-decisions.json'; link.click(); setTimeout(() => URL.revokeObjectURL(link.href), 1000); };
renderSelectors();
renderCue();
</script></body></html>
'''


def build(index_paths: list[Path], output: Path) -> None:
    evidence = []
    for path in index_paths:
        evidence.extend(read_evidence(path))
    evidence.sort(key=lambda item: (str(item.get("episode") or ""), int(item.get("cue") or 0)))
    cues = [prepare_item(item, output.parent) for item in evidence]
    payload = json.dumps(cues, ensure_ascii=True, separators=(",", ":"))
    payload = payload.replace("<", "\\u003c")
    content = HTML_TEMPLATE.replace("__CUE_PAYLOAD__", payload).replace("__CUE_COUNT__", str(len(cues)))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    print(f"CUES={len(cues)}")
    print(f"OUTPUT={output.resolve()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    indexes = sorted(args.evidence_root.resolve().glob("episode-*-pack/evidence-index.json"))
    if not indexes:
        raise SystemExit("No evidence-index.json found")
    build(indexes, args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
