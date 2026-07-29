"""Embedding backends. Local sentence-transformers by default (MPS on
Apple Silicon, nothing leaves the machine). Optional API backend behind
config, off by default."""
from __future__ import annotations

import hashlib
from typing import Protocol

from km.config import Config


class Embedder(Protocol):
    model_name: str
    dims: int

    def encode(self, texts: list[str]) -> list[list[float]]: ...
    def encode_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "embeddings need the embed extras: uv sync --extra embed"
            ) from exc
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self.dims = self._model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(
            texts, normalize_embeddings=True, show_progress_bar=False, batch_size=64
        )
        return [v.tolist() for v in vectors]

    def encode_query(self, text: str) -> list[float]:
        # bge models want a query instruction prefix for retrieval
        prefix = "Represent this sentence for searching relevant passages: "
        if "bge" in self.model_name.lower():
            text = prefix + text
        return self.encode([text])[0]


_CACHE: dict[str, Embedder] = {}


def get_embedder(cfg: Config) -> Embedder:
    if cfg.embedding.backend == "local":
        if cfg.embedding.model not in _CACHE:
            _CACHE[cfg.embedding.model] = LocalEmbedder(cfg.embedding.model)
        return _CACHE[cfg.embedding.model]
    raise RuntimeError(
        f"embedding backend {cfg.embedding.backend!r} not configured; "
        "set embedding.backend: local in config.yaml (API backends: add keys and "
        "implement per README)"
    )


def content_hash(chunks: list[str]) -> str:
    h = hashlib.sha256()
    for chunk in chunks:
        h.update(chunk.encode())
        h.update(b"\x00")
    return h.hexdigest()
