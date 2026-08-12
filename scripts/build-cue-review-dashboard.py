#!/usr/bin/env python3
"""Build a local cue-by-cue audio/video review dashboard.

The page is deliberately review-only. It reads evidence indexes generated
from the encoded videos and writes a static HTML page containing the exact
cue range, the midpoint frame, the machine candidate and alternatives. A
decision is saved in browser localStorage and can be exported as JSON. This
script never edits VTT files or character reports.
"""

from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
from urllib.parse import quote_plus


def read_evidence(index_path: Path) -> list[dict]:
    data = json.loads(index_path.read_text(encoding="utf-8"))
    rows = []
    for raw in data.get("evidence") or []:
        item = dict(raw)
        item["_frame_root"] = str(index_path.parent)
        rows.append(item)
    return rows


def alt_html(item: dict) -> str:
    rows = []
    for candidate in (item.get("alternatives") or [])[:6]:
        rows.append(
            "<li>"
            f"<b>{html.escape(str(candidate.get('character_name') or candidate.get('character_id') or 'unknown'))}</b> "
            f"<code>{html.escape(str(candidate.get('character_id') or ''))}</code> "
            f"score={html.escape(str(candidate.get('score') or ''))}"
            "</li>"
        )
    return "".join(rows) or "<li>No alternative candidate</li>"


def alt_options(item: dict) -> str:
    options = ["<option value=''>Choose an alternative candidate</option>"]
    for candidate in (item.get("alternatives") or [])[:6]:
        character_id = str(candidate.get("character_id") or "").strip()
        if not character_id:
            continue
        label = str(candidate.get("character_name") or character_id)
        score = str(candidate.get("score") or "")
        options.append(
            f"<option value='{html.escape(character_id, quote=True)}'>"
            f"{html.escape(label)} ({html.escape(character_id)}; score={html.escape(score)})"
            "</option>"
        )
    return "".join(options)


def media_url(video_path: str, output_parent: Path) -> str:
    source = Path(video_path).resolve()
    try:
        return html.escape(Path(os.path.relpath(source, output_parent.resolve())).as_posix(), quote=True)
    except ValueError:
        return html.escape(source.as_uri(), quote=True)


def cue_html(item: dict, number: int, output_parent: Path) -> str:
    episode = html.escape(str(item.get("episode") or ""), quote=True)
    cue = int(item.get("cue") or number)
    start = float(item.get("start") or 0)
    end = float(item.get("end") or 0)
    character = html.escape(str(item.get("character_name") or "unresolved"))
    character_id = html.escape(str(item.get("character_id") or ""), quote=True)
    status = html.escape(str(item.get("match_status") or ""))
    score = html.escape(str(item.get("candidate_score") or ""))
    margin = html.escape(str(item.get("candidate_margin") or ""))
    text = html.escape(str(item.get("text") or ""))
    frames = []
    for frame in item.get("frames") or []:
        if str(frame.get("label")) == "mid":
            frame_file = Path(str(item.get("_frame_root") or output_parent)) / str(frame.get("path") or "")
            frame_path = html.escape(Path(os.path.relpath(frame_file.resolve(), output_parent.resolve())).as_posix(), quote=True)
            frames.append(f"<img loading='lazy' src='{frame_path}' alt='midpoint frame' width='420'>")
    query = quote_plus(str(item.get("research_query") or ""))
    return f"""
    <article class='cue' id='cue-{episode}-{cue:04d}' data-episode='{episode}' data-cue='{cue}'
      data-start='{start:.3f}' data-end='{end:.3f}' data-character='{character_id}' data-status='{status}'>
      <header><h2>Episode {episode} - cue {cue}</h2><span class='status'>{status} - needs_review={str(bool(item.get('needs_review'))).lower()}</span></header>
      <p class='time'><b>{start:.3f}s - {end:.3f}s</b> - duration {max(0, end-start):.3f}s</p>
      <p class='text'>{text}</p>
      <video class='cue-video' controls preload='metadata' src='{media_url(str(item.get('video') or ''), output_parent)}'
        data-start='{start:.3f}' data-end='{end:.3f}'></video>
      <div class='candidate'><b>Main candidate:</b> {character} <code>{character_id}</code><br>
        score=<code>{score}</code> - margin=<code>{margin}</code></div>
      <details><summary>Alternative candidates</summary><ol>{alt_html(item)}</ol></details>
      <p><a href='https://www.google.com/search?q={query}' target='_blank' rel='noreferrer'>Search character information</a></p>
      <div class='frame'>{''.join(frames) or '<span>No midpoint frame</span>'}</div>
      <label class='decision'>Review result:
        <select class='decision-select'><option value=''>Not reviewed</option><option value='confirmed'>Confirm main candidate</option><option value='alternative'>Confirm alternative</option><option value='unresolved'>Still unresolved</option></select>
      </label>
      <select class='alternative-choice'>{alt_options(item)}</select>
      <input class='decision-character' placeholder='Confirmed character_id (if typed manually)' autocomplete='off'>
      <textarea class='decision-note' placeholder='Short note: voice, frame, context'></textarea>
      <div class='checks'>
        <label><input type='checkbox' class='check-timestamp'> Timestamp checked</label>
        <label><input type='checkbox' class='check-video'> Video/audio segment checked</label>
        <label><input type='checkbox' class='check-frame'> Frame/context checked</label>
        <label><input type='checkbox' class='check-candidates'> Main and alternatives compared</label>
      </div>
      <button class='save-decision' type='button'>Save local decision</button>
    </article>
    """


def build(index_paths: list[Path], output: Path) -> None:
    evidence = []
    for path in index_paths:
        evidence.extend(read_evidence(path))
    evidence.sort(key=lambda item: (str(item.get("episode") or ""), int(item.get("cue") or 0)))
    cards = "".join(cue_html(item, index, output.parent) for index, item in enumerate(evidence, 1))
    output.parent.mkdir(parents=True, exist_ok=True)
    content = f"""<!doctype html>
<html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>CineZero - review {len(evidence)} cues</title>
<style>
body{{font:15px system-ui;background:#101218;color:#edf1f7;max-width:1220px;margin:0 auto;padding:18px}}
h1{{margin-bottom:4px}} .toolbar{{position:sticky;top:0;z-index:10;background:#171a24;border:1px solid #34394a;padding:12px;border-radius:12px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
button,select,input,textarea{{font:inherit;background:#0c0f16;color:#edf1f7;border:1px solid #3d4352;border-radius:8px;padding:8px}}
button{{cursor:pointer;background:#2e4164}} .cue{{border:1px solid #34394a;border-radius:14px;padding:16px;margin:16px 0;background:#171a24}}
.cue header{{display:flex;gap:12px;justify-content:space-between;align-items:center}} h2{{margin:0}} .status{{color:#ffc36b;font-size:12px}} .time{{color:#91baff}} .text{{font-size:18px;white-space:pre-wrap}}
.cue-video{{display:block;width:min(900px,100%);background:#000;margin:12px 0}} .candidate{{line-height:1.7}} code{{color:#b9d1ff}} a{{color:#8fc7ff}}
.frame img{{max-width:420px;width:100%;height:auto;border-radius:8px}} details{{margin:10px 0}} summary{{cursor:pointer;color:#b9d1ff}}
.decision{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}} .decision-character,.decision-note,.alternative-choice{{display:block;width:min(700px,100%);margin-top:8px}} .decision-note{{min-height:55px}} .checks{{display:grid;gap:5px;margin:12px 0;color:#c7d0df}}
.saved{{outline:2px solid #58c889}} .hidden{{display:none}}
</style></head><body>
<h1>Cue character review - {len(evidence)} cues</h1>
<p>Each video starts at the cue timestamp and stops at the cue end. Check the audio, midpoint frame, candidates and context. Confirmed decisions require all four checks and are only applied after importing the exported JSON.</p>
<div class='toolbar'><label>Episode <select id='episode-filter'><option value='all'>All</option></select></label><label>Find cue <input id='cue-filter' placeholder='07:91'></label><button id='next-button'>Next unreviewed</button><button id='export-button'>Export decisions JSON</button><span id='progress'></span></div>
<main id='cards'>{cards}</main>
<script>
const key='cinezero-cue-review-decisions-v2';
const cards=[...document.querySelectorAll('.cue')];
const decisions=JSON.parse(localStorage.getItem(key)||'{{}}');
const episodeFilter=document.querySelector('#episode-filter');
const cueFilter=document.querySelector('#cue-filter');
const progress=document.querySelector('#progress');
const episodes=[...new Set(cards.map(x=>x.dataset.episode))].sort();
episodes.forEach(e=>{{const o=document.createElement('option');o.value=e;o.textContent='Episode '+e;episodeFilter.appendChild(o)}});
function keyFor(c){{return c.dataset.episode+':'+c.dataset.cue}}
function applySaved(c){{const d=decisions[keyFor(c)]; if(!d)return; c.classList.add('saved'); c.querySelector('.decision-select').value=d.decision||''; c.querySelector('.alternative-choice').value=d.alternative_id||''; c.querySelector('.decision-character').value=d.character_id||''; c.querySelector('.decision-note').value=d.note||''; const checks=d.checks||{{}}; c.querySelector('.check-timestamp').checked=Boolean(checks.timestamp); c.querySelector('.check-video').checked=Boolean(checks.video_audio); c.querySelector('.check-frame').checked=Boolean(checks.frame); c.querySelector('.check-candidates').checked=Boolean(checks.candidates)}}
cards.forEach(c=>{{applySaved(c);c.querySelector('.save-decision').onclick=()=>{{const decision=c.querySelector('.decision-select').value; const checks={{timestamp:c.querySelector('.check-timestamp').checked,video_audio:c.querySelector('.check-video').checked,frame:c.querySelector('.check-frame').checked,candidates:c.querySelector('.check-candidates').checked}}; if((decision==='confirmed'||decision==='alternative')&&!Object.values(checks).every(Boolean)){{alert('A confirmation requires all four checks.');return}} const alternativeId=c.querySelector('.alternative-choice').value; const typedId=c.querySelector('.decision-character').value.trim(); if(decision==='alternative'&&!alternativeId&&!typedId){{alert('Choose or type the confirmed alternative character_id.');return}} const d={{decision,character_id:typedId,alternative_id:alternativeId,note:c.querySelector('.decision-note').value.trim(),checks,saved_at:new Date().toISOString()}};decisions[keyFor(c)]=d;localStorage.setItem(key,JSON.stringify(decisions));c.classList.add('saved');update()}}}});
function visible(c){{return (episodeFilter.value==='all'||c.dataset.episode===episodeFilter.value)&&(!cueFilter.value.trim()||keyFor(c).includes(cueFilter.value.trim()))}}
function update(){{let shown=0;cards.forEach(c=>{{const yes=visible(c);c.classList.toggle('hidden',!yes);shown+=yes?1:0}});progress.textContent=Object.keys(decisions).length+'/'+cards.length+' saved; showing '+shown}}
episodeFilter.onchange=update;cueFilter.oninput=update;
document.querySelector('#next-button').onclick=()=>{{const c=cards.find(x=>!decisions[keyFor(x)]);if(!c)return;episodeFilter.value='all';cueFilter.value=keyFor(c);update();c.scrollIntoView({{behavior:'smooth',block:'start'}})}};
document.querySelector('#export-button').onclick=()=>{{const blob=new Blob([JSON.stringify(decisions,null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='cue-review-decisions.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}};
document.addEventListener('play',e=>{{const v=e.target;if(!v.matches('.cue-video'))return;const start=Number(v.dataset.start||0),end=Number(v.dataset.end||0);if(!v.dataset.cued){{v.dataset.cued='1';v.currentTime=start}}v.ontimeupdate=()=>{{if(v.currentTime>=end){{v.pause();v.currentTime=start}}}}}},true);
update();
</script></body></html>
"""
    output.write_text(content, encoding="utf-8")
    print(f"CUES={len(evidence)}")
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
