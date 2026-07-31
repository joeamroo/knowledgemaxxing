"""Custom categories: extend the taxonomy by describing what you want.

Two halves:
- create_category(): from an explicit name+description, or from a plain
  instruction that Claude turns into one (needs credits).
- assign_local(): zero-shot assignment with NO API at all. The category
  description is embedded and matched against the archive's existing
  vectors; strong matches get a classification row. Claude passes can
  refine later; this works the moment a category is born.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

PROMPT_VERSION = "custom:v1"


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug[:40] or "category"


def create_category(conn: sqlite3.Connection, name: str, description: str,
                    source: str = "manual") -> dict:
    slug = _slugify(name)
    conn.execute(
        """INSERT OR REPLACE INTO custom_categories(slug, name, description, created_at, source)
           VALUES (?,?,?,?,?)""",
        (slug, name, description, datetime.now(timezone.utc).isoformat(), source))
    conn.commit()
    return {"slug": slug, "name": name, "description": description}


def create_category_ai(conn: sqlite3.Connection, cfg, instruction: str,
                       model: Optional[str] = None) -> dict:
    from km.classify.client import get_client, parse_json_response

    response = get_client().messages.create(
        model=model or cfg.classification.model,
        max_tokens=300,
        messages=[{"role": "user", "content": (
            "Design a category for a personal knowledge archive from this request. "
            'Reply with JSON only: {"name": "<2-3 words>", "description": '
            '"<2-3 sentences describing exactly what belongs in it, written so an '
            'embedding model can match items against it>"}. Request: ' + instruction)}])
    spec = parse_json_response(
        "".join(b.text for b in response.content if b.type == "text"))
    return create_category(conn, spec["name"], spec["description"], source="ai")


def assign_local(conn: sqlite3.Connection, cfg, slug: str,
                 limit: int = 400, max_distance: float = 0.82) -> int:
    """Zero-shot assign archive items to a custom category via embeddings."""
    row = conn.execute(
        "SELECT description FROM custom_categories WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise ValueError(f"unknown category: {slug}")
    from km.embedding.embedder import get_embedder
    from km.embedding.store import ensure_vec_tables, vector_search

    embedder = get_embedder(cfg)
    if not ensure_vec_tables(conn, embedder.dims):
        return 0
    vec = embedder.encode_query(row["description"])
    hits = vector_search(conn, vec, limit=limit)
    now = datetime.now(timezone.utc).isoformat()
    assigned = 0
    for item_id, distance in hits:
        if distance > max_distance:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO classifications
               (item_id, category, confidence, model, prompt_version, classified_at)
               VALUES (?,?,?,?,?,?)""",
            (item_id, slug, round(1 - distance, 3), "local-zero-shot",
             PROMPT_VERSION, now))
        assigned += 1
    conn.commit()
    return assigned


def list_custom(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT slug, name, description, source, created_at FROM custom_categories ORDER BY id")]
