"""Free, local-only character and Vietnamese pronoun rules.

Speaker diarization can separate voices, but it cannot reliably know that a
voice belongs to a named fictional character.  A small per-title profile maps
diarization IDs to characters and stores human-reviewed age/gender metadata.
The rule engine then adds that metadata to the JSON report and chooses a
pronoun relationship for each cue when a listener is configured.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


DEFAULT_PRONOUN_RULES: dict[str, Any] = {
    "fallback": {"self": "tôi", "other": "bạn", "confidence": "unknown"},
    "same_age_friendly": {"self": "tớ", "other": "cậu", "confidence": "heuristic"},
    "older_to_younger": {
        "self": {"male": "anh", "female": "chị", "unknown": "tôi"},
        "other": "em",
        "confidence": "heuristic",
    },
    "younger_to_older": {
        "self": "em",
        "other": {"male": "anh", "female": "chị", "unknown": "cô/chú"},
        "confidence": "heuristic",
    },
    "adult_to_child": {
        "self": {"male": "chú", "female": "cô", "unknown": "tôi"},
        "other": "cháu",
        "confidence": "heuristic",
    },
    "child_to_adult": {
        "self": "cháu",
        "other": {"male": "chú", "female": "cô", "unknown": "cô/chú"},
        "confidence": "heuristic",
    },
    "formal": {"self": "tôi", "other": "bạn", "confidence": "profile"},
}


def normalize_profile(profile: dict[str, Any] | None) -> dict[str, Any] | None:
    if not profile:
        return None
    normalized = deepcopy(profile)
    characters = normalized.get("characters") or {}
    if isinstance(characters, list):
        characters = {
            str(item.get("id")): item
            for item in characters
            if isinstance(item, dict) and item.get("id")
        }
    normalized["characters"] = characters if isinstance(characters, dict) else {}
    normalized["speaker_map"] = {
        str(key).strip(): str(value).strip()
        for key, value in (normalized.get("speaker_map") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    normalized["relations"] = normalized.get("relations") or {}
    normalized["scene_targets"] = normalized.get("scene_targets") or []
    return normalized


def merge_rules(rules: dict[str, Any] | None) -> dict[str, Any]:
    merged = deepcopy(DEFAULT_PRONOUN_RULES)
    if not rules:
        return merged
    for key, value in rules.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _age(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if 0 < number < 130 else None


def age_band(value: Any) -> str:
    age = _age(value)
    if age is None:
        return "unknown"
    if age <= 12:
        return "child"
    if age <= 17:
        return "teen"
    if age <= 59:
        return "adult"
    return "senior"


def _gender(value: Any) -> str:
    raw = str(value or "unknown").strip().lower()
    aliases = {
        "nam": "male",
        "male": "male",
        "m": "male",
        "nữ": "female",
        "nu": "female",
        "female": "female",
        "f": "female",
        "khác": "unknown",
        "unknown": "unknown",
    }
    return aliases.get(raw, "unknown")


def _character_age_band(character: dict[str, Any] | None) -> str:
    """Prefer a reviewed age_band when an exact age is unavailable."""
    if not character:
        return "unknown"
    declared = str(character.get("age_band") or "").strip().lower()
    if declared in {"child", "teen", "adult", "senior", "unknown"}:
        return declared
    return age_band(character.get("age"))


def _name(character_id: str | None, character: dict[str, Any] | None) -> str | None:
    if not character:
        return None
    return str(character.get("display_name") or character.get("name") or character_id or "").strip() or None


def _value_for_gender(value: Any, gender: str, fallback: str = "tôi") -> str:
    if isinstance(value, dict):
        return str(value.get(gender) or value.get("unknown") or fallback)
    return str(value or fallback)


def _relation_override(relations: dict[str, Any], speaker_id: str | None, listener_id: str | None) -> dict[str, Any] | None:
    if not speaker_id or not listener_id:
        return None
    direct = relations.get(f"{speaker_id}->{listener_id}")
    if isinstance(direct, dict):
        return direct
    nested = relations.get(speaker_id)
    if isinstance(nested, dict) and isinstance(nested.get(listener_id), dict):
        return nested[listener_id]
    return None


def _find_listener(profile: dict[str, Any], item: dict[str, Any], character_id: str | None) -> str | None:
    raw_speaker = str(item.get("speaker") or "").strip()
    start = float(item.get("start") or 0.0)
    for target in profile.get("scene_targets") or []:
        if not isinstance(target, dict):
            continue
        target_speaker = str(target.get("speaker") or target.get("speaker_id") or target.get("character") or "").strip()
        if target_speaker and target_speaker not in {raw_speaker, character_id or ""}:
            continue
        try:
            target_start = float(target.get("start", 0))
            target_end = float(target.get("end", 10**12))
        except (TypeError, ValueError):
            continue
        if target_start <= start < target_end:
            listener = str(target.get("listener") or target.get("target") or "").strip()
            if listener:
                return listener

    defaults = profile.get("default_listener") or {}
    if isinstance(defaults, dict):
        return str(defaults.get(character_id) or defaults.get(raw_speaker) or "").strip() or None
    return None


def _heuristic_relation(speaker: dict[str, Any] | None, listener: dict[str, Any] | None) -> str:
    if not speaker or not listener:
        return "fallback"
    speaker_age = _age(speaker.get("age"))
    listener_age = _age(listener.get("age"))
    speaker_band = _character_age_band(speaker)
    listener_band = _character_age_band(listener)
    speaker_role = str(speaker.get("role") or "").lower()
    listener_role = str(listener.get("role") or "").lower()
    if (
        speaker_role in {"teacher", "parent", "adult_guardian"}
        or (speaker_age is None and speaker_band == "adult" and listener_band in {"child", "teen"})
    ):
        return "adult_to_child"
    if (
        listener_role in {"teacher", "parent", "adult_guardian"}
        or (listener_age is None and listener_band == "adult" and speaker_band in {"child", "teen"})
    ):
        return "child_to_adult"
    if speaker_age is not None and listener_age is not None:
        if speaker_age >= listener_age + 8:
            return "older_to_younger"
        if listener_age >= speaker_age + 8:
            return "younger_to_older"
        if abs(speaker_age - listener_age) <= 3:
            return "same_age_friendly"
    return "fallback"


def choose_pronouns(
    profile: dict[str, Any],
    rules: dict[str, Any],
    speaker_id: str | None,
    listener_id: str | None,
) -> dict[str, Any]:
    characters = profile.get("characters") or {}
    speaker = characters.get(speaker_id) if speaker_id else None
    listener = characters.get(listener_id) if listener_id else None
    speaker_gender = _gender((speaker or {}).get("gender"))
    listener_gender = _gender((listener or {}).get("gender"))

    override = _relation_override(profile.get("relations") or {}, speaker_id, listener_id)
    if override:
        return {
            "relation": str(override.get("relation") or "profile_override"),
            "self": str(override.get("self") or "tôi"),
            "other": str(override.get("other") or "bạn"),
            "confidence": str(override.get("confidence") or "profile"),
            "reason": "explicit_profile_relation",
        }

    if not listener_id:
        default_self = (speaker or {}).get("default_self") or (speaker or {}).get("self_pronoun")
        return {
            "relation": "unknown_listener",
            "self": str(default_self or rules["fallback"]["self"]),
            "other": str(rules["fallback"]["other"]),
            "confidence": "unknown",
            "reason": "listener_not_configured",
        }

    relation = _heuristic_relation(speaker, listener)
    template = rules.get(relation) or rules["fallback"]
    return {
        "relation": relation,
        "self": _value_for_gender(template.get("self"), speaker_gender),
        "other": _value_for_gender(template.get("other"), listener_gender, "bạn"),
        "confidence": str(template.get("confidence") or "heuristic"),
        "reason": "age_role_gender_rules",
    }


def apply_character_rules(
    segments: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    rules: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enrich subtitle segments with character metadata and pronoun choices."""
    normalized = normalize_profile(profile)
    if not normalized:
        return segments, {"enabled": False, "reason": "no_character_profile"}

    character_map = normalized.get("characters") or {}
    speaker_map = normalized.get("speaker_map") or {}
    merged_rules = merge_rules(rules)
    anonymous_labels: dict[str, str] = {}
    speaker_summary: dict[str, Any] = {}
    unmapped: set[str] = set()
    no_speaker_count = 0
    pronoun_review_count = 0
    enriched: list[dict[str, Any]] = []

    for item in segments:
        output = dict(item)
        raw_speaker = str(item.get("speaker") or "").strip()
        character_id = speaker_map.get(raw_speaker)
        character = character_map.get(character_id) if character_id else None

        if character:
            display_name = _name(character_id, character) or raw_speaker
            output["character_id"] = character_id
            output["character_name"] = display_name
            output["character_age"] = _age(character.get("age"))
            output["character_age_band"] = _character_age_band(character)
            output["character_age_confidence"] = character.get("age_confidence", "profile")
            output["character_gender"] = _gender(character.get("gender"))
            output["character_gender_confidence"] = character.get("gender_confidence", "profile")
            output["character_role"] = character.get("role")
            output["character_metadata_status"] = normalized.get("metadata_status", "profile")
            output["speaker_label"] = display_name
            speaker_confidence = "profile"
        else:
            if raw_speaker not in anonymous_labels:
                anonymous_labels[raw_speaker] = f"Người nói {len(anonymous_labels) + 1}"
            output["character_id"] = None
            output["character_name"] = None
            output["character_age"] = None
            output["character_age_band"] = "unknown"
            output["character_age_confidence"] = "unknown"
            output["character_gender"] = "unknown"
            output["character_gender_confidence"] = "unknown"
            output["character_role"] = None
            output["character_metadata_status"] = "unknown"
            output["speaker_label"] = anonymous_labels[raw_speaker] if raw_speaker else "Người nói"
            speaker_confidence = "unknown"
            if raw_speaker:
                unmapped.add(raw_speaker)
            else:
                no_speaker_count += 1

        listener_id = _find_listener(normalized, item, character_id)
        pronouns = choose_pronouns(normalized, merged_rules, character_id, listener_id)
        listener = character_map.get(listener_id) if listener_id else None
        pronouns["listener_id"] = listener_id
        pronouns["listener_name"] = _name(listener_id, listener)
        if pronouns["confidence"] == "unknown":
            pronoun_review_count += 1

        output["speaker_confidence"] = speaker_confidence
        output["pronouns"] = pronouns
        output["needs_review"] = []
        if speaker_confidence != "profile":
            output["needs_review"].append("map_speaker_to_character")
        if not listener_id:
            output["needs_review"].append("set_listener_or_scene_target")
        if output["needs_review"]:
            output["decision_note"] = ";".join(output["needs_review"])

        if raw_speaker not in speaker_summary:
            speaker_summary[raw_speaker or "unknown"] = {
                "speaker": raw_speaker or None,
                "character_id": character_id,
                "character_name": output["character_name"],
                "age": output["character_age"],
                "age_band": output["character_age_band"],
                "age_confidence": output["character_age_confidence"],
                "gender": output["character_gender"],
                "gender_confidence": output["character_gender_confidence"],
                "role": output["character_role"],
                "metadata_status": output["character_metadata_status"],
                "confidence": speaker_confidence,
            }
        enriched.append(output)

    summary = {
        "enabled": True,
        "profile_name": normalized.get("title") or normalized.get("name"),
        "speaker_map": speaker_map,
        "characters": character_map,
        "speakers": speaker_summary,
        "unmapped_speakers": sorted(unmapped),
        "segments_without_speaker": no_speaker_count,
        "pronoun_segments_needing_review": pronoun_review_count,
        "rules": merged_rules,
        "limitations": [
            "Diarization separates voices but does not identify a fictional character by name.",
            "Age and gender are profile metadata and must be reviewed; they are not reliable voice facts.",
            "Pronouns need a listener or scene target; otherwise the engine marks the cue for review.",
        ],
    }
    return enriched, summary
