"""Multi-Tenant RAG Embedding Client Module.

Provides deterministic 1536-dimensional dense embedding generation matching
OpenAI text-embedding-3-small specifications, cosine similarity computation,
and vector synthesis utilities for offline testing and production retrieval.
"""

from __future__ import annotations

import functools
import hashlib
import math
import random
import re
from typing import Dict, List, Optional, Sequence, Union


def cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Compute cosine similarity between two float vectors.

    cos_sim(A, B) = (A . B) / (||A||_2 * ||B||_2)
    Returns a float clamped to the range [-1.0, 1.0]. Returns 0.0 if either vector is zero.
    """
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0

    dot_product = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0

    for a, b in zip(vec_a, vec_b):
        dot_product += a * b
        norm_a_sq += a * a
        norm_b_sq += b * b

    if norm_a_sq <= 0.0 or norm_b_sq <= 0.0:
        return 0.0

    sim = dot_product / (math.sqrt(norm_a_sq) * math.sqrt(norm_b_sq))
    # Clamp to avoid floating point precision overshoot beyond [-1.0, 1.0]
    return max(-1.0, min(1.0, float(sim)))


_WORD_VECTOR_CACHE: Dict[str, List[float]] = {}


def _get_word_vector(word: str, dimensions: int = 1536) -> List[float]:
    """Generate a deterministic unit vector for an individual word using SHA-256."""
    cache_key = f"{word}:{dimensions}"
    cached = _WORD_VECTOR_CACHE.get(cache_key)
    if cached is not None:
        return cached

    seed_bytes = hashlib.sha256(word.encode("utf-8")).digest()
    rng = random.Random(seed_bytes)
    raw_vector = [rng.gauss(0.0, 1.0) for _ in range(dimensions)]
    norm = math.sqrt(sum(x * x for x in raw_vector))
    if norm > 0.0:
        unit_vector = [float(x / norm) for x in raw_vector]
    else:
        unit_vector = [0.0] * dimensions

    _WORD_VECTOR_CACHE[cache_key] = unit_vector
    return unit_vector


def create_deterministic_embedding(text: str, dimensions: int = 1536) -> List[float]:
    """Generate a deterministic, normalized 1536-dimensional float vector for text.

    Uses word-level projection to ensure texts sharing vocabulary exhibit higher
    semantic cosine similarity, while unrelated texts yield near-orthogonal vectors.
    Guarantees L2 unit norm ||v||_2 = 1.0.
    """
    if dimensions <= 0:
        raise ValueError("Dimensions must be a positive integer.")

    clean_text = text.strip() if text else ""
    tokens = re.findall(r"\w+", clean_text.lower(), flags=re.UNICODE)

    if not tokens:
        # Fallback deterministic vector for empty/whitespace input
        return _get_word_vector("__empty_text_placeholder__", dimensions)

    # Accumulate word vectors with frequency weighting
    accum = [0.0] * dimensions
    for token in tokens:
        word_vec = _get_word_vector(token, dimensions)
        for i in range(dimensions):
            accum[i] += word_vec[i]

    # Calculate L2 norm
    norm = math.sqrt(sum(x * x for x in accum))
    if norm <= 0.0:
        return _get_word_vector("__zero_norm_fallback__", dimensions)

    return [float(x / norm) for x in accum]


def generate_vector_with_similarity(
    base_vector: List[float],
    target_similarity: float,
) -> List[float]:
    """Synthesize a unit vector having an exact cosine similarity to base_vector.

    Uses Gram-Schmidt orthogonalization:
        w = s * u + sqrt(1 - s^2) * v, where v is orthogonal to u and ||v|| = 1.
    """
    if not -1.0 <= target_similarity <= 1.0:
        raise ValueError("target_similarity must be between -1.0 and 1.0 inclusive.")

    # Normalize base_vector
    norm_u = math.sqrt(sum(x * x for x in base_vector))
    if norm_u <= 0.0:
        raise ValueError("base_vector must have non-zero norm.")
    u = [x / norm_u for x in base_vector]
    dim = len(u)

    if abs(target_similarity - 1.0) < 1e-9:
        return list(u)
    if abs(target_similarity - (-1.0)) < 1e-9:
        return [-x for x in u]

    # Pick a deterministic reference vector not collinear with u
    rng = random.Random(42)
    rand_ref = [rng.gauss(0.0, 1.0) for _ in range(dim)]

    # Project out component along u: v_raw = rand_ref - (rand_ref . u) * u
    dot_proj = sum(r * ui for r, ui in zip(rand_ref, u))
    v_raw = [r - dot_proj * ui for r, ui in zip(rand_ref, u)]
    norm_v = math.sqrt(sum(x * x for x in v_raw))

    if norm_v < 1e-9:
        # Fallback shift if accidentally collinear
        rand_ref[0] += 1.0
        dot_proj = sum(r * ui for r, ui in zip(rand_ref, u))
        v_raw = [r - dot_proj * ui for r, ui in zip(rand_ref, u)]
        norm_v = math.sqrt(sum(x * x for x in v_raw))

    v = [x / norm_v for x in v_raw]

    # Construct target vector
    s = target_similarity
    ortho_weight = math.sqrt(max(0.0, 1.0 - s * s))
    w = [s * ui + ortho_weight * vi for ui, vi in zip(u, v)]

    # Normalize w to ensure floating point exactness
    norm_w = math.sqrt(sum(x * x for x in w))
    return [float(x / norm_w) for x in w]


class InMemoryEmbeddingClient:
    """Deterministic OpenAI text-embedding-3-small emulator.

    Generates 1536-dimensional dense float vectors normalized to unit length.
    """

    def __init__(
        self,
        dimensions: int = 1536,
        model: str = "text-embedding-3-small",
    ) -> None:
        self._dimensions = dimensions
        self._model = model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model(self) -> str:
        return self._model

    def embed(self, text: str) -> List[float]:
        """Generate a 1536-dimensional normalized vector for input text."""
        return create_deterministic_embedding(text, dimensions=self._dimensions)

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate normalized 1536-dimensional vectors for a batch of text strings."""
        return [self.embed(t) for t in texts]

    def get_embedding(self, text: str) -> List[float]:
        """Alias for embed matching standard naming conventions."""
        return self.embed(text)

    def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Alias for embed_batch matching standard naming conventions."""
        return self.embed_batch(texts)

    def create_embedding(self, text: str) -> List[float]:
        """Alias for embed matching OpenAI API conventions."""
        return self.embed(text)

    def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Alias for embed_batch matching OpenAI API conventions."""
        return self.embed_batch(texts)

    # UML 2.5 CamelCase Aliases
    getEmbedding = get_embedding
    getEmbeddings = get_embeddings
    createEmbedding = create_embedding
    createEmbeddings = create_embeddings


# Backwards compatibility and architecture alias
OpenAIEmbeddingClient = InMemoryEmbeddingClient


@functools.lru_cache(maxsize=1)
def get_embedding_client() -> InMemoryEmbeddingClient:
    """Return the global singleton InMemoryEmbeddingClient instance."""
    return InMemoryEmbeddingClient()
