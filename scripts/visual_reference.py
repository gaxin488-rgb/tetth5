"""Optional local visual-reference embeddings using an open CLIP model.

Reference images should preferably be tightly cropped character faces.  The
matcher is a visual candidate signal for anime frames, not a guarantee of
identity; voice matching remains the primary signal for dialogue speakers.
"""

from __future__ import annotations

from typing import Any, Iterable


def load_clip(model_name: str, device: str):
    import torch
    from transformers import AutoProcessor, CLIPModel

    processor = AutoProcessor.from_pretrained(model_name)
    model = CLIPModel.from_pretrained(model_name).to(device)
    model.eval()
    return model, processor, torch


def encode_images(images: Iterable[Any], model: Any, processor: Any, torch: Any, device: str) -> list[list[float]]:
    image_list = list(images)
    if not image_list:
        return []
    inputs = processor(images=image_list, return_tensors="pt")
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch.no_grad():
        features = model.get_image_features(**inputs)
        features = features / features.norm(dim=-1, keepdim=True).clamp_min(1e-12)
    return features.detach().cpu().tolist()


def average_feature_vectors(vectors: Iterable[Any]) -> list[float]:
    values = [list(map(float, vector)) for vector in vectors if isinstance(vector, (list, tuple)) and vector]
    if not values:
        return []
    width = len(values[0])
    values = [value for value in values if len(value) == width]
    if not values:
        return []
    output = [sum(value[index] for value in values) / len(values) for index in range(width)]
    length = sum(value * value for value in output) ** 0.5
    return [value / length for value in output] if length else []
