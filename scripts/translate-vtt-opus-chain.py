#!/usr/bin/env python3
"""Translate a WebVTT file through local Japanese->English->Vietnamese models.

This is a local fallback for subtitle review. It keeps the cue numbering and
timestamps, while the two-step translation gives a second independent draft
to compare with the existing NLLB output.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


TIME_RE = re.compile(r"^\s*\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->\s+")
TAG_RE = re.compile(r"<[^>\r\n]+>")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ja-en-model", default="Helsinki-NLP/opus-mt-ja-en")
    parser.add_argument("--en-vi-model", default="Helsinki-NLP/opus-mt-en-vi")
    parser.add_argument("--batch-size", type=int, default=8)
    return parser.parse_args()


def load_cues(path: Path) -> tuple[list[str], list[tuple[int, str]]]:
    blocks = re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip())
    output_blocks: list[str] = []
    cues: list[tuple[int, str]] = []
    for index, block in enumerate(blocks):
        lines = block.splitlines()
        time_index = next((i for i, line in enumerate(lines) if TIME_RE.match(line)), None)
        if time_index is None:
            output_blocks.append(block)
            continue
        text = TAG_RE.sub("", " ".join(lines[time_index + 1 :])).strip()
        if text:
            cues.append((index, text))
        output_blocks.append(block)
    return output_blocks, cues


def translate(texts: list[str], model_name: str, batch_size: int) -> list[str]:
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    import torch

    print(f"Loading {model_name}", flush=True)
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.eval()
    translated: list[str] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        encoded = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=256)
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                num_beams=4,
                max_new_tokens=160,
                no_repeat_ngram_size=3,
            )
        translated.extend(tokenizer.batch_decode(generated, skip_special_tokens=True))
        print(f"Translated {min(start + len(batch), len(texts))}/{len(texts)}", flush=True)
    return translated


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size must be at least 1")
    blocks, cues = load_cues(args.input)
    if not cues:
        raise SystemExit("No VTT cues found")
    english = translate([text for _, text in cues], args.ja_en_model, args.batch_size)
    vietnamese = translate(english, args.en_vi_model, args.batch_size)
    translated_by_block = {block_index: text.strip() for (block_index, _), text in zip(cues, vietnamese, strict=True)}
    rendered: list[str] = []
    for block_index, block in enumerate(blocks):
        if block_index not in translated_by_block:
            rendered.append(block)
            continue
        lines = block.splitlines()
        time_index = next(i for i, line in enumerate(lines) if TIME_RE.match(line))
        rendered.append("\n".join(lines[: time_index + 1] + [translated_by_block[block_index]]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "WEBVTT\n\n"
        "NOTE CineZero local OPUS chain draft\n"
        "NOTE source-language=ja\n"
        "NOTE target-language=vi\n\n"
        + "\n\n".join(rendered)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"VTT_OUTPUT={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
