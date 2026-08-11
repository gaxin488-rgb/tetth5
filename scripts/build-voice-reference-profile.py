#!/usr/bin/env python3
"""Build local pyannote voice references for named characters.

Each reference clip should contain one clean character voice.  The resulting
JSON stores only numeric embeddings and can be used to match diarized
SPEAKER_XX labels without sending audio to a paid service.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from reference_matching import average_embeddings


def resolve_token(explicit: str | None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    if os.getenv("HF_TOKEN"):
        return os.getenv("HF_TOKEN").strip()
    try:
        from huggingface_hub import get_token

        return get_token()
    except Exception:
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local voice-reference embeddings")
    parser.add_argument("--profile", required=True, help="Reference profile template JSON")
    parser.add_argument("--output", required=True, help="Generated JSON with voice embeddings")
    parser.add_argument("--project-root", default=".", help="Base directory for relative sample paths")
    parser.add_argument("--hf-token", default=None, help="Optional Hugging Face read token")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    return parser.parse_args()


def dominant_embedding(diarize_df: Any, embeddings: dict[str, list[float]] | None) -> list[float]:
    if not embeddings:
        return []
    durations: dict[str, float] = {}
    for _, row in diarize_df.iterrows():
        speaker = str(row.get("speaker") or "")
        if not speaker:
            continue
        durations[speaker] = durations.get(speaker, 0.0) + max(
            0.0,
            float(row.get("end") or 0.0) - float(row.get("start") or 0.0),
        )
    speaker = max(durations, key=durations.get) if durations else next(iter(embeddings), None)
    return list(embeddings.get(speaker, [])) if speaker else []


def main() -> int:
    args = parse_args()
    profile_path = Path(args.profile).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    project_root = Path(args.project_root).expanduser().resolve()
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    token = resolve_token(args.hf_token)
    if not token:
        raise RuntimeError("Chưa có Hugging Face token. Chạy hf auth login một lần.")

    import whisperx
    from whisperx.diarize import DiarizationPipeline

    pipeline = DiarizationPipeline(token=token, device=args.device)
    characters = profile.get("characters") or {}
    built = 0
    for character_id, item in characters.items():
        if not isinstance(item, dict):
            continue
        samples = item.get("voice_samples") or item.get("samples") or []
        embeddings: list[list[float]] = []
        for raw_path in samples:
            sample_path = Path(str(raw_path)).expanduser()
            if not sample_path.is_absolute():
                sample_path = project_root / sample_path
            if not sample_path.is_file():
                raise RuntimeError(f"Không tìm thấy voice sample cho {character_id}: {sample_path}")
            print(f"VOICE_REFERENCE={character_id}:{sample_path.name}")
            audio = whisperx.load_audio(str(sample_path))
            diarize_df, sample_embeddings = pipeline(audio, return_embeddings=True)
            embedding = dominant_embedding(diarize_df, sample_embeddings)
            if embedding:
                embeddings.append(embedding)
        if embeddings:
            item["voice_embeddings"] = embeddings
            item["voice_embedding"] = average_embeddings(embeddings)
            built += 1

    profile["generated_by"] = "pyannote-speaker-diarization-community-1"
    profile["voice_reference_count"] = built
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"VOICE_PROFILE_OUTPUT={output_path}")
    print(f"VOICE_PROFILE_CHARACTERS={built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
