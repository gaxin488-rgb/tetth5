#!/usr/bin/env python3
"""Build optional CLIP visual-reference embeddings from character face images."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from visual_reference import average_feature_vectors, encode_images, load_clip


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local visual-reference embeddings")
    parser.add_argument("--profile", required=True, help="Reference profile template JSON")
    parser.add_argument("--output", required=True, help="Generated JSON with face embeddings")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--model", default="openai/clip-vit-base-patch32")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    profile = json.loads(Path(args.profile).expanduser().resolve().read_text(encoding="utf-8"))
    project_root = Path(args.project_root).expanduser().resolve()
    model, processor, torch = load_clip(args.model, args.device)
    built = 0
    for character_id, item in (profile.get("characters") or {}).items():
        if not isinstance(item, dict):
            continue
        images = []
        image_paths = []
        for raw_path in item.get("face_images") or []:
            image_path = Path(str(raw_path)).expanduser()
            if not image_path.is_absolute():
                image_path = project_root / image_path
            if not image_path.is_file():
                raise RuntimeError(f"Không tìm thấy face image cho {character_id}: {image_path}")
            from PIL import Image

            image_paths.append(image_path)
            images.append(Image.open(image_path).convert("RGB"))
        if images:
            vectors = encode_images(images, model, processor, torch, args.device)
            item["face_embeddings"] = vectors
            item["face_embedding"] = average_feature_vectors(vectors)
            built += 1
            for image in images:
                image.close()
            print(f"FACE_REFERENCE={character_id}:{len(image_paths)}")

    profile["generated_by"] = args.model
    profile["face_reference_count"] = built
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"VISUAL_PROFILE_OUTPUT={output_path}")
    print(f"VISUAL_PROFILE_CHARACTERS={built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
