#!/usr/bin/env python3
"""Zero-cost, local-only CineZero subtitle pipeline.

WhisperX handles transcription, language detection, alignment and optional
speaker diarization. inaSpeechSegmenter removes music, singing, noise and
silence before the transcript becomes a subtitle cue.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from character_rules import apply_character_rules
from reference_matching import apply_reference_map, extract_reference_embeddings, match_embeddings


NON_SPEECH_MARKER = re.compile(
    r"^\s*[\[(].*(?:music|song|sing|singing|instrumental|applause|laughter|noise|sound effect|âm nhạc|nhạc|vỗ tay|cười|tiếng động).*[\])]\s*$",
    re.IGNORECASE,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CineZero local auto subtitle pipeline (0đ, no paid API)")
    parser.add_argument("--input", required=True, help="Video/audio input path")
    parser.add_argument("--slug", required=True, help="CineZero movie slug")
    parser.add_argument("--language", default=None, help="Override language code; otherwise WhisperX detects it")
    parser.add_argument("--model", default=os.getenv("SUBTITLE_MODEL", "small"), help="WhisperX model: tiny/base/small/medium/large-v3")
    parser.add_argument("--device", default=os.getenv("SUBTITLE_DEVICE", "auto"), choices=["auto", "cpu", "cuda"], help="Inference device")
    parser.add_argument("--compute-type", default=os.getenv("SUBTITLE_COMPUTE_TYPE", "auto"), help="auto/int8/float16/float32")
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("SUBTITLE_BATCH_SIZE", "4")))
    parser.add_argument("--hf-token", default=os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACE_TOKEN"), help="Free Hugging Face read token for diarization")
    parser.add_argument("--no-diarize", action="store_true", help="Skip speaker diarization; not recommended")
    parser.add_argument("--num-speakers", type=int, default=None, help="Force the expected number of distinct speakers")
    parser.add_argument("--min-speakers", type=int, default=None, help="Lower bound for automatic speaker count")
    parser.add_argument("--max-speakers", type=int, default=None, help="Upper bound for automatic speaker count")
    parser.add_argument("--no-align", action="store_true", help="Skip word alignment if the language has no alignment model")
    parser.add_argument("--show-speakers", action="store_true", help="Debug only: add speaker labels to VTT cues")
    parser.add_argument("--no-speakers", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--character-profile", default=os.getenv("CHARACTER_PROFILE"), help="JSON profile mapping diarization speaker IDs to characters")
    parser.add_argument("--pronoun-rules", default=os.getenv("PRONOUN_RULES"), help="JSON Vietnamese pronoun rule overrides")
    parser.add_argument("--voice-reference-profile", default=os.getenv("VOICE_REFERENCE_PROFILE"), help="JSON profile containing voice embeddings for named characters")
    parser.add_argument("--voice-match-threshold", type=float, default=float(os.getenv("VOICE_MATCH_THRESHOLD", "0.55")))
    parser.add_argument("--voice-match-margin", type=float, default=float(os.getenv("VOICE_MATCH_MARGIN", "0.05")))
    parser.add_argument("--output", default=None, help="Local WebVTT output path")
    parser.add_argument("--report", default=None, help="Local JSON report path")
    parser.add_argument("--output-key", default=None, help="R2 key for publishing")
    parser.add_argument("--site-url", default=os.getenv("CINEZERO_SITE_URL"), help="Deployed CineZero URL")
    parser.add_argument("--admin-token", default=os.getenv("ADMIN_TOKEN"), help="CineZero ADMIN_TOKEN for publishing")
    parser.add_argument("--label", default=None, help="Subtitle label shown by the player")
    return parser.parse_args()


def resolve_hf_token(explicit: str | None) -> str | None:
    """Resolve a Hugging Face token without requiring it on every run.

    The preferred source is the command-line/environment value.  When it is
    absent, huggingface_hub (or its standard Windows cache files) is checked.
    The token is never printed or written to project files.
    """
    if explicit and explicit.strip():
        return explicit.strip()

    try:
        from huggingface_hub import get_token

        cached = get_token()
        if cached and cached.strip():
            return cached.strip()
    except Exception:
        pass

    for token_path in (
        Path.home() / ".cache" / "huggingface" / "token",
        Path.home() / ".huggingface" / "token",
    ):
        try:
            cached = token_path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            continue
        if cached:
            return cached
    return None


def choose_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


def choose_compute_type(requested: str, device: str) -> str:
    if requested != "auto":
        return requested
    return "float16" if device == "cuda" else "int8"


def load_and_transcribe(args: argparse.Namespace, device: str, compute_type: str) -> tuple[dict, str, dict]:
    try:
        import whisperx
    except ImportError as exc:
        raise RuntimeError("Thiếu whisperx. Chạy scripts\\setup-free-subtitles.ps1 trước.") from exc

    audio = whisperx.load_audio(str(Path(args.input).resolve()))
    diarization_info: dict = {"enabled": False, "speaker_embeddings": {}}
    print(f"3/6 WhisperX đang nhận dạng bằng model {args.model} trên {device}/{compute_type}…")
    model = whisperx.load_model(args.model, device, compute_type=compute_type)
    result = model.transcribe(audio, batch_size=max(1, args.batch_size), language=args.language)
    language = str(result.get("language") or args.language or "und").lower()

    if not args.no_align and language != "und":
        print(f"4/6 Căn timestamp cho ngôn ngữ {language}…")
        try:
            align_model, metadata = whisperx.load_align_model(language_code=language, device=device)
            result = whisperx.align(result["segments"], align_model, metadata, audio, device, return_char_alignments=False)
        except Exception as exc:  # alignment models are not available for every language
            print(f"Cảnh báo: bỏ qua alignment cho {language}: {exc}", file=sys.stderr)
    else:
        print("4/6 Bỏ qua alignment…")

    if not args.no_diarize:
        if not args.hf_token:
            raise RuntimeError("Diarization cần HF_TOKEN miễn phí. Tạo Hugging Face read token và truyền -hf-token hoặc đặt biến môi trường HF_TOKEN.")
        print("5/6 Nhận diện người nói bằng pipeline local WhisperX/pyannote…")
        try:
            diarization_pipeline = whisperx.DiarizationPipeline
        except AttributeError:
            # WhisperX 3.8+ keeps this class in whisperx.diarize instead of
            # re-exporting it from the package root.
            from whisperx.diarize import DiarizationPipeline

            diarization_pipeline = DiarizationPipeline
        diarize_model = diarization_pipeline(token=args.hf_token, device=device)
        if args.num_speakers is not None and (args.min_speakers is not None or args.max_speakers is not None):
            raise RuntimeError("Chỉ dùng một trong --num-speakers hoặc --min-speakers/--max-speakers.")
        diarize_kwargs = {"return_embeddings": True}
        if args.num_speakers is not None:
            diarize_kwargs["num_speakers"] = args.num_speakers
        else:
            if args.min_speakers is not None:
                diarize_kwargs["min_speakers"] = args.min_speakers
            if args.max_speakers is not None:
                diarize_kwargs["max_speakers"] = args.max_speakers
        try:
            diarize_output = diarize_model(audio, **diarize_kwargs)
        except TypeError:
            diarize_output = diarize_model(audio, **{key: value for key, value in diarize_kwargs.items() if key != "return_embeddings"})
        if isinstance(diarize_output, tuple):
            diarize_segments, speaker_embeddings = diarize_output
        else:
            diarize_segments, speaker_embeddings = diarize_output, {}
        result = whisperx.assign_word_speakers(diarize_segments, result)
        diarization_info = {
            "enabled": True,
            "model": "pyannote/speaker-diarization-community-1",
            "num_speakers": args.num_speakers,
            "min_speakers": args.min_speakers,
            "max_speakers": args.max_speakers,
            "speaker_embeddings": speaker_embeddings or {},
        }
    else:
        print("5/6 Bỏ qua diarization theo yêu cầu…")

    return result, language, diarization_info


def speech_music_zones(input_path: Path) -> list[tuple[str, float, float]]:
    try:
        from inaSpeechSegmenter import Segmenter
    except ImportError as exc:
        raise RuntimeError("Thiếu inaSpeechSegmenter. Chạy scripts\\setup-free-subtitles.ps1 trước.") from exc

    ffmpeg = os.getenv("FFMPEG_BIN", "ffmpeg")
    print("2/6 Phân loại speech/music/noise bằng inaSpeechSegmenter…")
    segmenter = Segmenter(vad_engine="smn", detect_gender=False, ffmpeg=ffmpeg)
    return [(str(label), float(start), float(end)) for label, start, end in segmenter(str(input_path))]


def interval_overlap(start: float, end: float, zone_start: float, zone_end: float) -> float:
    return max(0.0, min(end, zone_end) - max(start, zone_start))


def is_non_speech_marker(text: str) -> bool:
    return bool(NON_SPEECH_MARKER.match(text)) or any(mark in text for mark in ("♪", "♫", "♬"))


def is_repetitive_vocalization(text: str) -> bool:
    """Drop singing/humming and repeated vocal sounds misread as dialogue."""
    compact = re.sub(r"[\s、。，．,.!?！？…〜~ー]+", "", str(text or ""))
    if len(compact) < 6:
        return False
    vocal_chars = set("あいうえおんっ")
    if set(compact) <= vocal_chars and len(set(compact)) <= 3:
        return True
    if len(set(compact)) == 1:
        return True
    if len(compact) % 2 == 0 and compact == compact[:2] * (len(compact) // 2):
        return True
    return False


SENTENCE_END = re.compile(r"[。！？!?…]+$")
TRAILING_PUNCTUATION = set("、。，．,.!?;:：；…)]}】》」』）〕］〉")
OPENING_PUNCTUATION = set("「『【（([{<《〈")


def join_word_text(parts: list[str], language: str) -> str:
    """Join WhisperX words without losing natural CJK punctuation/spacing."""
    compact = language.lower() in {"ja", "zh", "th", "lo", "km"}
    text = ""
    for raw in parts:
        word = " ".join(str(raw or "").split())
        if not word:
            continue
        if not text:
            text = word
        elif word[0] in TRAILING_PUNCTUATION or text[-1] in OPENING_PUNCTUATION:
            text += word
        elif compact:
            text += word
        else:
            text += " " + word
    return text


def split_segment_without_words(segment: dict, language: str) -> list[dict]:
    """Fallback for languages/models where WhisperX has no word timestamps."""
    start = max(0.0, float(segment.get("start") or 0.0))
    end = max(start, float(segment.get("end") or start))
    text = " ".join(str(segment.get("text") or "").split())
    if not text:
        return []

    parts = [part.strip() for part in re.findall(r".+?(?:[。！？!?…]+|$)", text) if part.strip()]
    if not parts:
        parts = [text]
    total_chars = max(1, sum(len(part) for part in parts))
    pieces: list[dict] = []
    cursor = start
    base_id = str(segment.get("id") or "segment")
    for index, part in enumerate(parts, start=1):
        piece_end = end if index == len(parts) else cursor + (end - start) * len(part) / total_chars
        pieces.append({
            "id": f"{base_id}-{index}",
            "start": cursor,
            "end": piece_end,
            "speaker": segment.get("speaker"),
            "text": join_word_text([part], language),
        })
        cursor = piece_end
    return pieces


def split_segment_with_words(segment: dict, language: str, max_duration: float = 6.0, max_chars: int = 42, max_words: int = 16) -> list[dict]:
    """Turn an aligned WhisperX segment into readable, short subtitle cues."""
    words = []
    for word in segment.get("words") or []:
        text = str(word.get("word") or word.get("text") or "").strip()
        try:
            start = float(word.get("start"))
            end = float(word.get("end"))
        except (TypeError, ValueError):
            continue
        if text and end >= start:
            words.append({"word": text, "start": start, "end": end, "speaker": word.get("speaker")})
    if not words:
        return split_segment_without_words(segment, language)

    pieces: list[dict] = []
    current: list[dict] = []
    base_id = str(segment.get("id") or "segment")

    def flush() -> None:
        if not current:
            return
        text = join_word_text([item["word"] for item in current], language)
        if text:
            pieces.append({
                "id": f"{base_id}-{len(pieces) + 1}",
                "start": current[0]["start"],
                "end": current[-1]["end"],
                "speaker": current[-1].get("speaker") or segment.get("speaker"),
                "text": text,
            })
        current.clear()

    for word in words:
        if current:
            previous_speaker = current[-1].get("speaker") or segment.get("speaker")
            next_speaker = word.get("speaker") or segment.get("speaker")
            if previous_speaker and next_speaker and previous_speaker != next_speaker:
                flush()
        current.append(word)
        text = join_word_text([item["word"] for item in current], language)
        duration = current[-1]["end"] - current[0]["start"]
        if SENTENCE_END.search(text) or duration >= max_duration or len(text) >= max_chars or len(current) >= max_words:
            flush()
    flush()
    return pieces


def split_transcript_segments(raw_segments: list[dict], language: str) -> list[dict]:
    split_segments: list[dict] = []
    for segment in raw_segments:
        split_segments.extend(split_segment_with_words(segment, language))
    return split_segments


def filter_segments(raw_segments: list[dict], zones: list[tuple[str, float, float]]) -> tuple[list[dict], list[dict]]:
    kept: list[dict] = []
    removed: list[dict] = []
    for index, item in enumerate(raw_segments, start=1):
        start = max(0.0, float(item.get("start") or 0.0))
        end = max(start, float(item.get("end") or 0.0))
        duration = max(0.01, end - start)
        speech = sum(interval_overlap(start, end, zs, ze) for label, zs, ze in zones if label == "speech")
        music = sum(interval_overlap(start, end, zs, ze) for label, zs, ze in zones if label == "music")
        noise = sum(interval_overlap(start, end, zs, ze) for label, zs, ze in zones if label in {"noise", "noEnergy"})
        text = " ".join(str(item.get("text") or "").split())
        normalized = {"id": str(item.get("id") or index), "start": start, "end": end, "speaker": item.get("speaker"), "text": text}
        normalized.update({"speech_overlap": round(speech, 3), "music_overlap": round(music, 3), "noise_overlap": round(noise, 3)})
        if not text:
            normalized["decision"] = "remove_empty"
            removed.append(normalized)
        elif is_non_speech_marker(text) or is_repetitive_vocalization(text):
            normalized["decision"] = "remove_non_speech_marker"
            removed.append(normalized)
        elif speech >= max(0.15, duration * 0.25):
            normalized["decision"] = "keep_dialogue"
            kept.append(normalized)
        elif music >= max(0.15, duration * 0.25):
            normalized["decision"] = "remove_music_or_lyrics"
            removed.append(normalized)
        elif noise > 0:
            normalized["decision"] = "remove_noise"
            removed.append(normalized)
        else:
            normalized["decision"] = "remove_no_speech_zone"
            removed.append(normalized)
    return kept, removed


def speaker_label(value: object, labels: dict[str, str]) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Người nói"
    if raw not in labels:
        labels[raw] = f"Người nói {len(labels) + 1}"
    return labels[raw]


def escape_vtt(value: object) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def timecode(seconds: float) -> str:
    total_ms = max(0, round(float(seconds) * 1000))
    milliseconds = total_ms % 1000
    total_seconds = total_ms // 1000
    second = total_seconds % 60
    minute = (total_seconds // 60) % 60
    hour = total_seconds // 3600
    return f"{hour:02d}:{minute:02d}:{second:02d}.{milliseconds:03d}"


def build_vtt(segments: list[dict], language: str, show_speakers: bool) -> str:
    labels: dict[str, str] = {}
    cues = []
    for index, item in enumerate(segments, start=1):
        voice = (item.get("speaker_label") or speaker_label(item.get("speaker"), labels)) if show_speakers else ""
        prefix = f"<v {escape_vtt(voice)}>[{escape_vtt(voice)}] " if show_speakers else ""
        cues.append(f"{index}\n{timecode(item['start'])} --> {timecode(item['end'])}\n{prefix}{escape_vtt(item['text'])}")
    return "WEBVTT\n\n" + "\n".join([
        "NOTE CineZero local auto-subtitle",
        "NOTE engine=WhisperX+inaSpeechSegmenter",
        f"NOTE detected-language={language}",
        f"NOTE speaker-labels={'true' if show_speakers else 'false'}",
        "",
        "\n\n".join(cues),
    ]) + "\n"


def encoded_key(key: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in key.split("/"))


def http_json(url: str, method: str, body: bytes, token: str, content_type: str) -> dict:
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Worker trả HTTP {exc.code}: {detail}") from exc


def publish(args: argparse.Namespace, language: str, vtt: str) -> None:
    if not args.site_url and not args.admin_token:
        return
    if not args.site_url or not args.admin_token:
        raise RuntimeError("Muốn upload tự động cần cả --site-url và --admin-token.")
    key = args.output_key or f"subtitles/{args.slug}/{language}.vtt"
    base = args.site_url.rstrip("/")
    print(f"6/6 Upload WebVTT lên R2: {key}…")
    http_json(f"{base}/api/admin/assets/{encoded_key(key)}", "PUT", vtt.encode("utf-8"), args.admin_token, "text/vtt; charset=utf-8")
    label = args.label or f"Tự động · {language}"
    body = json.dumps({"slug": args.slug, "language_code": language, "label": label, "r2_key": key}, ensure_ascii=False).encode("utf-8")
    http_json(f"{base}/api/admin/subtitles", "POST", body, args.admin_token, "application/json")
    print("Đã upload và đăng ký subtitle track.")


def main() -> int:
    args = parse_args()
    args.hf_token = resolve_hf_token(args.hf_token)
    if not args.no_diarize and not args.hf_token:
        raise RuntimeError("Chưa có Hugging Face token. Chạy `hf auth login` một lần, hoặc đặt HF_TOKEN/--hf-token; không cần token nếu dùng --no-diarize.")
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        raise RuntimeError(f"Không tìm thấy input: {input_path}")

    device = choose_device(args.device)
    compute_type = choose_compute_type(args.compute_type, device)
    print("1/6 Kiểm tra pipeline local 0đ…")
    zones = speech_music_zones(input_path)
    result, language, diarization_info = load_and_transcribe(args, device, compute_type)
    raw_segments = result.get("segments") or []
    split_segments = split_transcript_segments(raw_segments, language)
    kept, removed = filter_segments(split_segments, zones)
    profile_path = Path(args.character_profile).expanduser().resolve() if args.character_profile else Path(__file__).resolve().parents[1] / "profiles" / f"{args.slug}.json"
    rules_path = Path(args.pronoun_rules).expanduser().resolve() if args.pronoun_rules else Path(__file__).resolve().parents[1] / "config" / "pronoun-rules.vi.json"
    character_profile = None
    pronoun_rules = None
    if profile_path.is_file():
        character_profile = json.loads(profile_path.read_text(encoding="utf-8"))
    elif args.character_profile:
        raise RuntimeError(f"Không tìm thấy character profile: {profile_path}")
    if rules_path.is_file():
        pronoun_rules = json.loads(rules_path.read_text(encoding="utf-8"))
    elif args.pronoun_rules:
        raise RuntimeError(f"Không tìm thấy pronoun rules: {rules_path}")
    voice_matching: dict = {"enabled": False, "reason": "no_voice_reference_profile"}
    if args.voice_reference_profile:
        voice_profile_path = Path(args.voice_reference_profile).expanduser().resolve()
        if not voice_profile_path.is_file():
            raise RuntimeError(f"Không tìm thấy voice reference profile: {voice_profile_path}")
        if character_profile is None:
            raise RuntimeError("Voice matching cần --character-profile để biết danh sách character_id.")
        voice_profile = json.loads(voice_profile_path.read_text(encoding="utf-8"))
        reference_embeddings = extract_reference_embeddings(voice_profile, key="voice")
        speaker_embeddings = diarization_info.get("speaker_embeddings") or {}
        matched_map, diagnostics = match_embeddings(
            speaker_embeddings,
            reference_embeddings,
            threshold=args.voice_match_threshold,
            margin=args.voice_match_margin,
        )
        character_profile = apply_reference_map(character_profile, matched_map)
        voice_matching = {
            "enabled": True,
            "profile_path": str(voice_profile_path),
            "matched_speaker_map": matched_map,
            "diagnostics": diagnostics,
            "threshold": args.voice_match_threshold,
            "margin": args.voice_match_margin,
        }
    kept, character_summary = apply_character_rules(kept, character_profile, pronoun_rules)
    character_summary["profile_path"] = str(profile_path) if character_profile else None
    character_summary["rules_path"] = str(rules_path) if pronoun_rules else None
    vtt = build_vtt(kept, language, bool(args.show_speakers and not args.no_speakers and not args.no_diarize))

    output = Path(args.output or Path("content") / "generated-subtitles" / f"{args.slug}.{language}.vtt").expanduser().resolve()
    report = Path(args.report or output.with_suffix(".segments.json")).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(vtt, encoding="utf-8")
    report.write_text(json.dumps({
        "slug": args.slug,
        "language": language,
        "engine": "WhisperX+inaSpeechSegmenter",
        "device": device,
        "compute_type": compute_type,
        "speech_music_zones": [{"label": label, "start": start, "end": end} for label, start, end in zones],
        "raw_segment_count": len(raw_segments),
        "split_segment_count": len(split_segments),
        "diarization": diarization_info,
        "voice_matching": voice_matching,
        "character_rules": character_summary,
        "kept": kept,
        "removed": removed,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Đã tạo {len(kept)} cue thoại; loại {len(removed)} cue nhạc/âm thanh.")
    print(f"VTT: {output}")
    print(f"Report: {report}")
    if character_summary.get("enabled"):
        print(f"Character rules: {len(character_summary.get('speakers') or {})} speaker profile(s), {len(character_summary.get('unmapped_speakers') or [])} unmapped speaker(s).")
    publish(args, language, vtt)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Đã hủy.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"AUTO_SUB_FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
