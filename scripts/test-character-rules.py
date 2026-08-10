#!/usr/bin/env python3
"""Small dependency-free smoke test for the character rule engine."""

from character_rules import apply_character_rules


profile = {
    "title": "rule test",
    "speaker_map": {"SPEAKER_00": "older", "SPEAKER_01": "younger"},
    "characters": {
        "older": {"display_name": "Anh A", "age": 30, "gender": "male", "role": "adult"},
        "younger": {"display_name": "Em B", "age": 16, "gender": "female", "role": "student"},
    },
    "scene_targets": [{"start": 0, "end": 10, "speaker": "SPEAKER_00", "listener": "younger"}],
}

segments = [{"id": "1", "start": 1, "end": 2, "speaker": "SPEAKER_00", "text": "Chào em."}]
enriched, summary = apply_character_rules(segments, profile)

assert summary["enabled"] is True
assert enriched[0]["character_name"] == "Anh A"
assert enriched[0]["character_age_band"] == "adult"
assert enriched[0]["character_gender"] == "male"
assert enriched[0]["pronouns"]["self"] == "anh"
assert enriched[0]["pronouns"]["other"] == "em"
assert enriched[0]["pronouns"]["confidence"] == "heuristic"
print("CHARACTER_RULES_TEST=PASS")
