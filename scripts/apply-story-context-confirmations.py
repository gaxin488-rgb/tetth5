#!/usr/bin/env python3
"""Apply conservative, auditable batch confirmations to named reports.

The story-context diagnosis is intentionally a separate review step. This
command promotes only ``context_supported`` recommendations and, optionally,
the report's own high voice-confidence candidates. Every promoted cue keeps
the original machine candidate/score and receives a batch-confirmation record.
Ambiguous and weak-context cues remain in ``needs_review``.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


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


def metadata(character_id: str, registry: dict[str, Any]) -> dict[str, Any]:
    character = registry.get(character_id) or {}
    return {
        "character_name": character.get("display_name") or character_id,
        "character_age": character.get("age"),
        "character_age_band": inferred_age_band(character),
        "character_gender": character.get("gender") or "unknown",
        "character_role": character.get("role"),
    }


def summarize(rows: list[dict[str, Any]], episode: str) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    characters: Counter[str] = Counter()
    needs_review = 0
    for row in rows:
        statuses[str(row.get("match_status") or "unknown")] += 1
        characters[str(row.get("character_id") or "unknown")] += 1
        needs_review += int(bool(row.get("needs_review")))
    return {
        "episode": episode,
        "cues": len(rows),
        "named_working_labels": sum(1 for row in rows if row.get("character_id")),
        "needs_review": needs_review,
        "match_statuses": dict(statuses),
        "character_counts": dict(characters),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--episodes", nargs="+", default=[])
    parser.add_argument("--min-context-margin", type=float, default=0.12)
    parser.add_argument("--include-high-voice", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    diagnosis = json.loads(args.diagnosis.resolve().read_text(encoding="utf-8"))
    diagnosis_by_key = {
        (str(item.get("episode")).zfill(2), int(item.get("cue"))): item
        for item in diagnosis.get("cues") or []
    }
    requested = {str(value).zfill(2) for value in args.episodes}
    reports = sorted(args.reports_dir.resolve().glob("*.named.segments.json"))
    if requested:
        reports = [
            path
            for path in reports
            if str(json.loads(path.read_text(encoding="utf-8")).get("title") or "")[-2:] in requested
        ]
    if not reports:
        raise SystemExit("Không tìm thấy named report phù hợp")

    total_context = 0
    total_voice = 0
    total_changes = 0

    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        episode = str(report.get("title") or "")[-2:]
        registry = report.get("character_registry") or {}
        route_cast = {
            str(value).strip()
            for value in ((report.get("route_context") or {}).get("route_cast") or [])
            if str(value).strip()
        }
        rows = list(report.get("kept") or [])
        episode_context = 0
        episode_voice = 0
        episode_changes = 0
        for row in rows:
            try:
                cue = int(row.get("cue"))
            except (TypeError, ValueError):
                continue
            diagnosis_row = diagnosis_by_key.get((episode, cue))
            decision: dict[str, Any] | None = None
            if diagnosis_row:
                recommendation = str(diagnosis_row.get("recommended_character_id") or "").strip()
                context_margin = float(diagnosis_row.get("context_margin") or 0.0)
                if (
                    diagnosis_row.get("recommendation_status") == "context_supported"
                    and context_margin >= args.min_context_margin
                    and recommendation in registry
                    and (not route_cast or recommendation in route_cast)
                ):
                    decision = {
                        "status": "auto_context_confirmed",
                        "character_id": recommendation,
                        "context_margin": context_margin,
                        "reasons": list(diagnosis_row.get("evidence") or []),
                        "source": args.diagnosis.resolve().name,
                    }
            if decision is None and args.include_high_voice:
                machine_id = str(row.get("machine_candidate_id") or "").strip()
                if (
                    row.get("needs_review")
                    and row.get("match_status") == "high_voice_candidate"
                    and row.get("candidate_confidence") == "high"
                    and machine_id in registry
                    and (not route_cast or machine_id in route_cast)
                ):
                    decision = {
                        "status": "auto_voice_confirmed",
                        "character_id": machine_id,
                        "voice_score": row.get("candidate_score"),
                        "voice_margin": row.get("candidate_margin"),
                        "reasons": ["high_voice_candidate", "candidate_confidence=high"],
                        "source": "named-report-voice-confidence",
                    }
            if decision is None:
                continue

            character_id = decision["character_id"]
            previous_id = str(row.get("character_id") or "").strip()
            if previous_id != character_id:
                total_changes += 1
                episode_changes += 1
            row["character_id"] = character_id
            row.update(metadata(character_id, registry))
            row["speaker_label"] = row["character_name"]
            row["match_status"] = decision["status"]
            row["needs_review"] = False
            evidence = list(row.get("evidence") or [])
            evidence.append(f"{decision['status']}:{','.join(decision['reasons'])}")
            row["evidence"] = list(dict.fromkeys(evidence))
            row["batch_confirmation"] = {
                "status": decision["status"],
                "previous_character_id": previous_id,
                "character_id": character_id,
                "source": decision["source"],
                "context_margin": decision.get("context_margin"),
                "voice_score": decision.get("voice_score", row.get("candidate_score")),
                "voice_margin": decision.get("voice_margin", row.get("candidate_margin")),
                "reasons": decision["reasons"],
                "warning": "Tự động xác nhận bằng voice/context; không thay thế nghe thủ công khi cần độ chính xác tuyệt đối.",
            }
            if decision["status"] == "auto_context_confirmed":
                total_context += 1
                episode_context += 1
            else:
                total_voice += 1
                episode_voice += 1

        report["engine"] = f"{report.get('engine', 'named-report')} + auditable-batch-confirmation"
        report["batch_confirmation"] = {
            "enabled": True,
            "context_confirmed": episode_context,
            "voice_confirmed": episode_voice,
            "label_changes": episode_changes,
            "diagnosis_source": str(args.diagnosis.resolve()),
            "remaining_needs_review": sum(int(bool(row.get("needs_review"))) for row in rows),
        }
        report["summary"] = summarize(rows, episode)
        if not args.dry_run:
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"EPISODE={episode} CONTEXT={episode_context} VOICE={episode_voice} "
            f"CHANGED={episode_changes} REMAINING={report['summary']['needs_review']}"
        )

    print(f"CONTEXT_CONFIRMED={total_context}")
    print(f"VOICE_CONFIRMED={total_voice}")
    print(f"LABEL_CHANGES={total_changes}")
    print(f"DRY_RUN={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
