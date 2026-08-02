"""Archive tools: the read-only operations an AI (MCP client or the
built-in archivist chat) can run against the knowledge base.

One implementation, two front doors: km mcp exposes these over the Model
Context Protocol; the archivist persona in the chat UI calls them through
the Anthropic tool-use loop. Everything here only ever SELECTs.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

_SORTS = {
    "recent": "i.created_at DESC",
    "oldest": "i.created_at ASC",
    "interest": "i.interest_score DESC",
}


def search_archive(
    conn: sqlite3.Connection,
    embedder,
    query: str,
    k: int = 10,
    essays_only: bool = False,
) -> list[dict]:
    """Hybrid semantic + keyword search with matching passages and provenance."""
    from km.search.hybrid import fetch_results, hybrid_search
    from km.search.keyword import Filters, parse_query

    filters = Filters(is_essay=True if essays_only else None)
    query, filters = parse_query(query, filters)
    passages: dict = {}
    scored = hybrid_search(
        conn, query, embedder, filters, k=k, candidate_pool=100, passages=passages,
    )
    results = fetch_results(conn, scored, passages=passages)
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "passage": r["passage"],
            "snippet": None if r["passage"] else r["snippet"],
            "url": r["url"],
            "domain": r["domain"],
            "kind": r["kind"],
            "category": r["category"],
            "first_seen": (r["created_at"] or "")[:10] or None,
            "sources": r["sources"],
        }
        for r in results
    ]


def get_item(conn: sqlite3.Connection, id: int) -> dict:
    """One item in full: text, fetched article body, category, occurrences."""
    row = conn.execute("SELECT * FROM items WHERE id=?", (id,)).fetchone()
    if not row:
        return {"error": f"no item with id {id}"}
    body = conn.execute(
        "SELECT text FROM content WHERE item_id=? AND ok=1", (id,)
    ).fetchone()
    occurrences = [
        {"kind": o["kind"], "source": o["source_kind"], "occurred_at": o["occurred_at"]}
        for o in conn.execute(
            """SELECT o.kind, o.occurred_at, s.kind AS source_kind
               FROM occurrences o JOIN sources s ON s.id=o.source_id
               WHERE o.item_id=? ORDER BY o.occurred_at""",
            (id,),
        )
    ]
    cat = conn.execute(
        """SELECT coalesce(u.category_override, c.category) cat
           FROM items i LEFT JOIN classifications c ON c.item_id=i.id
           LEFT JOIN user_edits u ON u.item_id=i.id WHERE i.id=?""",
        (id,),
    ).fetchone()
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "text": row["text"],
        "article_body": body["text"] if body else None,
        "url": row["url"],
        "domain": row["domain"],
        "author": row["author"],
        "created_at": row["created_at"],
        "category": cat["cat"] if cat else None,
        "occurrences": occurrences,
    }


def list_items(
    conn: sqlite3.Connection,
    kind: Optional[str] = None,
    category: Optional[str] = None,
    domain: Optional[str] = None,
    source: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    starred: Optional[bool] = None,
    is_essay: Optional[bool] = None,
    in_reading_list: Optional[bool] = None,
    sort: str = "recent",
    limit: int = 50,
) -> list[dict]:
    """Filtered listing (no query): the tool for building lists of links.
    Sort: recent | oldest | interest."""
    from km.search.keyword import Filters

    filters = Filters(
        kind=kind, category=category, domain=domain, source=source,
        date_from=date_from, date_to=date_to, starred=starred,
        is_essay=is_essay, in_reading_list=in_reading_list,
    )
    where_sql, params = filters.sql()
    order = _SORTS.get(sort, _SORTS["recent"])
    rows = conn.execute(
        f"""SELECT i.id, i.kind, i.title, i.text, i.url, i.domain,
                   i.created_at, i.interest_score
            FROM items i WHERE {where_sql}
            ORDER BY {order} NULLS LAST LIMIT ?""",
        (*params, min(int(limit), 200)),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "title": r["title"] or (r["text"] or "")[:100] or None,
            "url": r["url"],
            "domain": r["domain"],
            "first_seen": (r["created_at"] or "")[:10] or None,
            "interest": round(r["interest_score"] or 0, 1),
        }
        for r in rows
    ]


def archive_stats(conn: sqlite3.Connection) -> dict:
    """Scale and shape of the archive: totals, sources, kinds, top domains."""
    from km.store import stats as get_stats

    s = get_stats(conn)
    return {
        "total_items": s["total_items"],
        "by_source": s["by_source_kind"],
        "by_kind": s["by_kind"],
        "top_domains": dict(s["top_domains"][:15]),
        "tweet_categories": s.get("categories") or {},
    }
