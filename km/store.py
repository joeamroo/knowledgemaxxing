"""Write and read paths over the knowledge database."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from km.models import NormalizedItem
from km.urls import canonicalize, domain_of


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_source(
    conn: sqlite3.Connection, kind: str, path_or_endpoint: str, file_hash: Optional[str] = None
) -> tuple[int, bool]:
    """Register a source. Returns (source_id, already_ingested)."""
    row = conn.execute(
        "SELECT id FROM sources WHERE path_or_endpoint=? AND file_hash IS ?",
        (path_or_endpoint, file_hash),
    ).fetchone()
    if row:
        return row["id"], True
    cur = conn.execute(
        "INSERT INTO sources(kind, path_or_endpoint, ingested_at, file_hash) VALUES (?,?,?,?)",
        (kind, path_or_endpoint, _now(), file_hash),
    )
    return cur.lastrowid, False


_KIND_PRIORITY = {
    # When the same dedupe_key arrives with different kinds, keep the
    # highest-intent kind on the item; occurrences keep the full story.
    "bookmark_tweet": 90,
    "like": 70,
    "retweet": 70,
    "own_tweet": 60,
    "saved_post": 70,
    "saved_comment": 70,
    "favorite": 70,
    "upvote": 60,
    "bookmark": 70,
    "chat_conversation": 50,
    "chat_message": 50,
    "search_query": 20,
    "visit": 10,
}


def upsert_item(conn: sqlite3.Connection, item: NormalizedItem, source_id: int) -> int:
    """Insert or merge a normalized item; always records the occurrence."""
    canonical = canonicalize(item.url) if item.url else None
    domain = domain_of(canonical) if canonical else None
    created = item.created_at.isoformat() if item.created_at else None

    row = conn.execute("SELECT * FROM items WHERE dedupe_key=?", (item.dedupe_key,)).fetchone()
    if row is None:
        cur = conn.execute(
            """INSERT INTO items(kind, url, canonical_url, domain, title, text, author,
                                 created_at, raw_json, dedupe_key)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                item.kind, item.url, canonical, domain, item.title, item.text,
                item.author, created, json.dumps(item.raw, default=str) if item.raw else None,
                item.dedupe_key,
            ),
        )
        item_id = cur.lastrowid
    else:
        item_id = row["id"]
        updates: dict[str, object] = {}
        # Fill gaps; upgrade kind by intent priority; prefer longer text
        if _KIND_PRIORITY.get(item.kind, 0) > _KIND_PRIORITY.get(row["kind"], 0):
            updates["kind"] = item.kind
        if item.title and not row["title"]:
            updates["title"] = item.title
        if item.text and len(item.text or "") > len(row["text"] or ""):
            updates["text"] = item.text
        if item.author and not row["author"]:
            updates["author"] = item.author
        if created and (row["created_at"] is None or created < row["created_at"]):
            updates["created_at"] = created  # keep earliest first-seen
        if updates:
            sets = ", ".join(f"{k}=?" for k in updates)
            conn.execute(f"UPDATE items SET {sets} WHERE id=?", (*updates.values(), item_id))

    occurred = item.created_at.isoformat() if item.created_at else None
    conn.execute(
        """INSERT OR IGNORE INTO occurrences(item_id, source_id, kind, occurred_at, detail)
           VALUES (?,?,?,?,?)""",
        (item_id, source_id, item.occurrence_kind, occurred, item.occurrence_detail),
    )
    return item_id


def get_scrape_cursor(conn: sqlite3.Connection, scraper: str) -> Optional[str]:
    row = conn.execute("SELECT cursor FROM scrape_state WHERE scraper=?", (scraper,)).fetchone()
    return row["cursor"] if row else None


def set_scrape_cursor(conn: sqlite3.Connection, scraper: str, cursor: Optional[str]) -> None:
    conn.execute(
        """INSERT INTO scrape_state(scraper, cursor, last_run_at) VALUES (?,?,?)
           ON CONFLICT(scraper) DO UPDATE SET cursor=excluded.cursor,
                                              last_run_at=excluded.last_run_at""",
        (scraper, cursor, _now()),
    )


def stats(conn: sqlite3.Connection) -> dict:
    """Summary counts used by km stats and /api/stats."""
    out: dict = {}
    out["total_items"] = conn.execute("SELECT count(*) c FROM items").fetchone()["c"]
    out["by_kind"] = {
        r["kind"]: r["c"]
        for r in conn.execute("SELECT kind, count(*) c FROM items GROUP BY kind ORDER BY c DESC")
    }
    out["by_source_kind"] = {
        r["kind"]: r["c"]
        for r in conn.execute(
            """SELECT s.kind kind, count(DISTINCT o.item_id) c
               FROM occurrences o JOIN sources s ON s.id=o.source_id
               GROUP BY s.kind ORDER BY c DESC"""
        )
    }
    out["top_domains"] = [
        (r["domain"], r["c"])
        for r in conn.execute(
            """SELECT domain, count(*) c FROM items
               WHERE domain IS NOT NULL AND domain != ''
               GROUP BY domain ORDER BY c DESC LIMIT 30"""
        )
    ]
    out["date_coverage"] = {
        r["kind"]: (r["lo"], r["hi"])
        for r in conn.execute(
            """SELECT s.kind kind, min(o.occurred_at) lo, max(o.occurred_at) hi
               FROM occurrences o JOIN sources s ON s.id=o.source_id
               WHERE o.occurred_at IS NOT NULL GROUP BY s.kind"""
        )
    }
    out["essays"] = conn.execute("SELECT count(*) c FROM items WHERE is_essay=1").fetchone()["c"]
    out["categories"] = {
        r["category"]: r["c"]
        for r in conn.execute(
            "SELECT category, count(*) c FROM classifications GROUP BY category ORDER BY c DESC"
        )
    }
    return out
