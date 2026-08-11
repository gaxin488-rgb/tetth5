#!/usr/bin/env python3
"""Dependency-free smoke tests for reference matching decisions."""

from reference_matching import match_embeddings


accepted, diagnostics = match_embeddings(
    {
        "SPEAKER_00": [1.0, 0.0, 0.0],
        "SPEAKER_01": [0.0, 1.0, 0.0],
        "SPEAKER_02": [0.7, 0.7, 0.0],
    },
    {
        "haruka": [1.0, 0.0, 0.0],
        "sora": [0.0, 1.0, 0.0],
    },
    threshold=0.6,
    margin=0.1,
)

assert accepted == {"SPEAKER_00": "haruka", "SPEAKER_01": "sora"}
assert diagnostics["SPEAKER_02"]["accepted"] is False
assert diagnostics["SPEAKER_02"]["reason"] == "below_threshold_or_ambiguous"
print("REFERENCE_MATCHING_TEST=PASS")
