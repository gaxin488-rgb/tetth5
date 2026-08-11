#!/usr/bin/env python3
"""Match character face references against sampled video frames locally.

This is an optional visual signal.  It samples the midpoint of dialogue cues,
compares the frame embedding with tightly cropped reference images, and maps
the accumulated visual vectors back to diarized speakers.  It does not upload
frames or images anywhere.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path

from character_rules import apply_character_rules
from reference_matching import apply_reference_map, extract_reference_embeddings, match_embeddings
from visual_reference import average_feature_vectors, encode_images, load_clip


def load_auto_sub_module():
    path = Path(__file__).resolve().with_name("auto-sub-local.py")
    spec = importlib.util.spec_from_file_location("auto_sub_local", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Không thể nạp {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match visual character references to an existing report")
    parser.add_argument("--input", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--visual-reference-profile", required=True)
    parser.add_argument("--character-profile", required=True)
    parser.add_argument("--pronoun-rules", default="config/pronoun-rules.vi.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--report-output", required=True)
    parser.add_argument("--model", default="openai/clip-vit-base-patch32")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--ffmpeg", default=os.getenv("FFMPEG_BIN", "ffmpeg"))
    parser.add_argument("--max-frames", type=int, default=200)
    parser.add_argument("--visual-match-threshold", type=float, default=0.65)
    parser.add_argument("--visual-match-margin", type=float, default=0.03)
    return parser.parse_args()


def frame_at(ffmpeg: str, input_path: Path, seconds: float):
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{max(0.0, seconds):.3f}",
        "-i",
        str(input_path),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    result = subprocess.run(command, check=False, capture_output=True)
    if result.returncode != 0 or not result.stdout:
        return None
    from PIL import Image

    return Image.open(io.BytesIO(result.stdout)).convert("RGB")


def main() -> int:
    args = parse_args()
    source_report = json.loads(Path(args.report).expanduser().resolve().read_text(encoding="utf-8"))
    segments = [dict(item) for item in source_report.get("kept") or []]
    if not segments:
        raise RuntimeError("Report không có kept transcript segments")
    visual_profile = json.loads(Path(args.visual_reference_profile).expanduser().resolve().read_text(encoding="utf-8"))
    reference_embeddings = extract_reference_embeddings(visual_profile, key="face")
    if not reference_embeddings:
        raise RuntimeError("Visual reference profile chưa có face_embedding; hãy chạy build-visual-reference-profile.py trước.")

    model, processor, torch = load_clip(args.model, args.device)
    selected = segments[: max(1, args.max_frames)]
    frames = []
    frame_speakers = []
    for index, item in enumerate(selected, start=1):
        midpoint = (float(item.get("start") or 0.0) + float(item.get("end") or 0.0)) / 2.0
        image = frame_at(args.ffmpeg, Path(args.input).expanduser().resolve(), midpoint)
        if image is not None:
            frames.append(image)
            frame_speakers.append(str(item.get("speaker") or "unknown"))
        if index % 20 == 0:
            print(f"VISUAL_FRAMES={index}/{len(selected)}")

    vectors = encode_images(frames, model, processor, torch, args.device)
    speaker_vectors: dict[str, list[list[float]]] = {}
    for speaker, vector in zip(frame_speakers, vectors):
        speaker_vectors.setdefault(speaker, []).append(vector)
    aggregated = {
        speaker: average_feature_vectors(values)
        for speaker, values in speaker_vectors.items()
        if average_feature_vectors(values)
    }
    matched_map, diagnostics = match_embeddings(
        aggregated,
        reference_embeddings,
        threshold=args.visual_match_threshold,
        margin=args.visual_match_margin,
    )

    character_profile = json.loads(Path(args.character_profile).expanduser().resolve().read_text(encoding="utf-8"))
    character_profile = apply_reference_map(character_profile, matched_map)
    rules_path = Path(args.pronoun_rules).expanduser().resolve()
    rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path.is_file() else None
    enriched, character_summary = apply_character_rules(segments, character_profile, rules)
    auto_sub = load_auto_sub_module()
    vtt = auto_sub.build_vtt(enriched, "ja", True)

    output_path = Path(args.output).expanduser().resolve()
    report_path = Path(args.report_output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(vtt, encoding="utf-8")
    output_report = dict(source_report)
    output_report["engine"] = "WhisperX+inaSpeechSegmenter+pyannote+visual-reference"
    output_report["visual_matching"] = {
        "enabled": True,
        "model": args.model,
        "frames_used": len(frames),
        "matched_speaker_map": matched_map,
        "diagnostics": diagnostics,
        "threshold": args.visual_match_threshold,
        "margin": args.visual_match_margin,
        "note": "Visual matching is a candidate signal; verify occluded or off-screen characters.",
    }
    output_report["character_rules"] = character_summary
    output_report["kept"] = enriched
    report_path.write_text(json.dumps(output_report, ensure_ascii=False, indent=2), encoding="utf-8")
    for image in frames:
        image.close()
    print(f"VISUAL_MATCHED_SPEAKERS={json.dumps(matched_map, ensure_ascii=True, sort_keys=True)}")
    print(f"VTT_OUTPUT={output_path}")
    print(f"REPORT_OUTPUT={report_path}")
    print("VISUAL_REFERENCE_MATCH=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
