"""Vector storage in the same knowledge.db via sqlite-vec.

vec_items rowids match embedding_chunks rowids; embedding_cache keys
(item_id, model) with a content hash so nothing is ever re-embedded
unless its text changes or the model changes. embedding_chunks stores
the chunk text so search can return the exact matching passage.

Switching embedding models changes vector dimensions; ensure_vec_tables
detects the mismatch and rebuilds the vector table (embedding_cache is
keyed by model, so everything re-embeds under the new model on the next
km embed).
"""
from __future__ import annotations

import re
import sqlite3
import struct
from datetime import datetime, timezone
from typing import Callable, Optional

from km.db import try_load_sqlite_vec
from km.embedding.chunking import content_for_item
from km.embedding.embedder import Embedder, content_hash


def serialize_f32(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _existing_vec_dims(conn: sqlite3.Connection) -> Optional[int]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='vec_items'"
    ).fetchone()
    if not row or not row["sql"]:
        return None
    match = re.search(r"float\[(\d+)\]", row["sql"])
    return int(match.group(1)) if match else None


def ensure_vec_tables(conn: sqlite3.Connection, dims: int) -> bool:
    if not try_load_sqlite_vec(conn):
        return False
    existing = _existing_vec_dims(conn)
    if existing is not None and existing != dims:
        # model switch: old vectors are useless at the new dimension count
        conn.execute("DROP TABLE vec_items")
        conn.execute("DELETE FROM embedding_chunks")
        conn.commit()
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_items USING vec0(embedding float[{dims}])"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS embedding_chunks(
             id INTEGER PRIMARY KEY,
             item_id INTEGER NOT NULL REFERENCES items(id),
             chunk_idx INTEGER NOT NULL,
             text TEXT,
             UNIQUE(item_id, chunk_idx)
           )"""
    )
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(embedding_chunks)")}
    if "text" not in cols:
        conn.execute("ALTER TABLE embedding_chunks ADD COLUMN text TEXT")
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
        """SELECT i.*, ct.text AS body
           FROM items i
           LEFT JOIN embedding_cache c ON c.item_id = i.id AND c.model = ?
           LEFT JOIN content ct ON ct.item_id = i.id AND ct.ok = 1
           WHERE c.item_id IS NULL""",
        (embedder.model_name,),
    ).fetchall()

    embedded = 0
    now = datetime.now(timezone.utc).isoformat()
    pending: list[tuple[int, int, str]] = []  # (item_id, chunk_idx, text)
    hashes: dict[int, str] = {}
    for row in rows:
        chunks = content_for_item(row, body=row["body"])
        if not chunks:
            conn.execute(
                "INSERT OR REPLACE INTO embedding_cache VALUES (?,?,?,?)",
                (row["id"], "", embedder.model_name, now),
            )
            continue
        hashes[row["id"]] = content_hash(chunks)
        for idx, chunk in enumerate(chunks):
            pending.append((row["id"], idx, chunk))

    # drop every stale chunk for the items being re-embedded: a shrunk item
    # must not leave orphan high-idx chunks behind
    for item_id in {item_id for item_id, _, _ in pending}:
        for old in conn.execute(
            "SELECT id FROM embedding_chunks WHERE item_id=?", (item_id,)
        ).fetchall():
            conn.execute("DELETE FROM vec_items WHERE rowid=?", (old["id"],))
        conn.execute("DELETE FROM embedding_chunks WHERE item_id=?", (item_id,))

    for start in range(0, len(pending), batch_size):
        batch = pending[start:start + batch_size]
        vectors = embedder.encode([text for _, _, text in batch])
        for (item_id, chunk_idx, text), vector in zip(batch, vectors):
            cur = conn.execute(
                "INSERT INTO embedding_chunks(item_id, chunk_idx, text) VALUES (?,?,?)",
                (item_id, chunk_idx, text),
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


def similar_items(
    conn: sqlite3.Connection, item_id: int, limit: int = 8
) -> list[tuple[int, float]]:
    """Nearest neighbors of an item's own embedding, excluding itself.

    Uses the item's first chunk as the anchor. Returns [] when the item
    has no embedding yet or sqlite-vec is unavailable.
    """
    if not try_load_sqlite_vec(conn):
        return []
    chunk = conn.execute(
        "SELECT id FROM embedding_chunks WHERE item_id=? ORDER BY chunk_idx LIMIT 1",
        (item_id,),
    ).fetchone()
    if not chunk:
        return []
    vec = conn.execute(
        "SELECT embedding FROM vec_items WHERE rowid=?", (chunk["id"],)
    ).fetchone()
    if not vec:
        return []
    rows = conn.execute(
        """SELECT c.item_id, min(v.distance) AS distance
           FROM (
             SELECT rowid, distance FROM vec_items
             WHERE embedding MATCH ? AND k = ?
           ) v JOIN embedding_chunks c ON c.id = v.rowid
           WHERE c.item_id != ?
           GROUP BY c.item_id ORDER BY distance LIMIT ?""",
        (vec["embedding"], (limit + 1) * 4, item_id, limit),
    ).fetchall()
    return [(r["item_id"], r["distance"]) for r in rows]


def vector_search(
    conn: sqlite3.Connection, query_vector: list[float], limit: int = 100
) -> list[dict]:
    """Nearest chunks deduped per item, best chunk kept.

    Returns [{"item_id", "distance", "passage"}] best-first; passage is
    the text of the closest matching chunk (None for pre-migration rows).
    """
    rows = conn.execute(
        """SELECT c.item_id, v.distance, c.text
           FROM (
             SELECT rowid, distance FROM vec_items
             WHERE embedding MATCH ? AND k = ?
           ) v JOIN embedding_chunks c ON c.id = v.rowid
           ORDER BY v.distance""",
        (serialize_f32(query_vector), limit * 2),
    ).fetchall()
    out: list[dict] = []
    seen: set[int] = set()
    for r in rows:
        if r["item_id"] in seen:
            continue
        seen.add(r["item_id"])
        out.append({"item_id": r["item_id"], "distance": r["distance"], "passage": r["text"]})
        if len(out) >= limit:
            break
    return out
