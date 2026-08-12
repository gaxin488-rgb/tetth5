#!/usr/bin/env python3
"""Validate named character reports against the source videos and VTT cues."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from vtt_time import format_interval


REQUIRED_FIELDS = (
    "character_id",
    "character_name",
    "character_age",
    "character_age_band",
    "character_gender",
    "character_role",
    "candidate_score",
    "candidate_margin",
    "alternatives",
    "timestamp",
)

TIMESTAMP_EPSILON = 0.011
VTT_TIMESTAMP_RE = re.compile(r"^(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})$")


def parse_timestamp(value: str) -> float | None:
    """Parse a WebVTT timestamp into seconds."""
    match = VTT_TIMESTAMP_RE.fullmatch(value.strip().replace(",", "."))
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    milliseconds = int(match.group(4))
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_vtt_cues(path: Path) -> tuple[list[tuple[float, float]], list[dict[str, Any]]]:
    cues: list[tuple[float, float]] = []
    issues: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if "-->" not in line:
            continue
        left, right = line.split("-->", 1)
        start_text = left.strip().split()[0] if left.strip() else ""
        end_text = right.strip().split()[0] if right.strip() else ""
        start = parse_timestamp(start_text)
        end = parse_timestamp(end_text)
        if start is None or end is None or end <= start:
            issues.append(
                {
                    "type": "invalid_vtt_timestamp",
                    "path": str(path),
                    "line": line_number,
                    "value": line.strip(),
                }
            )
            continue
        cues.append((start, end))
    return cues, issues


def probe_duration(path: Path) -> tuple[float | None, str | None]:
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
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        return None, f"ffprobe_error:{error.__class__.__name__}"
    if result.returncode != 0:
        return None, "ffprobe_failed"
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError):
        return None, "ffprobe_invalid_duration"
    if not math.isfinite(duration) or duration <= 0:
        return None, "ffprobe_invalid_duration"
    return duration, None


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def has_prior_overlap(intervals: list[tuple[float, float]], start: float, end: float) -> bool:
    """Allow an out-of-order cue when it overlaps an earlier subtitle layer."""
    return any(
        min(previous_end, end) - max(previous_start, start) > TIMESTAMP_EPSILON
        for previous_start, previous_end in intervals
    )


def vtt_cue_count(path: Path) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "-->" in line and line.strip().split("-->", 1)[0].strip():
            count += 1
    return count


def inferred_age_band(character: dict[str, Any]) -> str:
    declared = str(character.get("age_band") or "").strip()
    if declared:
        return declared
    try:
        age = int(character.get("age"))
    except (TypeError, ValueError):
        return "unknown"
    if age <= 12:
        return "child"
    if age <= 17:
        return "teen"
    if age <= 59:
        return "adult"
    return "senior"


def validate_report(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    report = json.loads(path.read_text(encoding="utf-8"))
    registry = report.get("character_registry") or {}
    route_cast = {
        str(value).strip()
        for value in ((report.get("route_context") or {}).get("route_cast") or [])
        if str(value).strip()
    }
    rows = list(report.get("kept") or [])
    issues: list[dict[str, Any]] = []

    source_video = Path(str(report.get("source_video") or ""))
    if not source_video.is_file():
        issues.append({"type": "missing_video", "episode": report.get("title"), "path": str(source_video)})

    source_vtt = Path(str(report.get("source_vtt") or ""))
    source_vtt_cues: list[tuple[float, float]] = []
    named_vtt = path.with_name(path.name.replace(".segments.json", ".vtt"))
    if not source_vtt.is_file():
        issues.append({"type": "missing_source_vtt", "episode": report.get("title"), "path": str(source_vtt)})
    else:
        source_vtt_cues, source_vtt_issues = parse_vtt_cues(source_vtt)
        issues.extend({**item, "episode": report.get("title")} for item in source_vtt_issues)
        if len(source_vtt_cues) != len(rows):
            issues.append(
                {
                    "type": "vtt_count_mismatch",
                    "episode": report.get("title"),
                    "vtt_cues": len(source_vtt_cues),
                    "report_cues": len(rows),
                }
            )

    named_vtt_cues: list[tuple[float, float]] = []
    if not named_vtt.is_file():
        issues.append({"type": "missing_named_vtt", "episode": report.get("title"), "path": str(named_vtt)})
    else:
        named_vtt_text = named_vtt.read_text(encoding="utf-8-sig")
        named_vtt_cues, named_vtt_issues = parse_vtt_cues(named_vtt)
        issues.extend({**item, "episode": report.get("title")} for item in named_vtt_issues)
        if "NOTE speaker-labels=false" not in named_vtt_text:
            issues.append({"type": "speaker_labels_not_disabled", "episode": report.get("title"), "path": str(named_vtt)})
        if re.search(r"<v\s+", named_vtt_text, flags=re.IGNORECASE):
            issues.append({"type": "visible_vtt_voice_tag", "episode": report.get("title"), "path": str(named_vtt)})
        if len(named_vtt_cues) != len(rows):
            issues.append(
                {
                    "type": "named_vtt_count_mismatch",
                    "episode": report.get("title"),
                    "vtt_cues": len(named_vtt_cues),
                    "report_cues": len(rows),
                }
            )
        elif source_vtt_cues and named_vtt_cues:
            for index, (source_cue, named_cue) in enumerate(zip(source_vtt_cues, named_vtt_cues), start=1):
                if any(abs(left - right) > TIMESTAMP_EPSILON for left, right in zip(source_cue, named_cue)):
                    issues.append(
                        {
                            "type": "named_vtt_timestamp_mismatch",
                            "episode": report.get("title"),
                            "cue": index,
                            "source": source_cue,
                            "named": named_cue,
                        }
                    )

    video_duration, duration_error = (None, None)
    if source_video.is_file():
        video_duration, duration_error = probe_duration(source_video)
        if duration_error:
            issues.append(
                {
                    "type": "video_duration_unchecked",
                    "episode": report.get("title"),
                    "path": str(source_video),
                    "reason": duration_error,
                }
            )

    field_nulls: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    character_counts: Counter[str] = Counter()
    outside_route_counts: Counter[str] = Counter()
    review_count = 0
    max_report_end: float | None = None
    previous_start: float | None = None
    prior_intervals: list[tuple[float, float]] = []
    intentional_overlap_count = 0

    for index, row in enumerate(rows, start=1):
        episode = str(report.get("title") or "")
        cue = row.get("cue")
        try:
            cue_number = int(cue)
        except (TypeError, ValueError):
            cue_number = None
        if cue_number != index:
            issues.append({"type": "cue_number_mismatch", "episode": episode, "cue": cue, "expected": index})

        start = finite_number(row.get("start"))
        end = finite_number(row.get("end"))
        if start is None or end is None or start < 0 or end <= start:
            issues.append(
                {
                    "type": "invalid_report_timestamp",
                    "episode": episode,
                    "cue": cue,
                    "start": row.get("start"),
                    "end": row.get("end"),
                }
            )
        else:
            expected_timestamp = format_interval(start, end)
            if row.get("timestamp") != expected_timestamp:
                issues.append(
                    {
                        "type": "report_timestamp_field_mismatch",
                        "episode": episode,
                        "cue": cue,
                        "report_timestamp": row.get("timestamp"),
                        "expected_timestamp": expected_timestamp,
                    }
                )
            max_report_end = end if max_report_end is None else max(max_report_end, end)
            if previous_start is not None and start + TIMESTAMP_EPSILON < previous_start:
                if has_prior_overlap(prior_intervals, start, end):
                    intentional_overlap_count += 1
                else:
                    issues.append(
                        {
                            "type": "non_monotonic_report_timestamp",
                            "episode": episode,
                            "cue": cue,
                            "previous_start": previous_start,
                            "start": start,
                        }
                    )
            previous_start = start
            prior_intervals.append((start, end))
            if video_duration is not None and end > video_duration + TIMESTAMP_EPSILON:
                issues.append(
                    {
                        "type": "timestamp_after_video_end",
                        "episode": episode,
                        "cue": cue,
                        "end": end,
                        "video_duration": video_duration,
                    }
                )
            if index <= len(source_vtt_cues):
                source_start, source_end = source_vtt_cues[index - 1]
                if abs(start - source_start) > TIMESTAMP_EPSILON or abs(end - source_end) > TIMESTAMP_EPSILON:
                    issues.append(
                        {
                            "type": "report_vtt_timestamp_mismatch",
                            "episode": episode,
                            "cue": cue,
                            "report": [start, end],
                            "source_vtt": [source_start, source_end],
                        }
                    )
        for field in REQUIRED_FIELDS:
            if field not in row:
                issues.append({"type": "missing_field", "episode": episode, "cue": cue, "field": field})
            elif row.get(field) is None and field != "character_age":
                field_nulls[field] += 1

        character_id = str(row.get("character_id") or "").strip()
        character_counts[character_id or "unknown"] += 1
        if route_cast and character_id not in route_cast:
            outside_route_counts[character_id or "unknown"] += 1
            issues.append(
                {
                    "type": "character_outside_route_cast",
                    "episode": episode,
                    "cue": cue,
                    "character_id": character_id,
                    "allowed_character_ids": sorted(route_cast),
                }
            )
        character = registry.get(character_id)
        if not character:
            issues.append({"type": "unknown_character_id", "episode": episode, "cue": cue, "character_id": character_id})
        else:
            expected_name = character.get("display_name") or character_id
            if row.get("character_name") != expected_name:
                issues.append({"type": "metadata_mismatch", "episode": episode, "cue": cue, "field": "character_name"})
            expected_band = inferred_age_band(character)
            if row.get("character_age_band") != expected_band:
                issues.append({"type": "metadata_mismatch", "episode": episode, "cue": cue, "field": "character_age_band"})
            if row.get("character_gender") != (character.get("gender") or "unknown"):
                issues.append({"type": "metadata_mismatch", "episode": episode, "cue": cue, "field": "character_gender"})
            if row.get("character_role") != character.get("role"):
                issues.append({"type": "metadata_mismatch", "episode": episode, "cue": cue, "field": "character_role"})

        try:
            score = float(row.get("candidate_score"))
            margin = float(row.get("candidate_margin"))
            if not (-1.0 <= score <= 1.0):
                issues.append({"type": "score_out_of_range", "episode": episode, "cue": cue, "value": score})
            if not (-2.0 <= margin <= 2.0):
                issues.append({"type": "margin_out_of_range", "episode": episode, "cue": cue, "value": margin})
        except (TypeError, ValueError):
            issues.append({"type": "invalid_score_or_margin", "episode": episode, "cue": cue})

        if not isinstance(row.get("alternatives"), list):
            issues.append({"type": "alternatives_not_list", "episode": episode, "cue": cue})
        status_counts[str(row.get("match_status") or "unknown")] += 1
        if row.get("needs_review"):
            review_count += 1

    summary = {
        "episode": str(report.get("title") or "").rsplit("-", 1)[-1],
        "report": str(path.resolve()),
        "source_video": str(source_video),
        "source_video_exists": source_video.is_file(),
        "source_vtt": str(source_vtt),
        "source_vtt_exists": source_vtt.is_file(),
        "named_vtt": str(named_vtt),
        "named_vtt_exists": named_vtt.is_file(),
        "cues": len(rows),
        "source_vtt_cues": len(source_vtt_cues),
        "named_vtt_cues": len(named_vtt_cues),
        "video_duration_seconds": video_duration,
        "max_report_end_seconds": max_report_end,
        "timestamp_check": {
            "duration_checked": video_duration is not None,
            "report_matches_source_vtt": not any(
                item.get("type") == "report_vtt_timestamp_mismatch" for item in issues
            ),
            "report_within_video": not any(
                item.get("type") == "timestamp_after_video_end" for item in issues
            ),
            "named_vtt_matches_source_vtt": not any(
                item.get("type") == "named_vtt_timestamp_mismatch" for item in issues
            ),
            "intentional_overlapping_cues_preserved": intentional_overlap_count,
        },
        "needs_review": review_count,
        "character_counts": dict(character_counts),
        "outside_route_counts": dict(outside_route_counts),
        "route_cast": sorted(route_cast),
        "match_statuses": dict(status_counts),
        "field_nulls": dict(field_nulls),
    }
    return summary, issues


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "episode",
        "cue",
        "start",
        "end",
        "timestamp",
        "scene",
        "character_id",
        "character_name",
        "character_age",
        "character_age_band",
        "character_gender",
        "character_role",
        "candidate_score",
        "candidate_margin",
        "candidate_confidence",
        "machine_candidate_id",
        "machine_candidate_name",
        "alternatives",
        "match_status",
        "needs_review",
        "text",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            data = dict(row)
            data["episode"] = data.get("id", "").split("-")[1] if data.get("id") else ""
            data["timestamp"] = data.get("timestamp") or format_interval(data.get("start"), data.get("end"))
            data["alternatives"] = " | ".join(
                f"{item.get('character_id')}:{item.get('score')}" for item in row.get("alternatives") or []
            )
            writer.writerow(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--episodes", nargs="+", default=[f"{number:02d}" for number in range(2, 13)])
    parser.add_argument("--csv-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    args = parser.parse_args()

    summaries: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for raw_episode in args.episodes:
        episode = str(raw_episode).zfill(2)
        path = args.reports_dir.resolve() / f"yosuga-no-sora-{episode}.vi.named.segments.json"
        if not path.is_file():
            issues.append({"type": "missing_report", "episode": episode, "path": str(path)})
            continue
        summary, report_issues = validate_report(path)
        summaries.append(summary)
        issues.extend(report_issues)
        report = json.loads(path.read_text(encoding="utf-8"))
        all_rows.extend(report.get("kept") or [])

    output = {
        "schema_version": 1,
        "title": "Yosuga no Sora character check episodes 02-12",
        "source": "local video + named per-cue reports",
        "episodes": summaries,
        "summary": {
            "episodes": len(summaries),
            "cues": sum(item["cues"] for item in summaries),
            "needs_review": sum(item["needs_review"] for item in summaries),
            "issues": len(issues),
            "ready_for_web": not issues and not any(item["needs_review"] for item in summaries),
        },
        "issues": issues,
    }
    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    args.summary_output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(args.csv_output.resolve(), all_rows)
    print(f"EPISODES={len(summaries)}")
    print(f"CUES={output['summary']['cues']}")
    print(f"NEEDS_REVIEW={output['summary']['needs_review']}")
    print(f"ISSUES={len(issues)}")
    print(f"CSV={args.csv_output.resolve()}")
    print(f"SUMMARY={args.summary_output.resolve()}")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
