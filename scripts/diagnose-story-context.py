#!/usr/bin/env python3
"""Diagnose story context for subtitle cues whose speaker is uncertain.

This is a free, local-only review assistant.  It does not overwrite a report
or claim that a fictional character was identified.  It combines nearby
dialogue, scene boundaries, trusted speakers, direct name mentions, route
constraints, voice candidates, profile relations and Vietnamese pronoun
rules to produce a conservative recommendation for translation review.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from character_rules import choose_pronouns, merge_rules, normalize_profile  # noqa: E402


SCENE_GAP_SECONDS = 7.0
DIRECT_NAME_BONUS = 0.08
RELATION_BONUS = 0.07
ALTERNATING_TURN_PENALTY = 0.12
CONTINUITY_BONUS = 0.025
RECOMMENDATION_MARGIN = 0.12

PLOT_SIGNAL_LEXICON: dict[str, tuple[str, ...]] = {
    "school": ("trường", "lớp", "giáo viên", "thầy giáo", "cô giáo", "học sinh", "bài tập", "kiểm tra", "tiết học", "clb", "câu lạc bộ"),
    "home": ("nhà", "phòng", "bếp", "nấu ăn", "đi ngủ", "ngủ", "tắm", "cửa", "chìa khóa", "điện thoại"),
    "family": ("anh", "em", "chị", "mẹ", "cha", "bố", "song sinh", "gia đình", "tai nạn", "ông", "bà"),
    "romance": ("thích", "yêu", "hẹn", "ghen", "hôn", "nhớ", "tình cảm", "ở bên", "đừng bỏ"),
    "health": ("bệnh", "bệnh viện", "đau", "thuốc", "sốt", "bơi", "chết", "cứu", "ngất", "máu"),
    "conflict": ("xin lỗi", "không tin", "đừng", "tránh", "im đi", "giận", "khóc", "sợ", "ghét", "không được"),
    "travel": ("đi", "về", "xe", "tàu", "trạm", "thành phố", "biển", "đường", "rời khỏi"),
    "work": ("làm việc", "cửa hàng", "quán", "phục vụ", "người giúp việc", "quản lý", "lương"),
}

ROLE_SIGNAL_BONUS: dict[str, tuple[str, ...]] = {
    "teacher": ("school",),
    "student": ("school",),
    "class_representative": ("school",),
    "student_friend": ("school",),
    "maid": ("work", "home"),
    "adult_guardian": ("family", "home"),
    "parent": ("family", "home"),
}


def fold_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in text if not unicodedata.combining(char))


def compact_text(value: Any, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def number(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return round(max(low, min(high, value)), 6)


def episode_code(report: dict[str, Any], path: Path) -> str:
    match = re.search(r"(\d{2})$", str(report.get("title") or ""))
    return match.group(1) if match else path.stem[-2:]


def base_title(report: dict[str, Any]) -> str:
    return re.sub(r"\s+-\s+\d{2}$", "", str(report.get("title") or "video")).strip()


def inferred_age_band(character: dict[str, Any]) -> str:
    declared = str(character.get("age_band") or "").strip()
    if declared:
        return declared
    try:
        age = int(character.get("age"))
    except (TypeError, ValueError):
        return "unknown"
    if age <= 12:
        return "child"
    if age <= 17:
        return "teen"
    if age <= 59:
        return "adult"
    return "senior"


def display_name(character_id: str, character: dict[str, Any]) -> str:
    return str(character.get("display_name") or character.get("name") or character_id)


def character_aliases(character_id: str, character: dict[str, Any]) -> list[str]:
    values = [character_id, display_name(character_id, character)]
    values.extend(str(item) for item in character.get("aliases") or [] if item)
    display = display_name(character_id, character)
    values.extend(part for part in re.split(r"\s+", display) if len(part) >= 3)
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = fold_text(value)
        if key and key not in seen and len(key) >= 3:
            seen.add(key)
            unique.append(value)
    return unique


def find_mentions(text: str, characters: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    folded = fold_text(text)
    matches: list[dict[str, Any]] = []
    for character_id, character in characters.items():
        found_alias: str | None = None
        for alias in character_aliases(character_id, character):
            alias_folded = fold_text(alias)
            if not alias_folded:
                continue
            if re.search(r"(?<!\w)" + re.escape(alias_folded) + r"(?!\w)", folded):
                found_alias = alias
                break
        if found_alias:
            matches.append({"character_id": character_id, "character_name": display_name(character_id, character), "alias": found_alias})
    return matches


def detect_addressed_character(text: str, mentions: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not mentions:
        return None
    folded = fold_text(text).strip()
    for mention in mentions:
        alias = fold_text(mention["alias"])
        if re.match(r"^" + re.escape(alias) + r"(?:\s|[,!.?…:]|$)", folded):
            return {**mention, "reason": "name_at_start_of_cue"}
        if re.search(r"(?:[,!?.…:]|\boi\b|\bnày\b)\s*" + re.escape(alias) + r"(?:\s|[,!.?…:]|$)", folded):
            return {**mention, "reason": "direct_address_pattern"}
    if len(mentions) == 1:
        return {**mentions[0], "reason": "single_name_mention_listener_candidate"}
    return None


def plot_signals(text: str) -> list[str]:
    folded = fold_text(text)
    found: list[str] = []
    for signal, keywords in PLOT_SIGNAL_LEXICON.items():
        if any(fold_text(keyword) in folded for keyword in keywords):
            found.append(signal)
    return found


def pronoun_signals(text: str) -> list[str]:
    folded = fold_text(text)
    terms = {
        "anh": ("anh", "anh ta", "anh ấy"),
        "em": ("em", "em ấy"),
        "chi": ("chi", "chi ay"),
        "co_thay": ("co", "co ay", "thay", "giao vien"),
        "ban_cau": ("ban", "cau", "cau ay"),
        "me_cha": ("me", "cha", "bo", "ba"),
    }
    return [label for label, values in terms.items() if any(fold_text(value) in folded for value in values)]


def build_scene_index(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[int]]]:
    scene_ids: list[dict[str, Any]] = []
    by_scene: dict[str, list[int]] = defaultdict(list)
    previous_end: float | None = None
    previous_scene: str | None = None
    scene_number = 0
    for index, row in enumerate(rows):
        scene = str(row.get("scene") or "unknown")
        start = number(row.get("start"))
        end = number(row.get("end"), start)
        gap = start - previous_end if previous_end is not None else 0.0
        if previous_scene != scene or (gap > SCENE_GAP_SECONDS and previous_end is not None):
            scene_number += 1
        scene_id = f"{scene}-{scene_number:03d}"
        row["story_scene_id"] = scene_id
        by_scene[scene_id].append(index)
        if not scene_ids or scene_ids[-1]["scene_id"] != scene_id:
            scene_ids.append({"scene_id": scene_id, "scene": scene, "start": start, "end": end, "indices": []})
        scene_ids[-1]["indices"].append(index)
        scene_ids[-1]["end"] = end
        previous_end = end
        previous_scene = scene
    return scene_ids, by_scene


def is_trusted(row: dict[str, Any]) -> bool:
    return not bool(row.get("needs_review")) and str(row.get("match_status") or "") in {
        "reviewed_rule",
        "verified_reference",
        "profile_override",
        "auto_context_confirmed",
        "auto_voice_confirmed",
    }


def candidate_seed(row: dict[str, Any]) -> tuple[dict[str, float], dict[str, list[str]]]:
    scores: dict[str, float] = {}
    reasons: dict[str, list[str]] = defaultdict(list)

    def add(character_id: Any, score: Any, reason: str) -> None:
        value = str(character_id or "").strip()
        if not value:
            return
        scores[value] = max(scores.get(value, -1.0), number(score, 0.0))
        reasons[value].append(reason)

    machine_id = str(row.get("machine_candidate_id") or "").strip()
    add(machine_id, row.get("candidate_score"), "voice_candidate")
    assigned_id = str(row.get("character_id") or "").strip()
    if assigned_id:
        add(assigned_id, number(row.get("candidate_score"), 0.0) - (0.03 if assigned_id != machine_id else 0.0), "current_report_label")
    for alternative in row.get("alternatives") or []:
        if isinstance(alternative, dict):
            add(alternative.get("character_id"), alternative.get("score"), "voice_alternative")
    return scores, reasons


def scene_summary(
    scene: dict[str, Any],
    rows: list[dict[str, Any]],
    characters: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    indices = scene["indices"]
    participants: Counter[str] = Counter()
    trusted: Counter[str] = Counter()
    unresolved: list[int] = []
    signals: Counter[str] = Counter()
    text_parts: list[str] = []
    for index in indices:
        row = rows[index]
        character_id = str(row.get("character_id") or "").strip()
        if character_id:
            participants[character_id] += 1
        if is_trusted(row) and character_id:
            trusted[character_id] += 1
        if row.get("needs_review"):
            unresolved.append(int(row.get("cue") or index + 1))
        for signal in plot_signals(str(row.get("text") or "")):
            signals[signal] += 1
        if len(text_parts) < 5 or index in indices[-2:]:
            text_parts.append(compact_text(row.get("text"), 160))
    participant_data = [
        {"character_id": key, "name": display_name(key, characters.get(key) or {}), "cue_count": count}
        for key, count in participants.most_common()
    ]
    return {
        "scene_id": scene["scene_id"],
        "scene": scene["scene"],
        "start": round(scene["start"], 3),
        "end": round(scene["end"], 3),
        "cue_count": len(indices),
        "participants": participant_data,
        "trusted_participants": [
            {"character_id": key, "name": display_name(key, characters.get(key) or {}), "cue_count": count}
            for key, count in trusted.most_common()
        ],
        "unresolved_cues": unresolved,
        "plot_signals": dict(signals),
        "text_preview": text_parts,
    }


def diagnose_row(
    row: dict[str, Any],
    row_index: int,
    rows: list[dict[str, Any]],
    scene_indices: list[int],
    characters: dict[str, dict[str, Any]],
    profile: dict[str, Any],
    rules: dict[str, Any],
    route_cast: set[str],
    evidence_by_key: dict[tuple[str, int], dict[str, Any]],
    code: str,
    show_title: str,
    window: int,
) -> dict[str, Any]:
    scores, reasons = candidate_seed(row)
    mentions = find_mentions(str(row.get("text") or ""), characters)
    addressed = detect_addressed_character(str(row.get("text") or ""), mentions)
    signals = plot_signals(str(row.get("text") or ""))
    pronoun_hints = pronoun_signals(str(row.get("text") or ""))
    scene_position = scene_indices.index(row_index)
    nearby_indices = scene_indices[max(0, scene_position - window) : min(len(scene_indices), scene_position + window + 1)]
    nearby_rows = [rows[index] for index in nearby_indices if index != row_index]

    scene_counts: Counter[str] = Counter()
    for nearby in nearby_rows:
        nearby_id = str(nearby.get("character_id") or "").strip()
        if nearby_id:
            scene_counts[nearby_id] += 1
    for character_id, count in scene_counts.items():
        if character_id in characters:
            scores.setdefault(character_id, 0.0)
            scores[character_id] += min(0.10, count * CONTINUITY_BONUS)
            reasons[character_id].append(f"scene_continuity:{count}")

    previous = rows[scene_indices[scene_position - 1]] if scene_position > 0 else None
    following = rows[scene_indices[scene_position + 1]] if scene_position + 1 < len(scene_indices) else None
    for label, neighbor in (("previous_turn", previous), ("next_turn", following)):
        if not neighbor or not is_trusted(neighbor):
            continue
        neighbor_id = str(neighbor.get("character_id") or "").strip()
        time_gap = abs(number(row.get("start")) - number(neighbor.get("end")))
        if not neighbor_id:
            continue
        scores.setdefault(neighbor_id, 0.0)
        if time_gap <= 0.45:
            scores[neighbor_id] -= ALTERNATING_TURN_PENALTY
            reasons[neighbor_id].append(f"{label}:adjacent_trusted_speaker")
        else:
            reasons[neighbor_id].append(f"{label}:trusted_scene_context")

    if addressed:
        addressed_id = str(addressed["character_id"])
        for character_id in list(scores):
            if character_id == addressed_id:
                scores[character_id] -= 0.09
                reasons[character_id].append("likely_listener_named_in_cue")
                continue
            if f"{character_id}->{addressed_id}" in (profile.get("relations") or {}):
                scores[character_id] += RELATION_BONUS
                reasons[character_id].append("profile_relation_to_named_listener")
        if addressed_id not in scores and addressed_id in characters:
            scores[addressed_id] = 0.0
            reasons[addressed_id].append("named_listener_candidate")

    for character_id, character in characters.items():
        role = str(character.get("role") or "")
        expected_signals = ROLE_SIGNAL_BONUS.get(role, ())
        matching = [signal for signal in expected_signals if signal in signals]
        if matching:
            scores.setdefault(character_id, 0.0)
            scores[character_id] += DIRECT_NAME_BONUS
            reasons[character_id].append("role_matches_plot_signal:" + "+".join(matching))

    for mention in mentions:
        mentioned_id = str(mention["character_id"])
        scores.setdefault(mentioned_id, 0.0)
        reasons[mentioned_id].append("name_mentioned:" + str(mention["alias"]))

    if route_cast:
        scores = {key: value for key, value in scores.items() if key in route_cast}
    scores = {key: value for key, value in scores.items() if key in characters}
    if not scores:
        scores = {key: 0.0 for key in route_cast if key in characters}
        for key in scores:
            reasons[key].append("route_cast_fallback")

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    top_id, top_raw = ranked[0] if ranked else (None, 0.0)
    second_raw = ranked[1][1] if len(ranked) > 1 else -1.0
    context_margin = round(top_raw - second_raw, 6) if ranked else 0.0
    current_id = str(row.get("character_id") or "").strip() or None
    explicit_evidence = any(reason.startswith("name_mentioned") or reason.startswith("profile_relation") for reason in reasons.get(top_id or "", []))
    if top_id and context_margin >= RECOMMENDATION_MARGIN and (explicit_evidence or len(reasons.get(top_id, [])) >= 2):
        recommendation = top_id
        recommendation_status = "context_supported"
    elif top_id and context_margin >= 0.06:
        recommendation = top_id
        recommendation_status = "weak_context_candidate"
    else:
        recommendation = None
        recommendation_status = "ambiguous"
    if not current_id and recommendation:
        change_required = True
    else:
        change_required = bool(recommendation and current_id and recommendation != current_id)

    listener_id = str(addressed["character_id"]) if addressed else None
    pronoun_speaker_id = recommendation or current_id or top_id
    pronouns = choose_pronouns(profile, rules, pronoun_speaker_id, listener_id)
    translation_hint = "Giữ xưng hô trung tính cho tới khi xác nhận người nói."
    if listener_id and pronoun_speaker_id:
        speaker_name = display_name(pronoun_speaker_id, characters.get(pronoun_speaker_id) or {})
        listener_name = display_name(listener_id, characters.get(listener_id) or {})
        translation_hint = f"Câu đang hướng tới {listener_name}; nếu người nói là {speaker_name}, ưu tiên quan hệ {pronouns.get('relation')} và xưng '{pronouns.get('self')}'–'{pronouns.get('other')}', nhưng vẫn cần nghe lại."
    elif recommendation:
        translation_hint = f"Ứng viên ngữ cảnh hiện nghiêng về {display_name(recommendation, characters.get(recommendation) or {})}; chưa tự động đổi nhãn."

    evidence_item = evidence_by_key.get((code, int(row.get("cue") or 0)))
    alternatives = [
        {
            "character_id": character_id,
            "character_name": display_name(character_id, characters.get(character_id) or {}),
            "context_score": clamp(raw),
            "reasons": reasons.get(character_id, []),
        }
        for character_id, raw in ranked[:6]
    ]
    before = [compact_text(rows[index].get("text")) for index in nearby_indices if index < row_index][-window:]
    after = [compact_text(rows[index].get("text")) for index in nearby_indices if index > row_index][:window]
    return {
        "episode": code,
        "cue": row.get("cue"),
        "id": row.get("id"),
        "start": row.get("start"),
        "end": row.get("end"),
        "scene": row.get("scene"),
        "story_scene_id": row.get("story_scene_id"),
        "text": row.get("text"),
        "current_character_id": current_id,
        "current_character_name": row.get("character_name"),
        "current_match_status": row.get("match_status"),
        "current_needs_review": bool(row.get("needs_review")),
        "machine_candidate_id": row.get("machine_candidate_id"),
        "voice_score": row.get("candidate_score"),
        "voice_margin": row.get("candidate_margin"),
        "recommended_character_id": recommendation,
        "recommended_character_name": display_name(recommendation, characters.get(recommendation) or {}) if recommendation else None,
        "recommendation_status": recommendation_status,
        "recommendation_would_change_label": change_required,
        "context_margin": context_margin,
        "candidate_alternatives": alternatives,
        "mentioned_characters": mentions,
        "addressed_character": addressed,
        "plot_signals": signals,
        "pronoun_signals": pronoun_hints,
        "translation_context": {
            "listener_id": listener_id,
            "listener_name": display_name(listener_id, characters.get(listener_id) or {}) if listener_id else None,
            "speaker_for_pronoun_check": pronoun_speaker_id,
            "pronouns": pronouns,
            "hint": translation_hint,
        },
        "nearby_dialogue": {"before": before, "after": after},
        "scene_participants": sorted(scene_counts),
        "evidence": reasons.get(recommendation or current_id or "", []),
        "evidence_frames": (evidence_item or {}).get("frames") or [],
        "research_query": (evidence_item or {}).get("research_query") or f'"{show_title}" "{row.get("character_name") or current_id or recommendation or "unknown"}" character official',
        "do_not_auto_apply": True,
    }


def load_evidence(path: Path | None) -> dict[tuple[str, int], dict[str, Any]]:
    if not path or not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    output: dict[tuple[str, int], dict[str, Any]] = {}
    for item in data.get("evidence") or []:
        try:
            output[(str(item.get("episode")).zfill(2), int(item.get("cue")))] = item
        except (TypeError, ValueError):
            continue
    return output


def write_html(path: Path, rows: list[dict[str, Any]], title: str) -> None:
    cards: list[str] = []
    for item in rows:
        candidates = "<br>".join(
            f"{html.escape(str(candidate['character_name']))} — {candidate['context_score']} — {html.escape(', '.join(candidate['reasons']))}"
            for candidate in item.get("candidate_alternatives") or []
        )
        before = "<br>".join(html.escape(value) for value in item.get("nearby_dialogue", {}).get("before", []))
        after = "<br>".join(html.escape(value) for value in item.get("nearby_dialogue", {}).get("after", []))
        image_tags: list[str] = []
        for frame in item.get("evidence_frames") or []:
            frame_path = html.escape(str(frame.get("path") or "").replace("\\", "/"))
            image_tags.append(f'<img loading="lazy" src="{frame_path}" width="280">')
        images = " ".join(image_tags)
        recommendation = item.get("recommended_character_name") or "Chưa đủ bằng chứng"
        query = str(item.get("research_query") or "")
        search_url = f"https://www.google.com/search?q={quote_plus(query)}"
        cards.append(
            "<article>"
            f"<h2>Tập {html.escape(str(item.get('episode')))} / cue {html.escape(str(item.get('cue')))} — {html.escape(recommendation)}</h2>"
            f"<p><b>Time:</b> {item.get('start')}–{item.get('end')}s · <b>Trạng thái:</b> {html.escape(str(item.get('recommendation_status')))}</p>"
            f"<p><b>Lời thoại:</b> {html.escape(str(item.get('text') or ''))}</p>"
            f"<p><b>Đang gán:</b> {html.escape(str(item.get('current_character_name') or 'chưa có'))} · <b>Ứng viên ngữ cảnh:</b> {html.escape(recommendation)}</p>"
            f"<p><b>Xưng hô:</b> {html.escape(str(item.get('translation_context', {}).get('hint') or ''))}</p>"
            f'<p><a href="{html.escape(search_url)}" target="_blank" rel="noreferrer">Tìm hồ sơ nhân vật</a> · <code>{html.escape(query)}</code></p>'
            f"<details><summary>Ngữ cảnh trước/sau</summary><p>{before}</p><hr><p>{after}</p></details>"
            f"<details><summary>Ứng viên và lý do</summary><p>{candidates}</p></details>"
            f"<div>{images}</div>"
            "</article>"
        )
    content = "<!doctype html><html lang='vi'><meta charset='utf-8'><title>Story context diagnosis</title>"
    content += "<style>body{font:15px system-ui;max-width:1450px;margin:24px auto;background:#111;color:#eee}article{border:1px solid #444;padding:16px;margin:16px 0}img{margin:4px;vertical-align:top}a{color:#8ecbff}code{color:#ccc}summary{cursor:pointer}</style>"
    content += f"<h1>{html.escape(title)}</h1><p>Đây là đề xuất ngữ cảnh, không tự động xác nhận người nói. Phải nghe video trước khi áp dụng.</p>"
    content += "".join(cards) or "<p>Không có cue cần chẩn đoán.</p>"
    content += "</html>\n"
    path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--character-profile", type=Path)
    parser.add_argument("--pronoun-rules", type=Path)
    parser.add_argument("--evidence-index", type=Path)
    parser.add_argument("--episodes", nargs="+", default=[])
    parser.add_argument("--mode", choices=("unresolved", "all"), default="unresolved")
    parser.add_argument("--window", type=int, default=4)
    parser.add_argument("--max-cues", type=int, default=0, help="0 means unlimited")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    requested = {str(value).zfill(2) for value in args.episodes}
    profile_data = json.loads(args.character_profile.resolve().read_text(encoding="utf-8")) if args.character_profile else None
    rules = merge_rules(json.loads(args.pronoun_rules.resolve().read_text(encoding="utf-8")) if args.pronoun_rules else None)
    evidence_by_key = load_evidence(args.evidence_index)
    reports = sorted(args.reports_dir.resolve().glob("*.named.segments.json"))
    if requested:
        filtered_reports: list[Path] = []
        for path in reports:
            report_data = json.loads(path.read_text(encoding="utf-8"))
            if episode_code(report_data, path) in requested:
                filtered_reports.append(path)
        reports = filtered_reports
    if not reports:
        raise SystemExit("Không tìm thấy named report phù hợp")

    diagnosis_rows: list[dict[str, Any]] = []
    episode_results: list[dict[str, Any]] = []
    for report_path in reports:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        code = episode_code(report, report_path)
        rows = [dict(row) for row in report.get("kept") or []]
        characters = report.get("character_registry") or {}
        if profile_data:
            profile = normalize_profile(profile_data) or {"characters": characters, "relations": {}}
        else:
            profile = normalize_profile({"characters": characters, "relations": {}}) or {"characters": characters, "relations": {}}
        profile["characters"] = {**characters, **(profile.get("characters") or {})}
        route_cast = {str(value).strip() for value in ((report.get("route_context") or {}).get("route_cast") or []) if str(value).strip()}
        scene_objects, scene_lookup = build_scene_index(rows)
        scene_reports = [scene_summary(scene, rows, characters) for scene in scene_objects]
        local_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if args.mode == "unresolved" and not row.get("needs_review"):
                continue
            scene_id = str(row.get("story_scene_id"))
            diagnosis = diagnose_row(
                row,
                index,
                rows,
                scene_lookup[scene_id],
                characters,
                profile,
                rules,
                route_cast,
                evidence_by_key,
                code,
                base_title(report),
                max(1, args.window),
            )
            local_rows.append(diagnosis)
        if args.max_cues > 0:
            local_rows = local_rows[: args.max_cues]
        diagnosis_rows.extend(local_rows)
        episode_results.append(
            {
                "episode": code,
                "report": str(report_path.resolve()),
                "cues": len(rows),
                "unresolved_cues": sum(1 for row in rows if row.get("needs_review")),
                "diagnosed_cues": len(local_rows),
                "scenes": scene_reports,
            }
        )

    title = base_title(json.loads(reports[0].read_text(encoding="utf-8")))
    output = {
        "schema_version": 1,
        "mode": "story_context_diagnosis",
        "title": title,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "warning": "Recommendations use subtitle context and profile rules. They do not replace listening to the video and never overwrite character_id.",
        "episodes": episode_results,
        "cues": diagnosis_rows,
        "summary": {
            "episodes": len(episode_results),
            "cues_diagnosed": len(diagnosis_rows),
            "context_supported": sum(1 for row in diagnosis_rows if row["recommendation_status"] == "context_supported"),
            "weak_context_candidate": sum(1 for row in diagnosis_rows if row["recommendation_status"] == "weak_context_candidate"),
            "ambiguous": sum(1 for row in diagnosis_rows if row["recommendation_status"] == "ambiguous"),
            "would_change_label": sum(1 for row in diagnosis_rows if row["recommendation_would_change_label"]),
        },
    }
    json_path = args.output_dir / "story-context-diagnosis.json"
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    csv_path = args.output_dir / "story-context-diagnosis.csv"
    columns = [
        "episode", "cue", "start", "end", "scene", "text", "current_character_id", "current_character_name",
        "recommended_character_id", "recommended_character_name", "recommendation_status", "recommendation_would_change_label",
        "voice_score", "voice_margin", "context_margin", "addressed_character", "plot_signals", "pronoun_signals",
        "translation_hint", "candidate_alternatives", "nearby_before", "nearby_after", "evidence_frames", "do_not_auto_apply",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in diagnosis_rows:
            flat = dict(row)
            flat["addressed_character"] = (row.get("addressed_character") or {}).get("character_name")
            flat["plot_signals"] = ",".join(row.get("plot_signals") or [])
            flat["pronoun_signals"] = ",".join(row.get("pronoun_signals") or [])
            flat["translation_hint"] = row.get("translation_context", {}).get("hint")
            flat["candidate_alternatives"] = " | ".join(
                f"{item['character_id']}:{item['context_score']}" for item in row.get("candidate_alternatives") or []
            )
            flat["nearby_before"] = " | ".join(row.get("nearby_dialogue", {}).get("before", []))
            flat["nearby_after"] = " | ".join(row.get("nearby_dialogue", {}).get("after", []))
            flat["evidence_frames"] = " | ".join(frame.get("path", "") for frame in row.get("evidence_frames") or [])
            writer.writerow(flat)
    html_path = args.output_dir / "story-context-review.html"
    write_html(html_path, diagnosis_rows, title)
    print(f"EPISODES={len(episode_results)}")
    print(f"CUES_DIAGNOSED={len(diagnosis_rows)}")
    print(f"CONTEXT_SUPPORTED={output['summary']['context_supported']}")
    print(f"WEAK_CONTEXT_CANDIDATE={output['summary']['weak_context_candidate']}")
    print(f"AMBIGUOUS={output['summary']['ambiguous']}")
    print(f"WOULD_CHANGE_LABEL={output['summary']['would_change_label']}")
    print(f"JSON={json_path.resolve()}")
    print(f"CSV={csv_path.resolve()}")
    print(f"HTML={html_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
