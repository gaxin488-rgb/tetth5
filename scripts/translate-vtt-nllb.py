#!/usr/bin/env python3
"""Translate WebVTT cues locally with NLLB.

The script keeps cue timings and VTT metadata intact. It is intentionally
separate from ASR so a Japanese transcript can be reviewed or retranslated
without running WhisperX again.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


TIME_LINE = re.compile(r"^\s*\d{2}:\d{2}:\d{2}[.,]\d{3}\s+-->\s+")
TAG = re.compile(r"<[^>\r\n]+>")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate WebVTT cues locally with NLLB")
    parser.add_argument("--input", required=True, help="Source WebVTT")
    parser.add_argument("--output", required=True, help="Translated WebVTT")
    parser.add_argument("--model", default="facebook/nllb-200-distilled-600M")
    parser.add_argument("--source-lang", default="jpn_Jpan", help="NLLB source language code")
    parser.add_argument("--target-lang", default="vie_Latn", help="NLLB target language code")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--beams", type=int, default=3)
    return parser.parse_args()


def split_blocks(value: str) -> list[str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    return re.split(r"\n{2,}", normalized) if normalized else []


def cue_text(block: str) -> tuple[list[str], int, str] | None:
    lines = block.split("\n")
    for index, line in enumerate(lines):
        if TIME_LINE.match(line):
            text = "\n".join(lines[index + 1 :]).strip()
            if text:
                return lines, index, text
    return None


def translatable_text(value: str) -> str:
    # Keep common VTT markup out of the translation model. The current CineZero
    # output has no speaker tags, but this also makes the tool safe for them.
    return TAG.sub("", value).replace("\n", " ").strip()


def load_model(model_name: str, source_lang: str):
    try:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("Can NLLB transformers va torch trong .venv-subtitles.") from exc

    tokenizer = AutoTokenizer.from_pretrained(model_name, src_lang=source_lang)
    tokenizer.src_lang = source_lang
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.eval()
    return torch, tokenizer, model


def translate_batch(
    texts: list[str],
    torch,
    tokenizer,
    model,
    target_lang: str,
    max_length: int,
    beams: int,
) -> list[str]:
    if not texts:
        return []
    encoded = tokenizer(
        texts,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=max_length,
    )
    target_id = tokenizer.convert_tokens_to_ids(target_lang)
    with torch.inference_mode():
        generated = model.generate(
            **encoded,
            forced_bos_token_id=target_id,
            max_length=max_length,
            num_beams=max(1, beams),
        )
    return tokenizer.batch_decode(generated, skip_special_tokens=True)


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size phai lon hon 0")
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if not input_path.is_file():
        raise SystemExit(f"Khong tim thay VTT: {input_path}")
    if input_path == output_path:
        raise SystemExit("Input va output phai la hai file khac nhau.")

    blocks = split_blocks(input_path.read_text(encoding="utf-8-sig"))
    cue_blocks: list[tuple[int, list[str], int, str]] = []
    for index, block in enumerate(blocks):
        parsed = cue_text(block)
        if parsed:
            lines, time_index, text = parsed
            cue_blocks.append((index, lines, time_index, translatable_text(text)))

    if not cue_blocks:
        raise SystemExit("Khong tim thay cue co timestamp trong VTT.")

    print(f"Loading NLLB: {args.model}")
    torch, tokenizer, model = load_model(args.model, args.source_lang)
    translated: list[str] = []
    total = len(cue_blocks)
    for start in range(0, total, args.batch_size):
        batch = [item[3] for item in cue_blocks[start : start + args.batch_size]]
        translated.extend(
            translate_batch(
                batch,
                torch,
                tokenizer,
                model,
                args.target_lang,
                args.max_length,
                args.beams,
            )
        )
        print(f"Translated {min(start + len(batch), total)}/{total} cues", flush=True)

    translated_by_block = {
        item[0]: output.strip() or item[3]
        for item, output in zip(cue_blocks, translated, strict=True)
    }
    output_blocks: list[str] = []
    for index, block in enumerate(blocks):
        # The generated file already gets a fresh header below. Avoid keeping
        # the source WEBVTT marker as a second header in the translated file.
        if block.strip().upper() == "WEBVTT":
            continue
        if index not in translated_by_block:
            output_blocks.append(block)
            continue
        lines = block.split("\n")
        time_index = next(i for i, line in enumerate(lines) if TIME_LINE.match(line))
        output_blocks.append("\n".join(lines[: time_index + 1] + [translated_by_block[index]]))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "WEBVTT\n\n"
        "NOTE CineZero local NLLB translation\n"
        f"NOTE source-language={args.source_lang}\n"
        f"NOTE target-language={args.target_lang}\n\n"
        + "\n\n".join(output_blocks)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"VTT_TRANSLATE_PASS={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
