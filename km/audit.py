"""Coverage audit: is search actually seeing everything you think it is?

Silent partial indexing is worse than a crash, because search quietly
lies. This cross-checks, per kind and per year, how much of the archive
is embedded and how much has fetched article text, so "2014 is 40%
embedded" is a visible fact instead of a mystery about why old results
never show up.
"""
from __future__ import annotations

import sqlite3


def coverage(conn: sqlite3.Connection) -> dict:
    def pct(part: int, whole: int) -> float:
        return round(100.0 * part / whole, 1) if whole else 100.0

    has_vec = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='embedding_cache'").fetchone())

    by_kind = []
    # EXISTS, not JOIN: an item cached under two embedding models (after a
    # model upgrade) must still count once
    for r in conn.execute(
        """SELECT i.kind,
                  count(*) total,
                  sum(EXISTS(SELECT 1 FROM embedding_cache c WHERE c.item_id = i.id)) embedded,
                  sum(EXISTS(SELECT 1 FROM content ct WHERE ct.item_id = i.id AND ct.ok = 1)) fetched
           FROM items i
           GROUP BY i.kind ORDER BY total DESC"""
    ):
        by_kind.append({
            "kind": r["kind"], "total": r["total"],
            "embedded_pct": pct(r["embedded"] or 0, r["total"]),
            "fetched_text": r["fetched"] or 0,
        })

    by_year = []
    for r in conn.execute(
        """SELECT substr(i.created_at, 1, 4) y,
                  count(*) total,
                  sum(EXISTS(SELECT 1 FROM embedding_cache c WHERE c.item_id = i.id)) embedded
           FROM items i
           WHERE i.created_at IS NOT NULL
           GROUP BY y ORDER BY y"""
    ):
        if r["y"] and r["y"] >= "1990":
            by_year.append({
                "year": r["y"], "total": r["total"],
                "embedded_pct": pct(r["embedded"] or 0, r["total"]),
            })

    has_chunks = bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='embedding_chunks'").fetchone())
    totals = conn.execute(
        f"""SELECT count(*) items,
                  (SELECT count(*) FROM embedding_cache) cached,
                  {"(SELECT count(*) FROM embedding_chunks)" if has_chunks else "0"} chunks,
                  (SELECT count(*) FROM content WHERE ok=1) fetched
           FROM items"""
    ).fetchone()
    gaps = [k for k in by_kind if k["total"] > 50 and k["embedded_pct"] < 90.0]
    return {
        "totals": {
            "items": totals["items"], "embedding_cache_rows": totals["cached"],
            "chunks": totals["chunks"], "articles_fetched": totals["fetched"],
            "vec_available": has_vec,
        },
        "by_kind": by_kind,
        "by_year": by_year,
        "gaps": gaps,
        "healthy": not gaps,
    }
