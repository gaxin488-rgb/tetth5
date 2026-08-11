#!/usr/bin/env python3
"""Apply a reviewed character map to an existing diarized report.

This is intentionally separate from diarization so a reviewer can update a
profile and regenerate named VTT cues without rescanning a long video.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from character_rules import apply_character_rules


def load_auto_sub_module():
    path = Path(__file__).resolve().with_name("auto-sub-local.py")
    spec = importlib.util.spec_from_file_location("auto_sub_local", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Không thể nạp {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply a character profile to an existing subtitle report")
    parser.add_argument("--report", required=True, help="Existing JSON report with kept segments")
    parser.add_argument("--character-profile", required=True)
    parser.add_argument("--pronoun-rules", default="config/pronoun-rules.vi.json")
    parser.add_argument("--output", required=True, help="Output WebVTT")
    parser.add_argument("--report-output", required=True, help="Output enriched JSON report")
    parser.add_argument("--language", default=None)
    parser.add_argument("--no-speakers", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = Path(args.report).expanduser().resolve()
    source = json.loads(source_path.read_text(encoding="utf-8"))
    segments = [dict(item) for item in source.get("kept") or []]
    if not segments:
        raise RuntimeError("Report không có kept transcript segments")

    auto_sub = load_auto_sub_module()
    removed_vocalizations = []
    kept = []
    for item in segments:
        if auto_sub.is_repetitive_vocalization(str(item.get("text") or "")):
            removed = dict(item)
            removed["decision"] = "remove_repetitive_vocalization"
            removed_vocalizations.append(removed)
        else:
            kept.append(item)

    profile_path = Path(args.character_profile).expanduser().resolve()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    rules_path = Path(args.pronoun_rules).expanduser().resolve()
    rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.is_file() else None
    enriched, summary = apply_character_rules(kept, profile, rules)
    language = args.language or str(source.get("language") or "und")
    vtt = auto_sub.build_vtt(enriched, language, not args.no_speakers)

    output_path = Path(args.output).expanduser().resolve()
    report_path = Path(args.report_output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(vtt, encoding="utf-8")
    output_report = dict(source)
    output_report["engine"] = f"{source.get('engine', 'CineZero')}+character-profile"
    output_report["character_rules"] = summary
    output_report["kept"] = enriched
    output_report["removed_character_profile"] = removed_vocalizations
    output_report["character_profile"] = str(profile_path)
    report_path.write_text(json.dumps(output_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"CHARACTER_PROFILE_SEGMENTS={len(enriched)}")
    print(f"REMOVED_REPETITIVE_VOCALIZATIONS={len(removed_vocalizations)}")
    print(f"VTT_OUTPUT={output_path}")
    print(f"REPORT_OUTPUT={report_path}")
    print("CHARACTER_PROFILE_APPLY=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
