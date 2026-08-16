"""
Embedding provider — pluggable.

Default: on-device model via fastembed (ONNX runtime, no PyTorch).
No API key, no cloud. The model (~130MB) is downloaded once and cached under
~/.cache/fastembed, then runs locally on CPU.

To swap in a hosted provider later (Voyage / OpenAI / Bedrock), implement the
same `embed(text) -> list[float]` contract and wire it up in `get_embedder()`.
"""

from __future__ import annotations

import os
from functools import lru_cache

# BAAI/bge-small-en-v1.5 -> 384-dimensional, a strong small English model.
DEFAULT_MODEL = os.environ.get("LEARNINGS_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_DIM = int(os.environ.get("LEARNINGS_EMBED_DIM", "384"))


@lru_cache(maxsize=1)
def _model():
    # Imported lazily so the process starts fast and only pays the load cost
    # on the first embedding call.
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=DEFAULT_MODEL)


def embed(text: str) -> list[float]:
    """Embed a single string into a normalized vector of length EMBED_DIM."""
    vectors = list(_model().embed([text or ""]))
    vec = vectors[0]
    # fastembed returns a numpy array; normalize defensively so L2 distance
    # tracks cosine similarity regardless of the underlying model's behaviour.
    import numpy as np

    arr = np.asarray(vec, dtype="float32")
    norm = float(np.linalg.norm(arr))
    if norm > 0:
        arr = arr / norm
    return arr.tolist()


def learning_text(title: str, content: str, tags: list[str] | None) -> str:
    """Canonical text we embed for a learning: title + content + tags."""
    tag_line = f"\n\nTags: {', '.join(tags)}" if tags else ""
    return f"{title}\n\n{content}{tag_line}"
