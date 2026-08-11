#!/usr/bin/env python3
"""Build the small character mapping payload consumed by the API and player.

The Vietnamese VTT remains presentation-only.  This payload keeps the
character identity, metadata, confidence and cue time range beside the VTT so
the Worker and browser can use the mapping without adding names to subtitle
text or uploading the large local reports.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def age_band(character: dict[str, Any]) -> str:
    value = str(character.get("age_band") or "").strip()
    if value:
        return value
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


def public_character(character_id: str, character: dict[str, Any]) -> dict[str, Any]:
    return {
        "character_id": character_id,
        "name": character.get("display_name") or character.get("name") or character_id,
        "aliases": list(character.get("aliases") or []),
        "age": character.get("age"),
        "age_band": age_band(character),
        "gender": character.get("gender") or "unknown",
        "role": character.get("role") or "unknown",
        "mapping_status": character.get("mapping_status") or "review_required",
    }


def public_cue(row: dict[str, Any]) -> dict[str, Any]:
    alternatives = []
    for item in row.get("alternatives") or []:
        if not isinstance(item, dict):
            continue
        alternatives.append(
            {
                "character_id": item.get("character_id"),
                "name": item.get("character_name") or item.get("name"),
                "score": item.get("score"),
            }
        )
    return {
        "cue": int(row.get("cue") or 0),
        "start": float(row.get("start") or 0),
        "end": float(row.get("end") or 0),
        "character_id": row.get("character_id"),
        "character_name": row.get("character_name") or row.get("speaker_label") or "Người nói",
        "speaker_label": row.get("speaker_label") or row.get("character_name") or "Người nói",
        "candidate_confidence": row.get("candidate_confidence") or "unknown",
        "match_status": row.get("match_status") or "unknown",
        "needs_review": bool(row.get("needs_review")),
        "candidate_score": row.get("candidate_score"),
        "candidate_margin": row.get("candidate_margin"),
        "alternatives": alternatives[:4],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--character-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--slug", default="yosuga-no-sora")
    parser.add_argument("--title", default="Yosuga no Sora")
    args = parser.parse_args()

    profile = json.loads(args.character_profile.resolve().read_text(encoding="utf-8"))
    registry = profile.get("characters") or {}
    characters = {
        character_id: public_character(character_id, character)
        for character_id, character in sorted(registry.items())
        if isinstance(character, dict)
    }
    episodes: dict[str, dict[str, Any]] = {}
    for report_path in sorted(args.reports_dir.resolve().glob("*.named.segments.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        title = str(report.get("title") or "")
        episode = title[-2:] if title[-2:].isdigit() else ""
        if not episode:
            continue
        rows = [public_cue(row) for row in report.get("kept") or []]
        for row in report.get("kept") or []:
            character_id = str(row.get("character_id") or "").strip()
            if character_id and character_id not in characters:
                characters[character_id] = public_character(
                    character_id,
                    {
                        "display_name": row.get("character_name") or character_id,
                        "age": row.get("character_age"),
                        "age_band": row.get("character_age_band"),
                        "gender": row.get("character_gender"),
                        "role": row.get("character_role"),
                    },
                )
        episodes[episode] = {
            "episode_number": int(episode),
            "title": f"Tập {episode}",
            "cue_count": len(rows),
            "needs_review": sum(int(row["needs_review"]) for row in rows),
            "cues": rows,
        }

    payload = {
        "schema_version": 1,
        "slug": args.slug,
        "title": args.title,
        "mapping_status": "review_required",
        "note": "Character names are delivered separately from the clean Vietnamese VTT.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "characters": characters,
        "episodes": dict(sorted(episodes.items())),
        "summary": {
            "episodes": len(episodes),
            "cues": sum(item["cue_count"] for item in episodes.values()),
            "needs_review": sum(item["needs_review"] for item in episodes.values()),
        },
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"EPISODES={payload['summary']['episodes']}")
    print(f"CUES={payload['summary']['cues']}")
    print(f"NEEDS_REVIEW={payload['summary']['needs_review']}")
    print(f"OUTPUT={output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
