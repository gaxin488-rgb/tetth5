#!/usr/bin/env python3
"""Convert an embedded English ASS subtitle track into Vietnamese WebVTT.

This uses the free Argos Translate model locally. Only Default dialogue styles
are kept, so opening/ending lyrics and sign/effect events are not subtitled.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ASS_DIALOGUE_PREFIX = "Dialogue:"
ASS_TAG_RE = re.compile(r"\{[^}]*\}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--subtitle-stream", default="0:s:0")
    return parser.parse_args()


def ass_time_to_seconds(value: str) -> float:
    hours, minutes, seconds = value.strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def seconds_to_vtt(value: float) -> str:
    milliseconds = max(0, int(round(value * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def clean_ass_text(value: str) -> str:
    value = ASS_TAG_RE.sub("", value)
    value = value.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    return value.strip()


def extract_ass(input_path: Path, stream: str, directory: Path) -> Path:
    output_path = directory / "source.ass"
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(input_path),
        "-map",
        stream,
        "-c:s",
        "copy",
        str(output_path),
    ]
    subprocess.run(command, check=True)
    return output_path


def parse_ass(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    total_dialogue = 0
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith(ASS_DIALOGUE_PREFIX):
            continue
        total_dialogue += 1
        fields = line[len(ASS_DIALOGUE_PREFIX) :].lstrip().split(",", 9)
        if len(fields) < 10:
            continue
        start = ass_time_to_seconds(fields[1])
        end = ass_time_to_seconds(fields[2])
        style = fields[3].strip()
        text = clean_ass_text(fields[9])
        if end <= start or not text or not style.lower().startswith("default"):
            continue
        events.append({"start": start, "end": end, "style": style, "text": text})
    return events, total_dialogue


def get_translator() -> Any:
    try:
        import argostranslate.translate as translate
    except ImportError as exc:
        raise RuntimeError(
            "Thiếu Argos Translate. Cài bằng: python -m pip install argostranslate"
        ) from exc
    installed = translate.get_installed_languages()
    source = next((language for language in installed if language.code == "en"), None)
    target = next((language for language in installed if language.code == "vi"), None)
    if source is None or target is None:
        raise RuntimeError("Chưa cài model Argos English -> Vietnamese")
    return translate


def translate_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    translate = get_translator()
    cache: dict[str, str] = {}
    translated: list[dict[str, Any]] = []
    for index, event in enumerate(events, start=1):
        source_text = event["text"].replace("\n", " ").strip()
        if source_text not in cache:
            cache[source_text] = str(translate.translate(source_text, "en", "vi")).strip()
        target_text = cache[source_text]
        if not target_text:
            continue
        translated.append({**event, "text": target_text})
        if index % 50 == 0:
            print(f"TRANSLATED_EVENTS={index}", flush=True)
    return translated, len(cache)


def write_vtt(path: Path, events: list[dict[str, Any]]) -> None:
    lines = [
        "WEBVTT",
        "",
        "NOTE CineZero Vietnamese subtitle",
        "NOTE engine=embedded-ASS+ArgosTranslate-local",
        "NOTE source-language=en",
        "NOTE translated-language=vi",
        "NOTE speaker-labels=false",
        "",
    ]
    seen: set[tuple[int, int, str]] = set()
    cue_number = 1
    for event in events:
        key = (round(event["start"] * 1000), round(event["end"] * 1000), event["text"])
        if key in seen:
            continue
        seen.add(key)
        text = html.escape(event["text"], quote=False)
        lines.extend(
            [
                str(cue_number),
                f"{seconds_to_vtt(event['start'])} --> {seconds_to_vtt(event['end'])}",
                text,
                "",
            ]
        )
        cue_number += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_path = args.output.expanduser().resolve()
    report_path = (args.report or output_path.with_suffix(".segments.json")).expanduser().resolve()
    if not input_path.is_file():
        raise RuntimeError(f"Không tìm thấy input: {input_path}")

    with tempfile.TemporaryDirectory(prefix="cinezero-ass-") as temp_dir:
        ass_path = extract_ass(input_path, args.subtitle_stream, Path(temp_dir))
        events, total_dialogue = parse_ass(ass_path)

    translated, unique_texts = translate_events(events)
    write_vtt(output_path, translated)
    report = {
        "input": str(input_path),
        "language": "vi",
        "source_language": "en",
        "engine": "embedded-ASS+ArgosTranslate-local",
        "subtitle_stream": args.subtitle_stream,
        "source_dialogue_events": total_dialogue,
        "kept_dialogue_events": len(events),
        "translated_events": len(translated),
        "unique_texts_translated": unique_texts,
        "filtered_non_dialogue_events": total_dialogue - len(events),
        "speaker_labels": False,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"SOURCE_DIALOGUE={total_dialogue}")
    print(f"KEPT_DIALOGUE={len(events)}")
    print(f"TRANSLATED_CUES={len(translated)}")
    print(f"UNIQUE_TEXTS={unique_texts}")
    print(f"VTT_OUTPUT={output_path}")
    print(f"REPORT_OUTPUT={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
