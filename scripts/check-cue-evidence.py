#!/usr/bin/env python3
"""Validate timestamp, media, midpoint-frame and candidate evidence packs."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


EPISODE_RE = re.compile(r"-(\d{2})\.vi\.named\.segments\.json$")


def ffprobe_media(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or f"ffprobe exit {result.returncode}"}
    try:
        data = json.loads(result.stdout or "{}")
        duration = float((data.get("format") or {}).get("duration") or 0)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"invalid ffprobe output: {exc}"}
    streams = list(data.get("streams") or [])
    return {
        "ok": duration > 0,
        "duration": duration,
        "video_stream": any(item.get("codec_type") == "video" for item in streams),
        "audio_stream": any(item.get("codec_type") == "audio" for item in streams),
        "streams": [{"type": item.get("codec_type"), "codec": item.get("codec_name")} for item in streams],
    }


def episode_from_report(path: Path) -> str:
    match = EPISODE_RE.search(path.name)
    return match.group(1) if match else ""


def is_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if shutil.which("ffprobe") is None:
        raise SystemExit("ffprobe is required")
    evidence_root = args.evidence_root.resolve()
    reports_dir = args.reports_dir.resolve()
    indexes = sorted(evidence_root.glob("episode-*-pack/evidence-index.json"))
    if not indexes:
        raise SystemExit("No evidence-index.json found")

    registries: dict[str, dict[str, Any]] = {}
    route_casts: dict[str, set[str]] = {}
    for report_path in sorted(reports_dir.glob("*.vi.named.segments.json")):
        episode = episode_from_report(report_path)
        if not episode:
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        registries[episode] = report.get("character_registry") or {}
        route_casts[episode] = {
            str(value).strip()
            for value in ((report.get("route_context") or {}).get("route_cast") or [])
            if str(value).strip()
        }

    media_cache: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    cue_count = 0
    for index_path in indexes:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        episode = str(data.get("episode") or index_path.parent.name[8:10]).zfill(2)
        registry = registries.get(episode, {})
        route_cast = route_casts.get(episode, set())
        for item in data.get("evidence") or []:
            cue_count += 1
            cue_key = f"{episode}:{int(item.get('cue') or 0)}"
            video_path = Path(str(item.get("video") or "")).resolve()
            media_key = str(video_path)
            if media_key not in media_cache:
                media_cache[media_key] = ffprobe_media(video_path) if video_path.exists() else {"ok": False, "error": "video missing"}
            media = media_cache[media_key]
            start = item.get("start")
            end = item.get("end")
            frame = next((frame for frame in item.get("frames") or [] if frame.get("label") == "mid"), None)
            frame_path = index_path.parent / str((frame or {}).get("path") or "")
            candidate_id = str(item.get("character_id") or "").strip()
            alternatives = list(item.get("alternatives") or [])
            checks = {
                "timestamp": is_number(start) and is_number(end) and float(start) >= 0 and float(end) > float(start) and (not media.get("duration") or float(end) <= float(media.get("duration")) + 0.05),
                "video_file": video_path.exists() and bool(media.get("video_stream")),
                "audio_stream": video_path.exists() and bool(media.get("audio_stream")),
                "midpoint_frame": bool(frame) and frame_path.exists(),
                "candidate": bool(candidate_id) and candidate_id in registry and (not route_cast or candidate_id in route_cast) and is_number(item.get("candidate_score")) and is_number(item.get("candidate_margin")),
                "alternatives": bool(alternatives) and all(str(value.get("character_id") or "").strip() in registry and is_number(value.get("score")) for value in alternatives if isinstance(value, dict)),
            }
            for name, passed in checks.items():
                counts[f"{name}_pass"] += int(passed)
                if not passed:
                    counts[f"{name}_fail"] += 1
                    issues.append({"cue": cue_key, "check": name, "video": str(video_path), "start": start, "end": end, "character_id": candidate_id, "detail": media.get("error") if name in {"video_file", "audio_stream", "timestamp"} else None})

    output = {
        "schema_version": 1,
        "mode": "technical_evidence_validation",
        "cue_count": cue_count,
        "video_count": len(media_cache),
        "media": media_cache,
        "checks": {
            name: {"passed": counts[f"{name}_pass"], "failed": counts[f"{name}_fail"]}
            for name in ("timestamp", "video_file", "audio_stream", "midpoint_frame", "candidate", "alternatives")
        },
        "issues": issues,
        "manual_audio_visual_confirmation_required": cue_count,
        "technical_ready": not issues,
        "note": "Technical validation does not claim that a human listened to every cue or confirmed the character identity.",
    }
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"CUES={cue_count}")
    print(f"VIDEOS={len(media_cache)}")
    print(f"ISSUES={len(issues)}")
    for name, values in output["checks"].items():
        print(f"{name.upper()}_PASS={values['passed']}")
        print(f"{name.upper()}_FAIL={values['failed']}")
    print(f"OUTPUT={output_path}")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
