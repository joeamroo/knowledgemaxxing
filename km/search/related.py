"""Related items: three signals, not one cosine.

"Similar to this" built only on embedding distance surfaces near-dupes
and title-twins. Real relatedness has at least three independent sources:

1. same meaning   - vector search from several of the anchor's chunks
                    (body chunks catch what the title misses)
2. shared language - the anchor's most distinctive terms as a BM25 query
                    (needs no embeddings at all)
3. read together  - items whose occurrences fall within a window of the
                    anchor's: co-browsing in one session is a relatedness
                    signal no embedding can see

The legs run independently, fuse with reciprocal rank fusion, near-dupes
collapse, and every result says WHY it is related.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

_STOP = frozenset(
    "a an and are as at be but by for from had has have how i in is it its of on or "
    "so that the their there this to was we were what when where which who will with "
    "you your not no if then than very really just more most about into over".split()
)

REASON_LABELS = {
    "semantic": "same meaning",
    "lexical": "shared language",
    "temporal": "read together",
}


def _distinctive_terms(title: str, text: str, n: int = 8) -> list[str]:
    from collections import Counter

    words = re.findall(r"[a-zA-Z0-9']+", f"{title or ''} {text or ''}".lower())
    counts = Counter(w for w in words if len(w) > 3 and w not in _STOP)
    return [w for w, _ in counts.most_common(n)]


def _semantic_leg(
    conn: sqlite3.Connection, anchor_id: int, pool: int, max_chunks: int = 2
) -> list[int]:
    """Vector neighbors aggregated over several anchor chunks, not just the
    first: best distance per candidate item wins. Each chunk costs a full
    vector scan, so callers pick how many they can afford."""
    from km.db import try_load_sqlite_vec

    if not try_load_sqlite_vec(conn):
        return []
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='embedding_chunks'"
    ).fetchone():
        return []  # nothing embedded yet; the other legs still fire
    chunks = conn.execute(
        """SELECT id FROM embedding_chunks WHERE item_id=?
           ORDER BY chunk_idx LIMIT ?""",
        (anchor_id, max_chunks),
    ).fetchall()
    best: dict[int, float] = {}
    for chunk in chunks:
        vec = conn.execute(
            "SELECT embedding FROM vec_items WHERE rowid=?", (chunk["id"],)
        ).fetchone()
        if not vec:
            continue
        for r in conn.execute(
            """SELECT c.item_id, min(v.distance) d
               FROM (SELECT rowid, distance FROM vec_items
                     WHERE embedding MATCH ? AND k = ?) v
               JOIN embedding_chunks c ON c.id = v.rowid
               WHERE c.item_id != ?
               GROUP BY c.item_id""",
            (vec["embedding"], pool, anchor_id),
        ):
            if r["item_id"] not in best or r["d"] < best[r["item_id"]]:
                best[r["item_id"]] = r["d"]
    return [item_id for item_id, _ in sorted(best.items(), key=lambda kv: kv[1])]


def _lexical_leg(conn: sqlite3.Connection, anchor, pool: int) -> list[int]:
    from km.search.keyword import keyword_search

    terms = _distinctive_terms(anchor["title"], anchor["text"])
    if not terms:
        return []
    hits = keyword_search(conn, " ".join(terms), limit=pool)
    return [item_id for item_id, _ in hits if item_id != anchor["id"]]


def _temporal_leg(
    conn: sqlite3.Connection, anchor_id: int, window_minutes: int = 45, pool: int = 40
) -> list[int]:
    """Items you touched in the same sitting as the anchor, closest first.

    Range form (BETWEEN over julianday) so the expression index applies;
    anchor occurrences capped so a much-visited item stays cheap."""
    window = window_minutes / (24 * 60.0)
    rows = conn.execute(
        """SELECT o2.item_id,
                  min(abs(julianday(o2.occurred_at) - o1.jd)) gap
           FROM (SELECT julianday(occurred_at) jd FROM occurrences
                 WHERE item_id = ? AND occurred_at IS NOT NULL
                 ORDER BY occurred_at DESC LIMIT 20) o1
           JOIN occurrences o2
             ON julianday(o2.occurred_at) BETWEEN o1.jd - ? AND o1.jd + ?
           WHERE o2.item_id != ?
           GROUP BY o2.item_id ORDER BY gap LIMIT ?""",
        (anchor_id, window, window, anchor_id, pool),
    ).fetchall()
    return [r["item_id"] for r in rows]


def related_items(
    conn: sqlite3.Connection,
    id: int,
    k: int = 8,
    pool: int = 40,
    max_anchor_chunks: int = 2,
) -> list[dict]:
    """Multi-signal related items with reasons. Works without embeddings
    (lexical + temporal legs still fire). max_anchor_chunks=1 is the
    interactive setting (one vector scan); 2 is the agent default."""
    from km.search.hybrid import rrf_merge

    anchor = conn.execute(
        "SELECT id, title, text FROM items WHERE id=?", (id,)
    ).fetchone()
    if not anchor:
        return []

    legs = {
        "semantic": _semantic_leg(conn, id, pool, max_chunks=max_anchor_chunks),
        "lexical": _lexical_leg(conn, anchor, pool),
        "temporal": _temporal_leg(conn, id, pool=pool),
    }
    fused = rrf_merge([lst for lst in legs.values() if lst])

    out: list[dict] = []
    seen_keys: set[tuple] = set()
    for item_id, score in fused:
        if item_id == id or len(out) >= k:
            continue
        row = conn.execute(
            """SELECT id, kind, title, text, author, url, domain, created_at
               FROM items WHERE id=?""",
            (item_id,),
        ).fetchone()
        if not row:
            continue
        key = ((row["title"] or "").strip().lower(), row["domain"])
        if key[0] and key in seen_keys:
            continue
        seen_keys.add(key)
        reasons = [REASON_LABELS[name] for name, lst in legs.items() if item_id in set(lst)]
        out.append({
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"] or (row["text"] or "")[:100] or None,
            "author": row["author"],
            "url": row["url"],
            "domain": row["domain"],
            "first_seen": (row["created_at"] or "")[:10] or None,
            "reasons": reasons,
            "score": round(score, 4),
        })
    return out
