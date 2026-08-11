"""Build a review-only per-cue character audit from local voice matching output.

The audit deliberately keeps machine candidates separate from verified names.
Speaker embeddings identify a voice candidate; they do not prove which fictional
character is speaking when the character is off-screen or voices overlap.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def confidence(score: float, margin: float) -> str:
    if score >= 0.70 and margin >= 0.20:
        return "high"
    if score >= 0.50 and margin >= 0.08:
        return "medium"
    return "low"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a review-only speaker audit")
    parser.add_argument("--voice-audit", type=Path, required=True)
    parser.add_argument("--character-profile", type=Path, required=True)
    parser.add_argument("--episode-01-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    voice_audit = json.loads(args.voice_audit.read_text(encoding="utf-8"))
    profile = json.loads(args.character_profile.read_text(encoding="utf-8"))
    reference = json.loads(args.episode_01_reference.read_text(encoding="utf-8"))
    characters = profile.get("characters") or {}

    # Episode 01 has a reviewed timing map. It is used only as a validation set;
    # it does not silently promote predictions in the other episodes.
    verified_by_start = {
        round(float(item.get("start", 0.0)), 3): item.get("character_id")
        for item in reference.get("kept", [])
        if item.get("character_id")
    }

    episodes: dict[str, Any] = {}
    total = 0
    verified = 0
    needs_review = 0
    confidence_counts: Counter[str] = Counter()
    for episode, data in (voice_audit.get("episodes") or {}).items():
        cues: list[dict[str, Any]] = []
        for index, item in enumerate(data.get("cues") or [], start=1):
            start = round(float(item.get("start", 0.0)), 3)
            candidate = str(item.get("character_id") or "").strip() or None
            score = float(item.get("score", 0.0))
            margin = float(item.get("margin", 0.0))
            level = confidence(score, margin)
            exact = None
            status = "machine_candidate"
            if episode == "01":
                exact = verified_by_start.get(start)
                if exact:
                    status = "verified_reference"
                    verified += 1
            review = status != "verified_reference"
            if review:
                needs_review += 1
            confidence_counts[level] += 1
            candidate_meta = characters.get(candidate) if candidate else None
            alternatives = []
            for option in item.get("candidates") or []:
                character_id = str(option.get("character_id") or "").strip()
                if not character_id or character_id == candidate:
                    continue
                metadata = characters.get(character_id) or {}
                alternatives.append(
                    {
                        "character_id": character_id,
                        "character_name": metadata.get("display_name") or character_id,
                        "score": option.get("score"),
                    }
                )
            cues.append(
                {
                    "cue": index,
                    "start": start,
                    "end": round(float(item.get("end", 0.0)), 3),
                    "text": item.get("text", ""),
                    "exact_character_id": exact,
                    "exact_character_name": (characters.get(exact) or {}).get("display_name") if exact else None,
                    "candidate_character_id": candidate,
                    "candidate_character_name": (candidate_meta or {}).get("display_name") or candidate,
                    "confidence": level,
                    "score": round(score, 6),
                    "margin": round(margin, 6),
                    "alternatives": alternatives,
                    "status": status,
                    "needs_review": review,
                    "evidence": ["voice_embedding_context4"],
                }
            )
            total += 1
        episodes[episode] = {"source": data.get("source"), "cue_count": len(cues), "cues": cues}

    output = {
        "schema_version": 1,
        "title": "Yosuga no Sora per-cue character audit",
        "mode": "review_only",
        "generated_by": "build-speaker-audit.py",
        "warning": "candidate_character_id is not an exact identification. Verify medium/low cues and all episodes except episode 01 before publishing names.",
        "calibration": {
            "reference_episode": "01",
            "reference_cues": verified,
            "reference_note": "Episode 01 candidate-vs-reviewed-map accuracy is calculated externally; non-reference episodes are not promoted to exact labels.",
            "confidence_rule": "high: score >= 0.70 and margin >= 0.20; medium: score >= 0.50 and margin >= 0.08; otherwise low",
        },
        "summary": {
            "episodes": len(episodes),
            "cues": total,
            "verified_reference_cues": verified,
            "machine_only_cues": needs_review,
            "confidence_counts": dict(confidence_counts),
        },
        "episodes": episodes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"AUDIT_CUES={total}")
    print(f"VERIFIED_REFERENCE_CUES={verified}")
    print(f"OUTPUT={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
