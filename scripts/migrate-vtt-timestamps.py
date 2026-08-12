#!/usr/bin/env python3
"""Add explicit WebVTT timing lines to existing report artifacts."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from pathlib import Path
from typing import Any, Callable

from vtt_time import format_interval


SCRIPT_DIR = Path(__file__).resolve().parent


def load_function(filename: str, name: str) -> Callable[..., Any]:
    spec = importlib.util.spec_from_file_location(name, SCRIPT_DIR / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


def interval(row: dict[str, Any]) -> str:
    return str(row.get("timestamp") or format_interval(row.get("start"), row.get("end")))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def migrate_named_reports(reports_dir: Path) -> int:
    changed = 0
    for path in sorted(reports_dir.glob("*.vi.named.segments.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        dirty = False
        for row in data.get("kept") or []:
            value = interval(row)
            if row.get("timestamp") != value:
                row["timestamp"] = value
                dirty = True
        if dirty:
            write_json(path, data)
            changed += 1
    return changed


def rewrite_evidence_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "episode", "cue", "start", "end", "timestamp", "character_id", "character_name",
        "match_status", "needs_review", "candidate_score", "candidate_margin", "research_query",
        "frames", "text",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for item in rows:
            row = dict(item)
            row["timestamp"] = interval(row)
            row["frames"] = " | ".join(str(frame.get("path") or "") for frame in item.get("frames") or [])
            writer.writerow(row)


def migrate_evidence(evidence_root: Path) -> tuple[int, int]:
    write_review_html = load_function("build-video-evidence.py", "write_review_html")
    index_count = 0
    cue_count = 0
    index_paths = sorted(evidence_root.glob("episode-*-pack/evidence-index.json"))
    direct_index = evidence_root / "evidence-index.json"
    if direct_index.is_file():
        index_paths.insert(0, direct_index)
    for index_path in index_paths:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        rows = list(data.get("evidence") or [])
        for row in rows:
            row["timestamp"] = interval(row)
        data["evidence"] = rows
        write_json(index_path, data)
        mapping_path = Path(str(data.get("mapping") or ""))
        mapping = json.loads(mapping_path.read_text(encoding="utf-8")) if mapping_path.is_file() else {"title": "CineZero"}
        rewrite_evidence_csv(index_path.with_name("evidence.csv"), rows)
        write_review_html(index_path.with_name("review.html"), rows, mapping)
        index_count += 1
        cue_count += len(rows)
    return index_count, cue_count


def migrate_story(story_dir: Path) -> bool:
    json_path = story_dir / "story-context-diagnosis.json"
    if not json_path.is_file():
        return False
    data = json.loads(json_path.read_text(encoding="utf-8"))
    rows = list(data.get("cues") or [])
    for row in rows:
        row["timestamp"] = interval(row)
    data["cues"] = rows
    write_json(json_path, data)
    csv_path = story_dir / "story-context-diagnosis.csv"
    if csv_path.is_file():
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
        columns = list(csv_rows[0].keys()) if csv_rows else ["episode", "cue", "start", "end", "timestamp"]
        if "timestamp" not in columns:
            columns.insert(columns.index("end") + 1, "timestamp")
        by_key = {(str(row.get("episode")), str(row.get("cue"))): row for row in rows}
        for row in csv_rows:
            row["timestamp"] = interval(by_key.get((str(row.get("episode")), str(row.get("cue"))), row))
        with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(csv_rows)
    write_html = load_function("diagnose-story-context.py", "write_html")
    write_html(story_dir / "story-context-review.html", rows, str(data.get("title") or "Story context diagnosis"))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path)
    parser.add_argument("--story-dir", type=Path)
    args = parser.parse_args()
    report_count = migrate_named_reports(args.reports_dir.resolve())
    evidence_indexes = evidence_cues = 0
    if args.evidence_root:
        evidence_indexes, evidence_cues = migrate_evidence(args.evidence_root.resolve())
    story_updated = migrate_story(args.story_dir.resolve()) if args.story_dir else False
    print(f"REPORTS_UPDATED={report_count}")
    print(f"EVIDENCE_INDEXES_UPDATED={evidence_indexes}")
    print(f"EVIDENCE_CUES_UPDATED={evidence_cues}")
    print(f"STORY_REPORT_UPDATED={str(story_updated).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
