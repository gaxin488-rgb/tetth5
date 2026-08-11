#!/usr/bin/env python3
"""Rebuild a Vietnamese VTT from a newer named-speaker report.

The existing Vietnamese translation is reused only when its timestamps match
the newer report. Unmatched ASR fragments are skipped instead of being shown
as guessed subtitles. The resulting cues use the character/speaker label from
the newer report.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


TIME_RE = re.compile(r"^\s*([0-9:.]+)\s*-->\s*([0-9:.]+)")


def timestamp_to_seconds(value: str) -> float:
    parts = value.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Invalid VTT timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def seconds_to_timestamp(value: float) -> str:
    milliseconds = max(0, int(round(value * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def parse_vtt(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8-sig")
    cues: list[dict[str, Any]] = []

    for block in re.split(r"\r?\n\s*\r?\n", content.strip()):
        lines = block.splitlines()
        time_index = next(
            (index for index, line in enumerate(lines) if "-->" in line), None
        )
        if time_index is None:
            continue

        match = TIME_RE.match(lines[time_index])
        if not match:
            continue

        caption = "\n".join(line.strip() for line in lines[time_index + 1 :]).strip()
        if not caption:
            continue

        cues.append(
            {
                "start": timestamp_to_seconds(match.group(1)),
                "end": timestamp_to_seconds(match.group(2)),
                "text": caption,
            }
        )

    return cues


def choose_label(segment: dict[str, Any]) -> str:
    for key in ("character_name", "speaker_label", "speaker"):
        value = str(segment.get(key) or "").strip()
        if value and not value.lower().startswith("người nói"):
            return value
    return str(segment.get("speaker_label") or segment.get("speaker") or "Người nói").strip()


def clean_source_caption(value: str) -> str:
    """Accept both the original unlabeled VTT and an already labeled VTT."""
    value = re.sub(r"^\s*<v\s+[^>]+>\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"^\s*\[[^\]]+\]\s*", "", value)
    return value.strip()


def build_vtt(
    report_path: Path,
    source_vtt_path: Path,
    output_path: Path,
    tolerance: float,
    show_speakers: bool,
) -> tuple[int, int, int]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report_cues = list(report.get("kept") or [])
    source_cues = parse_vtt(source_vtt_path)

    source_by_time = {
        (round(cue["start"], 3), round(cue["end"], 3)): cue for cue in source_cues
    }
    output_cues: list[dict[str, Any]] = []
    skipped = 0

    for segment in report_cues:
        start = float(segment["start"])
        end = float(segment["end"])
        source = source_by_time.get((round(start, 3), round(end, 3)))
        if source is None:
            source = next(
                (
                    cue
                    for cue in source_cues
                    if abs(cue["start"] - start) <= tolerance
                    and abs(cue["end"] - end) <= tolerance
                ),
                None,
            )

        source_text = clean_source_caption(source["text"]) if source else ""
        if source is None or not source_text:
            skipped += 1
            continue

        caption = html.escape(source_text.replace("\n", " ").strip(), quote=False)
        if show_speakers:
            label = html.escape(choose_label(segment), quote=True)
            caption = f"<v {label}>[{label}] {caption}"
        output_cues.append(
            {
                "start": start,
                "end": end,
                "text": caption,
            }
        )

    if not output_cues:
        raise RuntimeError("No Vietnamese cue matched the new speaker report")

    lines = [
        "WEBVTT",
        "",
        "NOTE CineZero Vietnamese subtitle",
        "NOTE engine=aligned-existing-translation",
        "NOTE source-language=ja",
        "NOTE translated-language=vi",
        f"NOTE speaker-labels={'true' if show_speakers else 'false'}",
        "",
    ]
    for index, cue in enumerate(output_cues, start=1):
        lines.extend(
            [
                str(index),
                f"{seconds_to_timestamp(cue['start'])} --> {seconds_to_timestamp(cue['end'])}",
                cue["text"],
                "",
            ]
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return len(report_cues), len(source_cues), skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source-vtt", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tolerance", type=float, default=0.01)
    parser.add_argument(
        "--show-speakers",
        action="store_true",
        help="Include WebVTT voice tags and visible speaker names (debug only)",
    )
    args = parser.parse_args()

    report_count, source_count, skipped = build_vtt(
        args.report,
        args.source_vtt,
        args.output,
        args.tolerance,
        args.show_speakers,
    )
    print(f"REPORT_CUES={report_count}")
    print(f"SOURCE_CUES={source_count}")
    print(f"OUTPUT_CUES={report_count - skipped}")
    print(f"SKIPPED_UNMATCHED={skipped}")
    print(f"VTT_OUTPUT={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
