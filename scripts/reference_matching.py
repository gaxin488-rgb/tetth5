"""Local reference matching helpers for anonymous diarization speakers.

The diarizer answers "who spoke when" using anonymous speaker IDs.  This
module compares those IDs with user-supplied voice or visual embeddings.  It
never invents a character name when the similarity is below the configured
threshold or the winning candidate is too close to the runner-up.
"""

from __future__ import annotations

from math import sqrt
from typing import Any, Iterable


def _vector(value: Any) -> list[float]:
    if not isinstance(value, (list, tuple)):
        return []
    output: list[float] = []
    for item in value:
        try:
            output.append(float(item))
        except (TypeError, ValueError):
            return []
    return output


def normalize_embedding(value: Any) -> list[float]:
    vector = _vector(value)
    length = sqrt(sum(item * item for item in vector))
    if not vector or length <= 0:
        return []
    return [item / length for item in vector]


def average_embeddings(values: Iterable[Any]) -> list[float]:
    vectors = [normalize_embedding(value) for value in values]
    vectors = [vector for vector in vectors if vector]
    if not vectors:
        return []
    width = len(vectors[0])
    compatible = [vector for vector in vectors if len(vector) == width]
    if not compatible:
        return []
    return normalize_embedding([
        sum(vector[index] for vector in compatible) / len(compatible)
        for index in range(width)
    ])


def cosine_similarity(left: Any, right: Any) -> float:
    left_vector = normalize_embedding(left)
    right_vector = normalize_embedding(right)
    if not left_vector or not right_vector or len(left_vector) != len(right_vector):
        return -1.0
    return sum(a * b for a, b in zip(left_vector, right_vector))


def extract_reference_embeddings(profile: dict[str, Any] | None, key: str = "voice") -> dict[str, list[float]]:
    """Read generated embeddings from a reference profile.

    Accepted shapes per character are ``embedding: [...]`` or
    ``embeddings: [[...], [...]]``.  The latter is averaged so several clean
    reference clips can be used for one character.
    """
    if not profile:
        return {}
    characters = profile.get("characters") or {}
    output: dict[str, list[float]] = {}
    for character_id, item in characters.items():
        if not isinstance(item, dict):
            continue
        embedding = item.get("embedding")
        if embedding is None:
            embedding = item.get(f"{key}_embedding")
        if embedding is None:
            embedding = item.get(f"{key}_embeddings")
        if embedding is None:
            embedding = item.get("embeddings")
        if isinstance(embedding, (list, tuple)) and embedding and isinstance(embedding[0], (list, tuple)):
            embedding = average_embeddings(embedding)
        normalized = normalize_embedding(embedding)
        if normalized:
            output[str(character_id)] = normalized
    return output


def match_embeddings(
    speaker_embeddings: dict[str, Any],
    reference_embeddings: dict[str, Any],
    *,
    threshold: float = 0.55,
    margin: float = 0.05,
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Match anonymous speaker vectors to reference vectors.

    Returns ``(accepted_speaker_map, diagnostics)``.  A match is accepted
    only when its score reaches ``threshold`` and beats the second candidate
    by at least ``margin``.  This conservative behavior avoids silently
    assigning the wrong fictional character.
    """
    accepted: dict[str, str] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    references = {
        str(character_id): normalize_embedding(embedding)
        for character_id, embedding in reference_embeddings.items()
        if normalize_embedding(embedding)
    }
    for speaker_id, speaker_embedding in speaker_embeddings.items():
        candidates = sorted(
            (
                (character_id, cosine_similarity(speaker_embedding, embedding))
                for character_id, embedding in references.items()
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        if not candidates:
            diagnostics[str(speaker_id)] = {
                "accepted": False,
                "reason": "no_reference_embeddings",
                "confidence": "unknown",
            }
            continue
        best_id, best_score = candidates[0]
        second_score = candidates[1][1] if len(candidates) > 1 else -1.0
        score_margin = best_score - second_score
        accepted_match = best_score >= threshold and score_margin >= margin
        confidence = "high" if accepted_match and best_score >= threshold + 0.1 else "review"
        diagnostics[str(speaker_id)] = {
            "accepted": accepted_match,
            "character_id": best_id,
            "score": round(best_score, 6),
            "second_score": round(second_score, 6),
            "margin": round(score_margin, 6),
            "threshold": threshold,
            "required_margin": margin,
            "confidence": confidence if accepted_match else "review",
            "reason": "voice_embedding_similarity" if accepted_match else "below_threshold_or_ambiguous",
        }
        if accepted_match:
            accepted[str(speaker_id)] = best_id
    return accepted, diagnostics


def apply_reference_map(profile: dict[str, Any] | None, speaker_map: dict[str, str]) -> dict[str, Any] | None:
    """Return a copy of a character profile with accepted matches merged in."""
    if profile is None:
        return None
    output = dict(profile)
    output["speaker_map"] = {
        **speaker_map,
        **(profile.get("speaker_map") or {}),
    }
    return output
