#!/usr/bin/env python3
"""Apply exported, four-check cue review decisions to named reports.

The dashboard is intentionally separate from report mutation. This command
accepts only decisions that contain all four evidence checks. Confirmed rows
are promoted to ``manual_audio_video_confirmed``; unresolved rows remain in
``needs_review`` but receive an auditable manual-review record.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_CHECKS = ("timestamp", "video_audio", "frame", "candidates")


def episode_from_report(report: dict[str, Any], path: Path) -> str:
    title = str(report.get("title") or "")
    if title[-2:].isdigit():
        return title[-2:]
    stem = path.name
    for token in stem.split("-"):
        if token[:2].isdigit() and len(token) >= 2:
            return token[:2]
    return ""


def metadata(character_id: str, registry: dict[str, Any]) -> dict[str, Any]:
    character = registry.get(character_id) or {}
    age = character.get("age")
    age_band = character.get("age_band")
    if not age_band:
        try:
            value = int(age)
        except (TypeError, ValueError):
            age_band = "unknown"
        else:
            age_band = "child" if value <= 12 else "teen" if value <= 17 else "adult" if value <= 59 else "senior"
    return {
        "character_name": character.get("display_name") or character_id,
        "character_age": age,
        "character_age_band": age_band,
        "character_gender": character.get("gender") or "unknown",
        "character_role": character.get("role") or "unknown",
        "speaker_label": character.get("display_name") or character_id,
    }


def checks_valid(decision: dict[str, Any]) -> bool:
    checks = decision.get("checks") or {}
    return all(bool(checks.get(key)) for key in REQUIRED_CHECKS)


def normalize_decisions(raw: Any) -> dict[tuple[str, int], dict[str, Any]]:
    if not isinstance(raw, dict):
        raise ValueError("Decision JSON must be an object keyed by episode:cue")
    normalized: dict[tuple[str, int], dict[str, Any]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        parts = str(key).split(":", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        normalized[(parts[0].zfill(2), int(parts[1]))] = value
    return normalized


def summarize(rows: list[dict[str, Any]], episode: str) -> dict[str, Any]:
    statuses: Counter[str] = Counter(str(row.get("match_status") or "unknown") for row in rows)
    characters: Counter[str] = Counter(str(row.get("character_id") or "unknown") for row in rows)
    return {
        "episode": episode,
        "cues": len(rows),
        "named_working_labels": sum(1 for row in rows if row.get("character_id")),
        "needs_review": sum(int(bool(row.get("needs_review"))) for row in rows),
        "match_statuses": dict(statuses),
        "character_counts": dict(characters),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Write reports; without this flag only validate")
    args = parser.parse_args()

    decisions_path = args.decisions.resolve()
    decisions = normalize_decisions(json.loads(decisions_path.read_text(encoding="utf-8")))
    reports = {}
    for path in sorted(args.reports_dir.resolve().glob("*.named.segments.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        episode = episode_from_report(report, path)
        if episode:
            reports[episode] = (path, report)
    if not reports:
        raise SystemExit("No named report found")

    counts: Counter[str] = Counter()
    errors: list[str] = []
    now = datetime.now(timezone.utc).isoformat()
    changed_reports: list[tuple[Path, dict[str, Any]]] = []

    for (episode, cue_number), decision in sorted(decisions.items()):
        path_report = reports.get(episode)
        if not path_report:
            errors.append(f"{episode}:{cue_number}: episode report not found")
            continue
        path, report = path_report
        rows = list(report.get("kept") or [])
        row = next((item for item in rows if int(item.get("cue") or 0) == cue_number), None)
        if row is None:
            errors.append(f"{episode}:{cue_number}: cue not found")
            continue

        result = str(decision.get("decision") or "").strip()
        if result not in {"confirmed", "alternative", "unresolved"}:
            counts["skipped_empty"] += 1
            continue
        if result in {"confirmed", "alternative"} and not checks_valid(decision):
            errors.append(f"{episode}:{cue_number}: confirmation is missing one of the four checks")
            continue

        previous_id = str(row.get("character_id") or "").strip()
        target_id = previous_id
        if result == "alternative":
            target_id = str(decision.get("alternative_id") or decision.get("character_id") or "").strip()
        elif str(decision.get("character_id") or "").strip():
            target_id = str(decision.get("character_id")).strip()

        registry = report.get("character_registry") or {}
        route_cast = {
            str(value).strip()
            for value in ((report.get("route_context") or {}).get("route_cast") or [])
            if str(value).strip()
        }
        if result in {"confirmed", "alternative"}:
            if not target_id or target_id not in registry:
                errors.append(f"{episode}:{cue_number}: target character_id is not in registry: {target_id or '<empty>'}")
                continue
            if route_cast and target_id not in route_cast:
                errors.append(f"{episode}:{cue_number}: target character_id is outside route cast: {target_id}")
                continue
            row.update(metadata(target_id, registry))
            row["character_id"] = target_id
            row["match_status"] = "manual_audio_video_confirmed"
            row["needs_review"] = False
            evidence = list(row.get("evidence") or [])
            row["evidence"] = list(dict.fromkeys(evidence + ["manual_audio_video_confirmed"]))
            counts["confirmed"] += 1
        else:
            row["match_status"] = "manual_audio_video_unresolved"
            row["needs_review"] = True
            counts["unresolved"] += 1

        row["manual_review"] = {
            "status": "confirmed" if result in {"confirmed", "alternative"} else "unresolved",
            "previous_character_id": previous_id,
            "character_id": target_id or None,
            "decision_source": str(decisions_path),
            "checks": decision.get("checks") or {},
            "note": str(decision.get("note") or "").strip(),
            "reviewed_at": str(decision.get("saved_at") or now),
        }
        changed_reports.append((path, report))

    if errors:
        print("VALIDATION_ERRORS=" + str(len(errors)))
        for error in errors[:50]:
            print("ERROR=" + error)
        if len(errors) > 50:
            print("ERRORS_TRUNCATED=" + str(len(errors) - 50))
        return 1

    unique_reports = {str(path): report for path, report in changed_reports}
    for path_string, report in unique_reports.items():
        path = Path(path_string)
        episode = episode_from_report(report, path)
        rows = list(report.get("kept") or [])
        report["engine"] = f"{report.get('engine', 'named-report')} + manual-audio-video-review"
        report["batch_confirmation"] = {
            **(report.get("batch_confirmation") or {}),
            "manual_review_decision_source": str(decisions_path),
            "manual_review_confirmed": sum(1 for row in rows if row.get("match_status") == "manual_audio_video_confirmed"),
            "manual_review_unresolved": sum(1 for row in rows if row.get("match_status") == "manual_audio_video_unresolved"),
            "remaining_needs_review": sum(int(bool(row.get("needs_review"))) for row in rows),
        }
        report["summary"] = summarize(rows, episode)
        if args.apply:
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("DECISIONS=" + str(len(decisions)))
    print("CONFIRMED=" + str(counts["confirmed"]))
    print("UNRESOLVED=" + str(counts["unresolved"]))
    print("SKIPPED_EMPTY=" + str(counts["skipped_empty"]))
    print("REPORTS_CHANGED=" + str(len(unique_reports)))
    print("APPLIED=" + str(bool(args.apply)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
