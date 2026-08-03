"""Fetch readable article text for URL items into the content table.

This is what makes passage-level recall possible: a browser visit stores
only its title, so the paragraph you half-remember was never in the DB
until this runs. Fetches are cached on disk (data/cache/content/), rate
limited per worker, and only ever read public URLs you already visited.

After new text lands, the item's embedding_cache rows are cleared so the
next `km embed` re-chunks it with the full body.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable, Optional

# kinds whose text IS the content already (tweets, chats, notes, queries)
_SELF_CONTAINED = (
    "like", "retweet", "own_tweet", "bookmark_tweet",
    "chat_conversation", "search_query", "note",
)
# domains where trafilatura gets nothing useful back
_SKIP_DOMAINS = (
    "youtube.com", "youtu.be", "twitter.com", "x.com", "reddit.com",
    "google.com", "mail.google.com", "docs.google.com", "instagram.com",
    "facebook.com", "amazon.com", "localhost",
)

MIN_WORDS = 80  # below this it's a nav page or a stub, not an article


def dead_saves(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Cold-chain report: things you deliberately saved whose links are now
    dead or unreadable (fetch failed or returned nothing). These are the
    items to rescue elsewhere before they are gone for good."""
    rows = conn.execute(
        """SELECT i.id, i.kind, i.title, i.url, i.domain, i.created_at
           FROM items i JOIN content c ON c.item_id = i.id
           LEFT JOIN user_edits u ON u.item_id = i.id
           WHERE c.ok = 0
             AND (i.is_essay = 1 OR i.in_reading_list = 1 OR u.starred = 1
                  OR i.kind IN ('bookmark', 'favorite', 'upvote', 'saved_post'))
           ORDER BY coalesce(u.starred, 0) DESC, i.interest_score DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    return [
        {"id": r["id"], "kind": r["kind"], "title": r["title"], "url": r["url"],
         "domain": r["domain"], "first_seen": (r["created_at"] or "")[:10] or None}
        for r in rows
    ]


def candidates(
    conn: sqlite3.Connection, limit: Optional[int] = None, everything: bool = False
) -> list[sqlite3.Row]:
    """URL items worth fetching, unfetched first. Default: essays, reading
    list, and saves (the things you deliberately kept); --all widens to every
    URL item in the archive."""
    skip = " AND ".join(f"i.domain NOT LIKE '%{d}'" for d in _SKIP_DOMAINS)
    worth = "1=1" if everything else (
        "(i.is_essay=1 OR i.in_reading_list=1 OR i.kind IN "
        "('bookmark','favorite','upvote','saved_post'))"
    )
    sql = f"""SELECT i.* FROM items i
              LEFT JOIN content c ON c.item_id = i.id
              WHERE c.item_id IS NULL
                AND i.url IS NOT NULL AND i.url != ''
                AND i.kind NOT IN ({",".join("?" for _ in _SELF_CONTAINED)})
                AND {skip} AND {worth}
              ORDER BY i.interest_score DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, _SELF_CONTAINED).fetchall()


def _fetch_one(client, cache_dir, url: str) -> dict:
    """Returns {"text": str|None, "error": bool}; disk-cached by URL hash."""
    import trafilatura

    key = hashlib.sha256(url.encode()).hexdigest()
    cache_file = cache_dir / f"{key}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    try:
        resp = client.get(url)
        text = trafilatura.extract(resp.text) or None
        result = {"text": text, "error": False}
    except Exception:
        result = {"text": None, "error": True}
    cache_file.write_text(json.dumps(result, ensure_ascii=False))
    return result


def fetch_content(
    conn: sqlite3.Connection,
    cfg,
    limit: Optional[int] = None,
    everything: bool = False,
    workers: int = 8,
    progress: Optional[Callable[[int, int, dict], None]] = None,
) -> dict:
    """Fetch readable text for candidate items. Returns counters."""
    try:
        import httpx
        import trafilatura  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "content fetching needs the fetch extras: uv sync --extra fetch"
        ) from exc

    cache_dir = cfg.data_dir / "cache" / "content"
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = candidates(conn, limit=limit, everything=everything)
    counters = {"fetched": 0, "empty": 0, "errors": 0, "total": len(rows)}
    if not rows:
        return counters

    now = datetime.now(timezone.utc).isoformat()
    client = httpx.Client(
        timeout=15, follow_redirects=True,
        headers={"User-Agent": "km/0.1 (personal archive tool)"},
        limits=httpx.Limits(max_connections=workers),
    )
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_one, client, cache_dir, row["url"]): row["id"]
            for row in rows
        }
        for fut in as_completed(futures):
            item_id = futures[fut]
            result = fut.result()
            text = (result.get("text") or "").strip()
            words = len(text.split())
            if result.get("error"):
                counters["errors"] += 1
                conn.execute(
                    "INSERT OR REPLACE INTO content(item_id, text, word_count, fetched_at, ok) "
                    "VALUES (?, NULL, 0, ?, 0)", (item_id, now))
            elif words < MIN_WORDS:
                counters["empty"] += 1
                conn.execute(
                    "INSERT OR REPLACE INTO content(item_id, text, word_count, fetched_at, ok) "
                    "VALUES (?, NULL, 0, ?, 0)", (item_id, now))
            else:
                counters["fetched"] += 1
                conn.execute(
                    "INSERT OR REPLACE INTO content(item_id, text, word_count, fetched_at, ok) "
                    "VALUES (?, ?, ?, ?, 1)", (item_id, text, words, now))
                # force re-embed with the full body on the next km embed
                conn.execute("DELETE FROM embedding_cache WHERE item_id=?", (item_id,))
            done += 1
            if done % 25 == 0:
                conn.commit()
            if progress:
                progress(done, len(rows), counters)
    conn.commit()
    client.close()
    return counters
