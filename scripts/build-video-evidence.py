#!/usr/bin/env python3
"""Check VTT/video timing and build a reviewable frame evidence pack.

The command is local-only.  It never re-encodes the source video or uploads
anything.  It validates clean VTT labels, compares report/VTT timestamps,
checks cue ends against ffprobe duration, extracts frames at cue times, and
creates a character research/mapping queue with search links.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from vtt_time import format_interval


TIMESTAMP_EPSILON = 0.011
TIMESTAMP_RE = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})$")
VOICE_TAG_RE = re.compile(r"<v\s+[^>]*>", re.IGNORECASE)
BRACKET_LABEL_RE = re.compile(r"^\s*\[[^\]\r\n]{1,100}\]\s*")


def parse_timestamp(value: str) -> float | None:
    match = TIMESTAMP_RE.fullmatch(value.strip().replace(",", "."))
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    milliseconds = int(match.group(4))
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def has_prior_overlap(intervals: list[tuple[float, float]], start: float, end: float) -> bool:
    """Allow an out-of-order cue when it belongs to an overlapping source layer."""
    return any(
        min(previous_end, end) - max(previous_start, start) > TIMESTAMP_EPSILON
        for previous_start, previous_end in intervals
    )


def parse_vtt(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    cues: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if "-->" not in line:
            index += 1
            continue
        left, right = line.split("-->", 1)
        start_text = left.strip().split()[0] if left.strip() else ""
        end_text = right.strip().split()[0] if right.strip() else ""
        start = parse_timestamp(start_text)
        end = parse_timestamp(end_text)
        if start is None or end is None or end <= start:
            issues.append({"type": "invalid_vtt_timestamp", "line": index + 1, "value": line.strip()})
            index += 1
            continue

        cue_number: int | None = None
        if index > 0 and lines[index - 1].strip().isdigit():
            cue_number = int(lines[index - 1].strip())
        text_lines: list[str] = []
        text_index = index + 1
        while text_index < len(lines) and lines[text_index].strip():
            if "-->" in lines[text_index]:
                break
            text_lines.append(lines[text_index])
            text_index += 1
        cues.append(
            {
                "cue": cue_number or len(cues) + 1,
                "start": start,
                "end": end,
                "text": "\n".join(text_lines).strip(),
                "line": index + 1,
            }
        )
        index = text_index
    return cues, issues


def probe_duration(video: Path) -> tuple[float | None, str | None]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None, "ffprobe_not_found"
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return None, f"ffprobe_error:{error.__class__.__name__}"
    if result.returncode != 0:
        return None, "ffprobe_failed"
    duration = finite_number(result.stdout.strip())
    return (duration, None) if duration and duration > 0 else (None, "ffprobe_invalid_duration")


def episode_code(report: dict[str, Any], path: Path) -> str:
    title = str(report.get("title") or path.stem)
    match = re.search(r"(\d{2})$", title)
    return match.group(1) if match else path.stem[-2:]


def base_title(report: dict[str, Any]) -> str:
    title = str(report.get("title") or "video")
    return re.sub(r"\s+-\s+\d{2}$", "", title).strip()


def compare_cues(
    report_rows: list[dict[str, Any]],
    source_cues: list[dict[str, Any]],
    named_cues: list[dict[str, Any]],
    video_duration: float | None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if len(report_rows) != len(source_cues):
        issues.append(
            {"type": "report_source_vtt_count_mismatch", "report_cues": len(report_rows), "source_vtt_cues": len(source_cues)}
        )
    if named_cues and len(named_cues) != len(source_cues):
        issues.append(
            {"type": "named_source_vtt_count_mismatch", "named_vtt_cues": len(named_cues), "source_vtt_cues": len(source_cues)}
        )

    previous_start: float | None = None
    prior_intervals: list[tuple[float, float]] = []
    for index, row in enumerate(report_rows, start=1):
        cue = row.get("cue", index)
        start = finite_number(row.get("start"))
        end = finite_number(row.get("end"))
        if start is None or end is None or start < 0 or end <= start:
            issues.append({"type": "invalid_report_timestamp", "cue": cue, "start": row.get("start"), "end": row.get("end")})
            continue
        if previous_start is not None and start + TIMESTAMP_EPSILON < previous_start:
            if not has_prior_overlap(prior_intervals, start, end):
                issues.append({"type": "non_monotonic_report_timestamp", "cue": cue, "previous_start": previous_start, "start": start})
        previous_start = start
        prior_intervals.append((start, end))
        if video_duration is not None and end > video_duration + TIMESTAMP_EPSILON:
            issues.append({"type": "timestamp_after_video_end", "cue": cue, "end": end, "video_duration": video_duration})
        if index <= len(source_cues):
            source = source_cues[index - 1]
            if abs(start - source["start"]) > TIMESTAMP_EPSILON or abs(end - source["end"]) > TIMESTAMP_EPSILON:
                issues.append({"type": "report_source_vtt_timestamp_mismatch", "cue": cue, "report": [start, end], "source": [source["start"], source["end"]]})
        if index <= len(named_cues):
            named = named_cues[index - 1]
            if abs(start - named["start"]) > TIMESTAMP_EPSILON or abs(end - named["end"]) > TIMESTAMP_EPSILON:
                issues.append({"type": "report_named_vtt_timestamp_mismatch", "cue": cue, "report": [start, end], "named": [named["start"], named["end"]]})
    return issues


def clean_vtt_issues(path: Path, cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    raw = path.read_text(encoding="utf-8-sig")
    if VOICE_TAG_RE.search(raw):
        issues.append({"type": "visible_vtt_voice_tag", "path": str(path)})
    if "NOTE speaker-labels=false" not in raw:
        issues.append({"type": "speaker_labels_not_disabled", "path": str(path)})
    for cue in cues:
        text = str(cue.get("text") or "")
        if VOICE_TAG_RE.search(text):
            issues.append({"type": "visible_vtt_voice_tag", "path": str(path), "cue": cue["cue"], "text": text})
        if BRACKET_LABEL_RE.search(text):
            issues.append({"type": "visible_bracket_speaker_label", "path": str(path), "cue": cue["cue"], "text": text})
    return issues


def load_summary_issue_cues(path: Path | None) -> set[tuple[str, int]]:
    if not path or not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    result: set[tuple[str, int]] = set()
    for issue in data.get("issues") or []:
        try:
            cue = int(issue.get("cue"))
        except (TypeError, ValueError):
            continue
        episode = str(issue.get("episode") or "")
        match = re.search(r"(\d{2})$", episode)
        if match:
            result.add((match.group(1), cue))
    return result


def select_rows(
    report: dict[str, Any],
    code: str,
    mode: str,
    issue_cues: set[tuple[str, int]],
    local_issue_cues: set[int],
    selectors: set[tuple[str, int]],
    max_cues: int,
) -> list[dict[str, Any]]:
    rows = list(report.get("kept") or [])
    if selectors:
        selected = [row for row in rows if (code, int(row.get("cue") or 0)) in selectors]
    elif mode == "all":
        selected = rows
    elif mode == "review":
        selected = [row for row in rows if row.get("needs_review")]
    else:
        selected = [
            row
            for row in rows
            if (code, int(row.get("cue") or 0)) in issue_cues or int(row.get("cue") or 0) in local_issue_cues
        ]
    return selected if max_cues <= 0 else selected[:max_cues]


def shot_times(row: dict[str, Any], shots: list[str]) -> dict[str, float]:
    start = finite_number(row.get("start")) or 0.0
    end = finite_number(row.get("end")) or start + 0.1
    safe_end = max(start, end - min(0.15, max(0.02, (end - start) / 4)))
    values = {"start": start, "mid": start + (end - start) / 2, "end": safe_end}
    result: dict[str, float] = {}
    for name in shots:
        value = values[name]
        if not any(abs(value - existing) < 0.01 for existing in result.values()):
            result[name] = round(max(0.0, value), 3)
    return result


def extract_frame(ffmpeg: str, video: Path, seconds: float, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{seconds:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not destination.is_file():
        detail = (result.stderr or "ffmpeg did not create a frame").strip()
        raise RuntimeError(detail[-500:])


def infer_age_band(character: dict[str, Any]) -> str:
    if character.get("age_band"):
        return str(character["age_band"])
    age = finite_number(character.get("age"))
    if age is None:
        return "unknown"
    if age <= 12:
        return "child"
    if age <= 17:
        return "teen"
    if age <= 59:
        return "adult"
    return "senior"


def load_or_create_mapping(
    mapping_path: Path,
    report: dict[str, Any],
    character_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    if mapping_path.is_file():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    else:
        mapping = {
            "schema_version": 1,
            "title": base_title(report),
            "status": "research_required",
            "sources": list((character_profile or {}).get("sources") or []),
            "characters": {},
        }
    mapping.setdefault("schema_version", 1)
    mapping.setdefault("title", base_title(report))
    mapping.setdefault("status", "research_required")
    mapping.setdefault("sources", list((character_profile or {}).get("sources") or []))
    mapping.setdefault("characters", {})
    ensure_mapping_characters(mapping, report, character_profile)
    return mapping


def ensure_mapping_characters(
    mapping: dict[str, Any],
    report: dict[str, Any],
    character_profile: dict[str, Any] | None,
) -> None:
    """Merge every episode's registry into one mapping document."""
    for character_id, character in sorted((report.get("character_registry") or {}).items()):
        profile_values = {
            "character_id": character_id,
            "name": character.get("display_name") or character_id,
            "aliases": list(character.get("aliases") or []),
            "age": character.get("age"),
            "age_band": infer_age_band(character),
            "gender": character.get("gender") or "unknown",
            "role": character.get("role"),
        }
        entry = mapping["characters"].setdefault(character_id, {})
        entry["character_id"] = character_id
        entry["name"] = profile_values["name"]
        entry["aliases"] = profile_values["aliases"]
        entry["age"] = profile_values["age"]
        entry["age_band"] = profile_values["age_band"]
        entry["gender"] = profile_values["gender"]
        entry["role"] = profile_values["role"]
        default_source_urls = [
            str(source.get("url"))
            for source in (mapping.get("sources") or [])
            if isinstance(source, dict) and source.get("url")
        ]
        if not entry.get("source_urls"):
            entry["source_urls"] = default_source_urls
        entry.setdefault("reference_frames", [])
        entry.setdefault("mapping_status", "needs_source_review")
        entry["profile_values"] = profile_values
        entry.setdefault("verified_values", {})
        entry.setdefault("research_query", f'"{base_title(report)}" "{profile_values["name"]}" character official')


def remove_reference_frames_for_cue(mapping: dict[str, Any], episode: str, cue: int) -> None:
    """Move evidence when a cue's character mapping is corrected and rerun."""
    prefix = f"episode-{episode}/cue-{cue:04d}/"
    for entry in (mapping.get("characters") or {}).values():
        frames = entry.get("reference_frames")
        if isinstance(frames, list):
            entry["reference_frames"] = [path for path in frames if not str(path).startswith(prefix)]


def write_review_html(path: Path, evidence: list[dict[str, Any]], mapping: dict[str, Any]) -> None:
    rows: list[str] = []
    for item in evidence:
        image_tags: list[str] = []
        for frame in item.get("frames") or []:
            frame_path = html.escape(str(frame.get("path") or "").replace("\\", "/"))
            frame_label = html.escape(str(frame.get("label") or "frame"))
            image_tags.append(f'<img loading="lazy" src="{frame_path}" alt="{frame_label}" width="320">')
        images = " ".join(image_tags)
        query = str(item.get("research_query") or "")
        search = f'https://www.google.com/search?q={quote_plus(query)}' if query else "#"
        ffplay_command = html.escape(str(item.get("ffplay_command") or ""))
        rows.append(
            "<article>"
            f"<h2>{html.escape(str(item.get('episode')))} / cue {html.escape(str(item.get('cue')))} &mdash; {html.escape(str(item.get('character_name') or 'unresolved'))}</h2>"
            f"<p><b>VTT time:</b> <code>{html.escape(str(item.get('timestamp') or format_interval(item.get('start'), item.get('end'))))}</code> &nbsp; <b>Seconds:</b> {item.get('start')}&ndash;{item.get('end')}s &nbsp; <b>Status:</b> {html.escape(str(item.get('match_status') or ''))}</p>"
            f"<p>{html.escape(str(item.get('text') or ''))}</p>"
            f'<p><a href="{html.escape(search)}" target="_blank" rel="noreferrer">Tìm thông tin nhân vật</a> &nbsp; <code>{html.escape(query)}</code></p>'
            f"<p><code>{ffplay_command}</code></p>"
            f"<div>{images}</div>"
            "</article>"
        )
    content = "<!doctype html><html lang='vi'><meta charset='utf-8'><title>CineZero video evidence</title>"
    content += "<style>body{font:15px system-ui;max-width:1400px;margin:24px auto;background:#111;color:#eee}article{border:1px solid #444;padding:16px;margin:16px 0}img{margin:4px;vertical-align:top}a{color:#8ecbff}code{color:#ccc}</style>"
    content += f"<h1>{html.escape(str(mapping.get('title') or 'Character evidence'))}</h1>"
    content += "<p>Ảnh chỉ là bằng chứng khung hình tại cue. Vẫn phải mở video và nghe đúng khoảng thời gian trước khi xác nhận nhân vật.</p>"
    content += "".join(rows) or "<p>Không có cue được chọn.</p>"
    content += "</html>\n"
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--character-profile", type=Path)
    parser.add_argument("--mapping-output", type=Path)
    parser.add_argument("--episodes", nargs="*", default=[], help="Limit evidence generation to episode codes such as 07 12")
    parser.add_argument("--mode", choices=("issues", "review", "all"), default="issues")
    parser.add_argument("--cues", nargs="*", default=[], help="Explicit selectors such as 07:91 12:158")
    parser.add_argument("--max-cues", type=int, default=100, help="0 means unlimited")
    parser.add_argument("--shots", nargs="+", choices=("start", "mid", "end"), default=["start", "mid", "end"])
    parser.add_argument("--reuse-existing-frames", action="store_true", help="Reuse already extracted cue frames when the source video is unavailable")
    parser.add_argument("--allow-missing-video", action="store_true", help="Keep static VTT/mapping validation usable when local videos are not mounted")
    parser.add_argument("--no-mapping-update", action="store_true", help="Build evidence without treating provisional cue frames as character reference frames")
    args = parser.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("Không tìm thấy ffmpeg trong PATH")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_issue_cues = load_summary_issue_cues(args.summary)
    selectors: set[tuple[str, int]] = set()
    for value in args.cues:
        match = re.fullmatch(r"(\d{1,2}):(\d+)", value.strip())
        if not match:
            raise SystemExit(f"Selector không hợp lệ: {value}; dùng dạng 07:91")
        selectors.add((match.group(1).zfill(2), int(match.group(2))))

    profile: dict[str, Any] | None = None
    if args.character_profile:
        profile = json.loads(args.character_profile.resolve().read_text(encoding="utf-8"))

    reports = sorted(args.reports_dir.resolve().glob("*.named.segments.json"))
    requested_episodes = {str(value).zfill(2) for value in args.episodes}
    if requested_episodes:
        reports = [
            path
            for path in reports
            if episode_code(json.loads(path.read_text(encoding="utf-8")), path) in requested_episodes
        ]
    if not reports:
        raise SystemExit(f"Không tìm thấy report trong {args.reports_dir}")

    evidence: list[dict[str, Any]] = []
    report_results: list[dict[str, Any]] = []
    frame_errors: list[dict[str, Any]] = []
    mapping: dict[str, Any] | None = None
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        code = episode_code(report, report_path)
        if mapping is None:
            mapping_path = (args.mapping_output or (args.output_dir / f"{base_title(report).lower().replace(' ', '-')}-character-mapping.json")).resolve()
            mapping = load_or_create_mapping(mapping_path, report, profile)
        else:
            ensure_mapping_characters(mapping, report, profile)

        source_video = Path(str(report.get("source_video") or ""))
        source_vtt = Path(str(report.get("source_vtt") or ""))
        named_vtt = report_path.with_name(report_path.name.replace(".segments.json", ".vtt"))
        report_rows = list(report.get("kept") or [])
        source_cues, source_parse_issues = (parse_vtt(source_vtt) if source_vtt.is_file() else ([], [{"type": "missing_source_vtt"}]))
        named_cues, named_parse_issues = (parse_vtt(named_vtt) if named_vtt.is_file() else ([], [{"type": "missing_named_vtt"}]))
        duration, duration_error = probe_duration(source_video) if source_video.is_file() else (None, "missing_video")
        if args.allow_missing_video and duration_error == "missing_video":
            duration_error = None
        validation_issues = compare_cues(report_rows, source_cues, named_cues, duration)
        route_cast = {
            str(value).strip()
            for value in ((report.get("route_context") or {}).get("route_cast") or [])
            if str(value).strip()
        }
        for row in report_rows:
            character_id = str(row.get("character_id") or "").strip()
            if route_cast and character_id not in route_cast:
                validation_issues.append(
                    {
                        "type": "character_outside_route_cast",
                        "cue": row.get("cue"),
                        "character_id": character_id,
                        "allowed_character_ids": sorted(route_cast),
                    }
                )
        validation_issues.extend(source_parse_issues)
        validation_issues.extend(named_parse_issues)
        if named_vtt.is_file():
            validation_issues.extend(clean_vtt_issues(named_vtt, named_cues))
        if source_vtt.is_file():
            validation_issues.extend({**issue, "path": str(source_vtt), "source": True} for issue in clean_vtt_issues(source_vtt, source_cues) if issue["type"] != "speaker_labels_not_disabled")
        if duration_error:
            validation_issues.append({"type": "video_duration_unchecked", "reason": duration_error})

        local_issue_cues = {
            int(issue["cue"])
            for issue in validation_issues
            if issue.get("cue") is not None
        }
        selected = select_rows(report, code, args.mode, summary_issue_cues, local_issue_cues, selectors, args.max_cues)
        report_results.append(
            {
                "episode": code,
                "report": str(report_path.resolve()),
                "video": str(source_video),
                "source_vtt": str(source_vtt),
                "named_vtt": str(named_vtt),
                "duration_seconds": duration,
                "report_cues": len(report_rows),
                "source_vtt_cues": len(source_cues),
                "named_vtt_cues": len(named_cues),
                "selected_cues": len(selected),
                "validation_issues": validation_issues,
            }
        )

        for row in selected:
            cue = int(row.get("cue") or 0)
            evidence_dir = args.output_dir / f"episode-{code}" / f"cue-{cue:04d}"
            frames: list[dict[str, Any]] = []
            for label, seconds in shot_times(row, args.shots).items():
                destination = evidence_dir / f"{label}.jpg"
                if args.reuse_existing_frames and not source_video.is_file() and destination.is_file():
                    relative = destination.relative_to(args.output_dir).as_posix()
                    frames.append({"label": label, "seconds": seconds, "path": relative})
                    continue
                if args.reuse_existing_frames and not source_video.is_file():
                    continue
                try:
                    extract_frame(ffmpeg, source_video, seconds, destination)
                    relative = destination.relative_to(args.output_dir).as_posix()
                    frames.append({"label": label, "seconds": seconds, "path": relative})
                except (OSError, RuntimeError) as error:
                    frame_errors.append({"episode": code, "cue": cue, "label": label, "error": str(error)})
            character_id = str(row.get("character_id") or "").strip()
            mapping_entry = (mapping or {}).get("characters", {}).get(character_id, {})
            research_query = str(mapping_entry.get("research_query") or f'"{base_title(report)}" "{row.get("character_name") or character_id}" character official')
            relative_mid_frames = [frame["path"] for frame in frames if frame["label"] == "mid"]
            if mapping and not args.no_mapping_update and character_id in mapping.get("characters", {}):
                remove_reference_frames_for_cue(mapping, code, cue)
                entry = mapping["characters"][character_id]
                for frame_path in frames:
                    if frame_path["path"] not in entry.setdefault("reference_frames", []):
                        entry["reference_frames"].append(frame_path["path"])
            evidence.append(
                {
                    "episode": code,
                    "cue": cue,
                    "id": row.get("id"),
                "video": str(source_video),
                "source_video_exists": source_video.is_file(),
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "timestamp": row.get("timestamp") or format_interval(row.get("start"), row.get("end")),
                    "text": row.get("text"),
                    "scene": row.get("scene"),
                    "character_id": character_id,
                    "character_name": row.get("character_name"),
                    "match_status": row.get("match_status"),
                    "needs_review": bool(row.get("needs_review")),
                    "candidate_score": row.get("candidate_score"),
                    "candidate_margin": row.get("candidate_margin"),
                    "alternatives": row.get("alternatives") or [],
                    "frames": frames,
                    "mid_frames": relative_mid_frames,
                    "research_query": research_query,
                    "research_search_url": f"https://www.google.com/search?q={quote_plus(research_query)}",
                    "ffplay_command": f'ffplay -ss {float(row.get("start") or 0):.3f} -t {max(0.5, float(row.get("end") or 0) - float(row.get("start") or 0) + 0.5):.3f} -autoexit "{source_video}"',
                    "visual_identity_status": "context_frame_only_audio_review_required",
                }
            )

    if mapping is None:
        raise SystemExit("Không tạo được character mapping")
    mapping_path = (args.mapping_output or (args.output_dir / f"{base_title(json.loads(reports[0].read_text(encoding='utf-8'))).lower().replace(' ', '-')}-character-mapping.json")).resolve()
    mapping["generated_at"] = datetime.now(timezone.utc).isoformat()
    mapping["evidence_index"] = str((args.output_dir / "evidence-index.json").resolve())
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    evidence_index = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "shots": args.shots,
        "reuse_existing_frames": args.reuse_existing_frames,
        "allow_missing_video": args.allow_missing_video,
        "no_mapping_update": args.no_mapping_update,
        "reports": report_results,
        "evidence": evidence,
        "frame_errors": frame_errors,
        "mapping": str(mapping_path),
        "summary": {
            "reports": len(report_results),
            "cues_selected": len(evidence),
            "frames": sum(len(item.get("frames") or []) for item in evidence),
            "frame_errors": len(frame_errors),
            "validation_issues": sum(len(item.get("validation_issues") or []) for item in report_results),
        },
    }
    index_path = args.output_dir / "evidence-index.json"
    index_path.write_text(json.dumps(evidence_index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    csv_path = args.output_dir / "evidence.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        columns = ["episode", "cue", "start", "end", "timestamp", "character_id", "character_name", "match_status", "needs_review", "candidate_score", "candidate_margin", "research_query", "frames", "text"]
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for item in evidence:
            row = dict(item)
            row["frames"] = " | ".join(frame["path"] for frame in item.get("frames") or [])
            writer.writerow(row)

    html_path = args.output_dir / "review.html"
    write_review_html(html_path, evidence, mapping)
    print(f"REPORTS={len(report_results)}")
    print(f"SELECTED_CUES={len(evidence)}")
    print(f"FRAMES={sum(len(item.get('frames') or []) for item in evidence)}")
    print(f"VALIDATION_ISSUES={evidence_index['summary']['validation_issues']}")
    print(f"FRAME_ERRORS={len(frame_errors)}")
    print(f"EVIDENCE_INDEX={index_path.resolve()}")
    print(f"MAPPING={mapping_path}")
    print(f"REVIEW_HTML={html_path.resolve()}")
    return 0 if not frame_errors and evidence_index["summary"]["validation_issues"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
