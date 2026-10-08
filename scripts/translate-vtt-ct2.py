#!/usr/bin/env python3
"""Translate WebVTT cues locally with a CTranslate2 NLLB model.

The tokenizer remains the original Hugging Face NLLB tokenizer while the
decoder runs through CTranslate2. This keeps timestamps and VTT metadata
intact and is substantially lighter on CPU than Transformers generation.
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
    parser = argparse.ArgumentParser(description="Translate WebVTT cues locally with CTranslate2 NLLB")
    parser.add_argument("--input", required=True, help="Source WebVTT")
    parser.add_argument("--output", required=True, help="Translated WebVTT")
    parser.add_argument("--model", required=True, help="Converted CTranslate2 model directory")
    parser.add_argument("--tokenizer", required=True, help="Hugging Face NLLB tokenizer directory or model id")
    parser.add_argument("--source-lang", default="jpn_Jpan", help="NLLB source language code")
    parser.add_argument("--target-lang", default="vie_Latn", help="NLLB target language code")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--beam-size", type=int, default=2)
    parser.add_argument("--max-decoding-length", type=int, default=96)
    parser.add_argument("--intra-threads", type=int, default=4)
    parser.add_argument("--repetition-penalty", type=float, default=1.2)
    parser.add_argument("--no-repeat-ngram-size", type=int, default=3)
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
    return TAG.sub("", value).replace("\n", " ").strip()


def load_runtime(model_path: str, tokenizer_path: str, source_lang: str, intra_threads: int):
    try:
        import ctranslate2
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise RuntimeError("Cần ctranslate2 và transformers trong .venv-subtitles.") from exc

    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, src_lang=source_lang)
    tokenizer.src_lang = source_lang
    translator = ctranslate2.Translator(
        model_path,
        device="cpu",
        intra_threads=max(1, intra_threads),
        inter_threads=1,
    )
    return tokenizer, translator


def translate_batch(
    texts: list[str],
    tokenizer,
    translator,
    source_lang: str,
    target_lang: str,
    beam_size: int,
    max_decoding_length: int,
    repetition_penalty: float,
    no_repeat_ngram_size: int,
) -> list[str]:
    encoded = tokenizer(texts, add_special_tokens=True, padding=False, truncation=True, max_length=256)
    source_tokens = [tokenizer.convert_ids_to_tokens(ids) for ids in encoded["input_ids"]]
    results = translator.translate_batch(
        source_tokens,
        target_prefix=[[target_lang] for _ in source_tokens],
        beam_size=max(1, beam_size),
        max_decoding_length=max(8, max_decoding_length),
        repetition_penalty=max(1.0, repetition_penalty),
        no_repeat_ngram_size=max(0, no_repeat_ngram_size),
        return_scores=False,
    )
    translated: list[str] = []
    for result in results:
        tokens = result.hypotheses[0]
        if tokens and tokens[0] == target_lang:
            tokens = tokens[1:]
        translated.append(tokenizer.convert_tokens_to_string(tokens).strip())
    return translated


def main() -> int:
    args = parse_args()
    if args.batch_size < 1:
        raise SystemExit("--batch-size phải lớn hơn 0")
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    if not input_path.is_file():
        raise SystemExit(f"Không tìm thấy VTT: {input_path}")
    if input_path == output_path:
        raise SystemExit("Input và output phải là hai file khác nhau.")

    blocks = split_blocks(input_path.read_text(encoding="utf-8-sig"))
    cue_blocks: list[tuple[int, list[str], int, str]] = []
    for index, block in enumerate(blocks):
        parsed = cue_text(block)
        if parsed:
            lines, time_index, text = parsed
            cue_blocks.append((index, lines, time_index, translatable_text(text)))

    if not cue_blocks:
        raise SystemExit("Không tìm thấy cue có timestamp trong VTT.")

    print(f"Loading CTranslate2 NLLB: {args.model}")
    tokenizer, translator = load_runtime(args.model, args.tokenizer, args.source_lang, args.intra_threads)
    translated: list[str] = []
    total = len(cue_blocks)
    for start in range(0, total, args.batch_size):
        batch = [item[3] for item in cue_blocks[start : start + args.batch_size]]
        translated.extend(
            translate_batch(
                batch,
                tokenizer,
                translator,
                args.source_lang,
                args.target_lang,
                args.beam_size,
                args.max_decoding_length,
                args.repetition_penalty,
                args.no_repeat_ngram_size,
            )
        )
        print(f"Translated {min(start + len(batch), total)}/{total} cues", flush=True)

    translated_by_block = {
        item[0]: output.strip() or item[3]
        for item, output in zip(cue_blocks, translated, strict=True)
    }
    output_blocks: list[str] = []
    for index, block in enumerate(blocks):
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
        "NOTE CineZero local CTranslate2 NLLB translation\n"
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
