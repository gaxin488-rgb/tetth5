#!/usr/bin/env python3
"""Build per-cue character reports for the remaining Yosuga no Sora episodes.

The local WeSpeaker matcher supplies a voice candidate.  This command adds
title-specific route/scene constraints and writes a reviewable, named report
without changing the clean Vietnamese subtitle track used by the website.

It is deliberately conservative: a name in ``character_id`` is a provisional
working label unless ``match_status`` says ``verified_reference`` or
``reviewed_rule``.  The original candidate, score, margin and evidence are
kept next to the chosen label so a reviewer can correct a cue without running
WhisperX again.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vtt_time import format_interval


TIME_RE = re.compile(r"^\s*([0-9:.]+)\s*-->\s*([0-9:.]+)")


FALLBACK_CHARACTERS: dict[str, dict[str, Any]] = {
    "haruka": {"display_name": "Haruka Kasugano", "age": 16, "gender": "male", "role": "student"},
    "sora": {"display_name": "Sora Kasugano", "age": 16, "gender": "female", "role": "student"},
    "akira": {"display_name": "Akira Amatsume", "age": 16, "gender": "female", "role": "student"},
    "nao": {"display_name": "Nao Yorihime", "age": 17, "gender": "female", "role": "older_neighbor_student"},
    "teacher": {"display_name": "Giáo viên", "age_band": "adult", "gender": "unknown", "role": "teacher"},
    "kazuha": {"display_name": "Kazuha Migiwa", "age": 16, "gender": "female", "role": "student"},
    "kozue": {"display_name": "Kozue Kuranaga", "age": 16, "gender": "female", "role": "class_representative"},
    "ryouhei": {"display_name": "Ryouhei Nakazato", "age": 17, "gender": "male", "role": "student_friend"},
    "motoka": {"display_name": "Motoka Nogisaka", "age_band": "adult", "gender": "female", "role": "maid"},
    "yahiro": {"display_name": "Yahiro Ifukube", "age_band": "adult", "gender": "female", "role": "adult_guardian"},
    "nao_mother": {"display_name": "Mẹ của Nao", "age_band": "adult", "gender": "female", "role": "parent"},
    "nao_father": {"display_name": "Cha của Nao (hồi tưởng)", "age_band": "adult", "gender": "male", "role": "parent"},
}


# Characters that can occur in the main story of each episode.  The list is a
# constraint for impossible prototype labels, not proof that every character
# in the list speaks in every scene.
ROUTE_CAST: dict[str, set[str]] = {
    "02": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "03": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "04": {"haruka", "sora", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "05": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "06": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "07": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "nao_mother", "nao_father", "ryouhei", "yahiro", "teacher", "motoka"},
    "08": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "09": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "10": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "11": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
    "12": {"haruka", "sora", "akira", "kazuha", "kozue", "nao", "ryouhei", "yahiro", "teacher", "motoka"},
}


@dataclass(frozen=True)
class ManualCueRule:
    episode: str
    cue_numbers: frozenset[int]
    character_id: str
    evidence: str
    status: str = "reviewed_rule"
    needs_review: bool = False


# Episode 02 has a few unambiguous dialogue turns that are useful as anchors:
# Ryouhei opens the school conversation, Haruka answers, and Nao explicitly
# addresses Ryouhei.  The remaining group greetings stay machine candidates.
MANUAL_RULES = (
    ManualCueRule(
        "02",
        frozenset({6, 8, 10, 11, 12, 13, 15, 16, 18, 19, 20, 21, 22, 24, 26, 27, 28, 32, 33}),
        "ryouhei",
        "episode-02 dialogue order: Ryouhei leads the school conversation and calls Nao",
    ),
    ManualCueRule(
        "02",
        frozenset({7, 9, 14, 17, 23, 25, 29, 30, 31}),
        "haruka",
        "episode-02 dialogue order: Haruka answers Ryouhei in the school conversation",
    ),
    ManualCueRule(
        "02",
        frozenset({34, 35}),
        "nao",
        "episode-02 dialogue text explicitly says Nao will stand by Ryouhei",
    ),
    ManualCueRule(
        "02",
        frozenset({58, 60, 61, 68, 69, 71, 75, 77, 79}),
        "haruka",
        "episode-02 mosquito scene: Haruka addresses or responds to Sora",
    ),
    ManualCueRule(
        "02",
        frozenset({59, 62, 63, 64, 65, 66, 67, 70, 72, 73, 74, 76, 78}),
        "sora",
        "episode-02 mosquito scene: Sora reacts to the mosquito and clings to Haruka",
    ),
    # Episode 04, 00:18:46.600-00:19:00.010: the source subtitles carry
    # two interleaved calls during Kazuha and Haruka's embrace.  The voice
    # matcher confuses this short, overlapping scene with Sora/Kozue, so keep
    # the source cue order and use the visible characters plus the addressed
    # names as the title-specific mapping.
    ManualCueRule(
        "04",
        frozenset({244, 249, 251, 252}),
        "haruka",
        "episode-04 video/context review: the silver-haired Haruka calls or answers Kazuha; duplicate source-layer cues keep their original timestamps",
        status="context_rule",
        needs_review=True,
    ),
    ManualCueRule(
        "04",
        frozenset({245, 246, 247, 248, 250}),
        "kazuha",
        "episode-04 video/context review: the black-haired Kazuha calls Haruka; duplicate source-layer cues keep their original timestamps",
        status="context_rule",
        needs_review=True,
    ),
    # Episode 07, 00:07:42.140-00:07:50.530: the source ASS contains a
    # background argument (Nao's parents) plus an overlapping Default-Alt
    # layer where Nao says "Stop it" from the bath.  The parent voices are
    # context mappings and remain reviewable; the visible Nao cues are fixed
    # from the extracted video frames.
    ManualCueRule(
        "07",
        frozenset({88, 90}),
        "nao_mother",
        "episode-07 source-layer context: Nao's mother speaks in the background argument; exact cue overlaps the Nao memory scene",
        status="context_rule",
        needs_review=True,
    ),
    ManualCueRule(
        "07",
        frozenset({89}),
        "nao_father",
        "episode-07 source-layer context: Nao's father answers in the background argument; exact cue overlaps the Nao memory scene",
        status="context_rule",
        needs_review=True,
    ),
    ManualCueRule(
        "07",
        frozenset({91, 92, 93}),
        "nao",
        "episode-07 manual video review at 00:07:45.150-00:07:49.150: the visible short dark-haired girl with glasses is Nao Yorihime",
    ),
    # Episode 07, 00:14:57.700-00:14:59.410: two exact-time source layers
    # read "Nao-chan?" and "Haru-chan?" over the same pool shot.  The silver-
    # haired boy on the diving board is Haruka and the dark-haired girl in
    # the pool is Nao; both cues were previously mislabeled as Sora.
    ManualCueRule(
        "07",
        frozenset({186}),
        "haruka",
        "episode-07 video/source-layer review at 00:14:57.700-00:14:59.410: Haruka on the diving board says the Nao-chan line; overlapping layer preserved",
        status="context_rule",
        needs_review=True,
    ),
    ManualCueRule(
        "07",
        frozenset({187}),
        "nao",
        "episode-07 video/source-layer review at 00:14:57.700-00:14:59.410: Nao in the pool says the Haru-chan line; overlapping layer preserved",
        status="context_rule",
        needs_review=True,
    ),
    # Episode 12 has two overlapping copies of Sora's apology in the source
    # subtitle layer.  The following reply addresses Sora and is Haruka's
    # line; keep the original cue times unchanged.
    ManualCueRule(
        "12",
        frozenset({156, 157}),
        "sora",
        "episode-12 manual context/frame review: Sora is the one apologizing; cue 157 overlaps cue 158 in the source layer",
    ),
    ManualCueRule(
        "12",
        frozenset({158}),
        "haruka",
        "episode-12 dialogue context: Haruka replies \"Không sao đâu, Sora.\" to Sora",
    ),
)


def parse_timestamp(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"Invalid timestamp: {value}")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def format_timestamp(value: float) -> str:
    milliseconds = max(0, int(round(float(value) * 1000)))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def parse_vtt(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8-sig")
    cues: list[dict[str, Any]] = []
    for block in re.split(r"\r?\n\s*\r?\n", content.strip()):
        lines = block.splitlines()
        time_index = next((index for index, line in enumerate(lines) if "-->" in line), None)
        if time_index is None:
            continue
        match = TIME_RE.match(lines[time_index])
        if not match:
            continue
        text = " ".join(line.strip() for line in lines[time_index + 1 :] if line.strip()).strip()
        text = re.sub(r"^\s*<v\s+[^>]+>\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^\s*\[[^\]]+\]\s*", "", text)
        if text:
            cues.append(
                {
                    "start": parse_timestamp(match.group(1)),
                    "end": parse_timestamp(match.group(2)),
                    "text": text,
                }
            )
    return cues


def load_characters(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    characters = {key: dict(value) for key, value in FALLBACK_CHARACTERS.items()}
    for key, value in (profile.get("characters") or {}).items():
        if isinstance(value, dict):
            characters[str(key)] = {**characters.get(str(key), {}), **value}
    return characters


def scene_boundaries(cues: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """Return the likely omake and next-preview starts from long cue gaps."""
    if not cues:
        return None, None
    gaps: list[tuple[int, float]] = []
    for index in range(1, len(cues)):
        gap = float(cues[index]["start"]) - float(cues[index - 1]["end"])
        if gap >= 60 and float(cues[index]["start"]) >= 1200:
            gaps.append((index, float(cues[index]["start"])))
    if not gaps:
        return None, None
    omake_start = gaps[0][1]
    preview_start = gaps[1][1] if len(gaps) > 1 else None
    return omake_start, preview_start


def scene_for(start: float, omake_start: float | None, preview_start: float | None) -> str:
    if preview_start is not None and start >= preview_start:
        return "next_episode_preview"
    if omake_start is not None and start >= omake_start:
        return "omake"
    if start < 20:
        return "opening_dialogue"
    return "main_story"


def manual_rule_for(episode: str, cue_number: int) -> ManualCueRule | None:
    for rule in MANUAL_RULES:
        if rule.episode == episode and cue_number in rule.cue_numbers:
            return rule
    return None


def omake_rule(text: str, scene: str) -> tuple[str, str] | None:
    if scene != "omake":
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in ("nogisaka motoka", "motoka nogisaka", "nhiệm vụ của", "câu nói hôm nay")):
        return "motoka", "omake self-narration/title text identifies Motoka"
    return None


def confidence(score: float, margin: float) -> str:
    if score >= 0.70 and margin >= 0.20:
        return "high"
    if score >= 0.50 and margin >= 0.08:
        return "medium"
    return "low"


def choose_route_candidate(item: dict[str, Any], episode: str, scene: str, characters: dict[str, dict[str, Any]]) -> tuple[str | None, list[str]]:
    route_cast = ROUTE_CAST.get(episode, set(characters))
    candidate = str(item.get("character_id") or "").strip() or None
    candidates = [
        str(option.get("character_id") or "").strip()
        for option in item.get("candidates") or []
        if str(option.get("character_id") or "").strip()
    ]
    if candidate and candidate not in candidates:
        candidates.insert(0, candidate)
    allowed_candidates = [value for value in candidates if value in route_cast]

    if candidate and candidate in route_cast:
        return candidate, []
    if allowed_candidates:
        return allowed_candidates[0], ["route_cast_constraint"]
    if candidate and candidate in characters:
        return candidate, ["candidate_outside_route_cast"]
    return None, ["no_named_candidate"]


def character_metadata(character_id: str | None, characters: dict[str, dict[str, Any]]) -> dict[str, Any]:
    character = characters.get(character_id or "") or {}
    age = character.get("age")
    try:
        age_number = int(age) if age is not None else None
    except (TypeError, ValueError):
        age_number = None
    declared_band = character.get("age_band")
    inferred_band = declared_band or ("teen" if age_number is not None and age_number <= 17 else "adult" if age_number is not None else "unknown")
    return {
        "character_name": character.get("display_name") or character_id,
        "character_age": age,
        "character_age_band": inferred_band,
        "character_age_confidence": character.get("age_confidence") or "profile_or_title_metadata",
        "character_gender": character.get("gender") or "unknown",
        "character_gender_confidence": character.get("gender_confidence") or "profile_or_title_metadata",
        "character_role": character.get("role"),
    }


def build_named_report(
    episode: str,
    voice_data: dict[str, Any],
    characters: dict[str, dict[str, Any]],
    source_vtt: Path,
    output_dir: Path,
    source_video_override: Path | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    source_cues = parse_vtt(source_vtt)
    voice_cues = list((voice_data.get("episodes") or {}).get(episode, {}).get("cues") or [])
    if len(source_cues) != len(voice_cues):
        raise RuntimeError(f"Episode {episode}: VTT cues={len(source_cues)} but voice cues={len(voice_cues)}")

    omake_start, preview_start = scene_boundaries(source_cues)
    kept: list[dict[str, Any]] = []
    match_statuses: Counter[str] = Counter()
    chosen_counts: Counter[str] = Counter()
    review_count = 0

    for index, (cue, machine) in enumerate(zip(source_cues, voice_cues), start=1):
        start = round(float(cue["start"]), 3)
        end = round(float(cue["end"]), 3)
        scene = scene_for(start, omake_start, preview_start)
        score = float(machine.get("score") or 0.0)
        margin = float(machine.get("margin") or 0.0)
        machine_candidate = str(machine.get("character_id") or "").strip() or None
        evidence = ["voice_embedding_context4"]
        rule = manual_rule_for(episode, index)
        chosen: str | None
        status: str
        needs_review = True

        if rule:
            chosen = rule.character_id
            status = rule.status
            evidence.append(rule.evidence)
            needs_review = rule.needs_review
        else:
            omake = omake_rule(str(cue["text"]), scene)
            if omake:
                chosen = omake[0]
                status = "reviewed_rule"
                evidence.append(omake[1])
                needs_review = False
            else:
                chosen, route_evidence = choose_route_candidate(machine, episode, scene, characters)
                evidence.extend(route_evidence)
                status = "machine_candidate"
                if chosen and chosen == machine_candidate and confidence(score, margin) == "high":
                    status = "high_voice_candidate"
                if route_evidence:
                    status = "route_constrained_candidate"

        if scene == "next_episode_preview":
            evidence.append("scene_boundary_next_episode_preview")
            needs_review = True
        elif scene == "omake":
            evidence.append("scene_boundary_omake")

        if not chosen:
            status = "unresolved_candidate"
            needs_review = True

        metadata = character_metadata(chosen, characters)
        match_statuses[status] += 1
        chosen_counts[chosen or "unknown"] += 1
        if needs_review:
            review_count += 1

        alternatives: list[dict[str, Any]] = []
        for option in machine.get("candidates") or []:
            option_id = str(option.get("character_id") or "").strip()
            if not option_id or option_id == machine_candidate:
                continue
            option_meta = character_metadata(option_id, characters)
            alternatives.append(
                {
                    "character_id": option_id,
                    "character_name": option_meta["character_name"],
                    "score": option.get("score"),
                }
            )

        kept.append(
            {
                "cue": index,
                "id": f"episode-{episode}-cue-{index:04d}",
                "start": start,
                "end": end,
                "timestamp": format_interval(start, end),
                "text": cue["text"],
                "scene": scene,
                "machine_candidate_id": machine_candidate,
                "machine_candidate_name": character_metadata(machine_candidate, characters)["character_name"],
                "candidate_score": round(score, 6),
                "candidate_margin": round(margin, 6),
                "candidate_confidence": confidence(score, margin),
                "character_id": chosen,
                **metadata,
                "speaker_label": metadata["character_name"],
                "alternatives": alternatives,
                "match_status": status,
                "needs_review": needs_review,
                "evidence": evidence,
            }
        )

    report_path = output_dir / f"yosuga-no-sora-{episode}.vi.named.segments.json"
    vtt_path = output_dir / f"yosuga-no-sora-{episode}.vi.named.vtt"
    report = {
        "schema_version": 2,
        "title": f"Yosuga no Sora - {episode}",
        "language": "vi",
        "mode": "named_review",
        "metadata_status": "review_required",
        "engine": "pyannote WeSpeaker context4 + route/scene rules",
        "source_vtt": str(source_vtt.resolve()),
        "source_video": str(source_video_override.resolve()) if source_video_override else (voice_data.get("episodes") or {}).get(episode, {}).get("source"),
        "warning": "Names for episodes 02-12 are working labels. Only reviewed_rule rows are promoted by a title-specific rule; all other rows retain needs_review unless manually verified.",
        "route_context": {
            "route_cast": sorted(ROUTE_CAST.get(episode, set())),
            "omake_start": omake_start,
            "next_episode_preview_start": preview_start,
            "scene_note": "Large gaps after 20:00 are treated as the extra/omake and preview boundaries; verify if a source edit differs.",
        },
        "character_registry": {key: value for key, value in sorted(characters.items())},
        "summary": {
            "episode": episode,
            "cues": len(kept),
            "named_working_labels": sum(1 for item in kept if item.get("character_id")),
            "needs_review": review_count,
            "match_statuses": dict(match_statuses),
            "character_counts": dict(chosen_counts),
        },
        "kept": kept,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Keep the player track clean.  The report is the only place where names
    # are stored; no <v Name> or [Name] prefix is written to this VTT.
    lines = [
        "WEBVTT",
        "",
        "NOTE CineZero Vietnamese subtitle",
        "NOTE engine=aligned-existing-translation+character-report",
        "NOTE source-language=en",
        "NOTE translated-language=vi",
        "NOTE speaker-labels=false",
        "NOTE character-labels-hidden=true",
        "NOTE overlapping-cues=preserved-from-source-subtitle-layering",
        f"NOTE character-report={report_path.name}",
        "",
    ]
    for index, cue in enumerate(kept, start=1):
        text = html.escape(str(cue["text"]), quote=False)
        lines.extend([str(index), f"{format_timestamp(cue['start'])} --> {format_timestamp(cue['end'])}", text, ""])
    vtt_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path, vtt_path, report["summary"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice-audit", type=Path, required=True)
    parser.add_argument("--character-profile", type=Path, required=True)
    parser.add_argument("--subtitle-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--video-dir", type=Path, help="Directory containing encoded episode videos, e.g. content/encoded")
    parser.add_argument("--episodes", nargs="+", default=[f"{number:02d}" for number in range(2, 13)])
    args = parser.parse_args()

    voice_audit = json.loads(args.voice_audit.resolve().read_text(encoding="utf-8"))
    profile = json.loads(args.character_profile.resolve().read_text(encoding="utf-8"))
    characters = load_characters(profile)
    video_dir = args.video_dir.resolve() if args.video_dir else None
    if video_dir and not video_dir.is_dir():
        raise FileNotFoundError(video_dir)
    summaries = []

    for raw_episode in args.episodes:
        episode = str(raw_episode).zfill(2)
        source_vtt = args.subtitle_dir.resolve() / f"yosuga-no-sora-{episode}.vi.vtt"
        if not source_vtt.is_file():
            raise FileNotFoundError(source_vtt)
        source_video_override = None
        if video_dir:
            candidates = sorted(video_dir.glob(f"yosuga-no-sora-{episode}-*.mp4"))
            if not candidates:
                raise FileNotFoundError(f"No encoded MP4 for episode {episode} in {video_dir}")
            source_video_override = candidates[0]
        report_path, vtt_path, summary = build_named_report(
            episode,
            voice_audit,
            characters,
            source_vtt,
            args.output_dir.resolve(),
            source_video_override,
        )
        summaries.append(summary)
        print(
            f"EPISODE={episode} CUES={summary['cues']} NEEDS_REVIEW={summary['needs_review']} "
            f"REPORT={report_path} VTT={vtt_path}",
            flush=True,
        )

    print("TOTAL_CUES=" + str(sum(item["cues"] for item in summaries)))
    print("TOTAL_NEEDS_REVIEW=" + str(sum(item["needs_review"] for item in summaries)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
