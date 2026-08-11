#!/usr/bin/env python3
"""Attach voice-reference names to an existing diarized transcript.

This avoids rerunning Whisper ASR when a transcript/report already exists.
The pyannote model is run locally once to obtain speaker embeddings, then the
embeddings are matched against the generated reference profile.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path

import whisperx
from huggingface_hub import get_token

from character_rules import apply_character_rules
from reference_matching import apply_reference_map, extract_reference_embeddings, match_embeddings


def load_auto_sub_module():
    path = Path(__file__).resolve().with_name("auto-sub-local.py")
    spec = importlib.util.spec_from_file_location("auto_sub_local", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Không thể nạp {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match named voice references to a diarized report")
    parser.add_argument("--input", required=True, help="Video/audio input path")
    parser.add_argument("--report", required=True, help="Existing report containing kept transcript segments")
    parser.add_argument("--voice-reference-profile", required=True, help="Generated voice reference JSON")
    parser.add_argument("--character-profile", required=True, help="Character profile JSON")
    parser.add_argument("--pronoun-rules", default="config/pronoun-rules.vi.json")
    parser.add_argument("--output", required=True, help="Output VTT")
    parser.add_argument("--report-output", required=True, help="Output JSON report")
    parser.add_argument("--hf-token", default=os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN"))
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--no-embeddings", action="store_true", help="Skip speaker embedding extraction for a faster visual/manual review run")
    parser.add_argument("--num-speakers", type=int, default=None, help="Force the expected number of distinct speakers")
    parser.add_argument("--min-speakers", type=int, default=None, help="Lower bound for automatic speaker count")
    parser.add_argument("--max-speakers", type=int, default=None, help="Upper bound for automatic speaker count")
    parser.add_argument("--voice-match-threshold", type=float, default=0.55)
    parser.add_argument("--voice-match-margin", type=float, default=0.05)
    return parser.parse_args()


def resolve_token(explicit: str | None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    try:
        return get_token()
    except Exception:
        return None


def main() -> int:
    args = parse_args()
    token = resolve_token(args.hf_token)
    if not token:
        raise RuntimeError("Chưa có Hugging Face token. Chạy hf auth login một lần.")

    source_report_path = Path(args.report).expanduser().resolve()
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    segments = [dict(item) for item in source_report.get("kept") or []]
    if not segments:
        raise RuntimeError("Report không có kept transcript segments")

    from whisperx.diarize import DiarizationPipeline

    print(f"REUSE_TRANSCRIPT_SEGMENTS={len(segments)}")
    pipeline = DiarizationPipeline(token=token, device=args.device)
    audio = whisperx.load_audio(str(Path(args.input).expanduser().resolve()))
    last = [-1]

    def progress(value: float) -> None:
        step = int(value // 10) * 10
        if step > last[0]:
            last[0] = step
            print(f"DIARIZATION_PROGRESS={step}%")

    if args.num_speakers is not None and (args.min_speakers is not None or args.max_speakers is not None):
        raise RuntimeError("Chỉ dùng một trong --num-speakers hoặc --min-speakers/--max-speakers.")
    diarize_kwargs = {"return_embeddings": not args.no_embeddings, "progress_callback": progress}
    if args.num_speakers is not None:
        diarize_kwargs["num_speakers"] = args.num_speakers
    else:
        if args.min_speakers is not None:
            diarize_kwargs["min_speakers"] = args.min_speakers
        if args.max_speakers is not None:
            diarize_kwargs["max_speakers"] = args.max_speakers
    diarize_output = pipeline(audio, **diarize_kwargs)
    if isinstance(diarize_output, tuple):
        diarized_df, speaker_embeddings = diarize_output
    else:
        diarized_df, speaker_embeddings = diarize_output, {}
    assigned = whisperx.assign_word_speakers(
        diarized_df,
        {"segments": segments},
        fill_nearest=True,
    )
    segments = assigned["segments"]

    voice_profile = json.loads(Path(args.voice_reference_profile).expanduser().resolve().read_text(encoding="utf-8"))
    reference_embeddings = extract_reference_embeddings(voice_profile, key="voice")
    matched_map, diagnostics = match_embeddings(
        speaker_embeddings or {},
        reference_embeddings,
        threshold=args.voice_match_threshold,
        margin=args.voice_match_margin,
    )
    character_profile = json.loads(Path(args.character_profile).expanduser().resolve().read_text(encoding="utf-8"))
    character_profile = apply_reference_map(character_profile, matched_map)
    rules_path = Path(args.pronoun_rules).expanduser().resolve()
    pronoun_rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.is_file() else None
    enriched, character_summary = apply_character_rules(segments, character_profile, pronoun_rules)

    auto_sub = load_auto_sub_module()
    vtt = auto_sub.build_vtt(enriched, "ja", True)
    output_path = Path(args.output).expanduser().resolve()
    report_path = Path(args.report_output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(vtt, encoding="utf-8")
    output_report = dict(source_report)
    output_report["engine"] = "WhisperX+inaSpeechSegmenter+pyannote+voice-reference"
    output_report["diarization"] = {
        "enabled": True,
        "model": "pyannote/speaker-diarization-community-1",
        "return_embeddings": not args.no_embeddings,
        "num_speakers": args.num_speakers,
        "min_speakers": args.min_speakers,
        "max_speakers": args.max_speakers,
        "speaker_embeddings": speaker_embeddings or {},
    }
    output_report["voice_matching"] = {
        "enabled": True,
        "profile_path": str(Path(args.voice_reference_profile).expanduser().resolve()),
        "matched_speaker_map": matched_map,
        "diagnostics": diagnostics,
        "threshold": args.voice_match_threshold,
        "margin": args.voice_match_margin,
    }
    output_report["character_rules"] = character_summary
    output_report["kept"] = enriched
    report_path.write_text(json.dumps(output_report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"VOICE_MATCHED_SPEAKERS={json.dumps(matched_map, ensure_ascii=True, sort_keys=True)}")
    print(f"VTT_OUTPUT={output_path}")
    print(f"REPORT_OUTPUT={report_path}")
    print("VOICE_REFERENCE_MATCH=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
