"""Local cross-encoder reranking: the precision stage after retrieval.

A bi-encoder (bge) retrieves by embedding query and passage separately;
a cross-encoder reads them together and scores actual relevance, which
is what separates "mentions the same words" from "is the thing you
meant". Runs locally (ms-marco MiniLM, ~80MB, downloads once), costs
nothing per query, and loads lazily so nothing pays for it until the
first deep search.
"""
from __future__ import annotations

from typing import Optional

MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"

_CACHE: dict = {}


def get_reranker() -> Optional[object]:
    """The cached CrossEncoder, or None when the embed extras are missing."""
    if "model" not in _CACHE:
        try:
            from sentence_transformers import CrossEncoder

            _CACHE["model"] = CrossEncoder(MODEL)
        except Exception:
            _CACHE["model"] = None
    return _CACHE["model"]


def rerank_pairs(reranker, query: str, texts: list[str]) -> list[float]:
    """Relevance score per text for this query, higher is better."""
    if not texts:
        return []
    return [float(s) for s in reranker.predict([(query, t) for t in texts])]
