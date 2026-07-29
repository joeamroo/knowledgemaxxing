"""Vector storage in the same knowledge.db via sqlite-vec.

vec_items rowids match embedding_chunks rowids; embedding_cache keys
(item_id, model) with a content hash so nothing is ever re-embedded
unless its text changes or the model changes.
"""
from __future__ import annotations

import sqlite3
import struct
from datetime import datetime, timezone
from typing import Callable, Optional

from km.db import try_load_sqlite_vec
from km.embedding.chunking import content_for_item
from km.embedding.embedder import Embedder, content_hash


def serialize_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def ensure_vec_tables(conn: sqlite3.Connection, dims: int) -> bool:
    if not try_load_sqlite_vec(conn):
        return False
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(embedding float[{dims}])"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embedding_chunks(
             id INTEGER PRIMARY KEY,
             item_id INTEGER NOT NULL REFERENCES items(id),
             chunk_idx INTEGER NOT NULL,
             UNIQUE(item_id, chunk_idx)
           )"""
    )
    return True


def embed_pending(
    conn: sqlite3.Connection,
    embedder: Embedder,
    batch_size: int = 256,
    progress: Optional[Callable[[int, int], None]] = None,
) -> int:
    """Embed every item missing from embedding_cache for this model."""
    if not ensure_vec_tables(conn, embedder.dims):
        raise RuntimeError("sqlite-vec is not installed: uv sync --extra embed")

    rows = conn.execute(
        """SELECT i.* FROM items i
           LEFT JOIN embedding_cache c ON c.item_id = i.id AND c.model = ?
           WHERE c.item_id IS NULL""",
        (embedder.model_name,),
    ).fetchall()

    embedded = 0
    now = datetime.now(timezone.utc).isoformat()
    pending: list[tuple[int, int, str]] = []  # (item_id, chunk_idx, text)
    hashes: dict[int, str] = {}
    for row in rows:
        chunks = content_for_item(row)
        if not chunks:
            conn.execute(
                "INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?)",
                (row["id"], "", embedder.model_name, now),
            )
            continue
        hashes[row["id"]] = content_hash(chunks)
        for idx, chunk in enumerate(chunks):
            pending.append((row["id"], idx, chunk))

    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        vectors = embedder.encode([text for _, _, text in batch])
        for (item_id, chunk_idx, _), vector in zip(batch, vectors):
            # replace any stale chunk row for this (item, idx)
            old = conn.execute(
                "SELECT id FROM embedding_chunks WHERE item_id=? AND chunk_idx=?",
                (item_id, chunk_idx),
            ).fetchone()
            if old:
                conn.execute("DELETE FROM vec_items WHERE rowid=?", (old["id"],))
                conn.execute("DELETE FROM embedding_chunks WHERE id=?", (old["id"],))
            cur = conn.execute(
                "INSERT INTO embedding_chunks(item_id, chunk_idx) VALUES (?,?)",
                (item_id, chunk_idx),
            )
            conn.execute(
                "INSERT INTO vec_items(rowid, embedding) VALUES (?,?)",
                (cur.lastrowid, serialize_f32(vector)),
            )
            embedded += 1
        done_items = {item_id for item_id, _, _ in batch}
        for item_id in done_items:
            conn.execute(
                "INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?)",
                (item_id, hashes.get(item_id, ""), embedder.model_name, now),
            )
        conn.commit()
        if progress:
            progress(min(start + batch_size, len(pending)), len(pending))
    conn.commit()
    return embedded


def vector_search(
    conn: sqlite3.Connection, query_vector: list[float], limit: int = 100
) -> list[tuple[int, float]]:
    """Return [(item_id, distance)] nearest chunks, deduped per item."""
    rows = conn.execute(
        """SELECT c.item_id, min(v.distance) AS distance
           FROM (
             SELECT rowid, distance FROM vec_items
             WHERE embedding MATCH ? AND k = ?
           ) v JOIN embedding_chunks c ON c.id = v.rowid
           GROUP BY c.item_id ORDER BY distance""",
        (serialize_f32(query_vector), limit * 2),
    ).fetchall()
    return [(r["item_id"], r["distance"]) for r in rows[:limit]]
