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


def similar_items(conn: sqlite3.Connection, id: int, k: int = 8) -> list[dict]:
    """Embedding nearest-neighbors of one item: 'more like this'."""
    from km.embedding.store import similar_items as _similar

    hits = _similar(conn, id, limit=k)
    if not hits:
        return [{"note": "no embedding for this item yet (or sqlite-vec missing)"}]
    out = []
    for item_id, distance in hits:
        row = conn.execute(
            "SELECT id, kind, title, text, url, domain, created_at FROM items WHERE id=?",
            (item_id,),
        ).fetchone()
        if row:
            out.append({
                "id": row["id"], "kind": row["kind"],
                "title": row["title"] or (row["text"] or "")[:100] or None,
                "url": row["url"], "domain": row["domain"],
                "first_seen": (row["created_at"] or "")[:10] or None,
                "distance": round(distance, 3),
            })
    return out


def _edit_item(conn: sqlite3.Connection, item_id: int, **changes) -> dict:
    from datetime import datetime, timezone

    if not conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
        return {"error": f"no item with id {item_id}"}
    existing = conn.execute(
        "SELECT * FROM user_edits WHERE item_id=?", (item_id,)
    ).fetchone()
    values = {
        "starred": existing["starred"] if existing else 0,
        "archived": existing["archived"] if existing else 0,
        "note": existing["note"] if existing else None,
        "category_override": existing["category_override"] if existing else None,
    }
    values.update(changes)
    conn.execute(
        """INSERT OR REPLACE INTO user_edits
           (item_id, starred, archived, category_override, note, updated_at)
           VALUES (?,?,?,?,?,?)""",
        (item_id, values["starred"], values["archived"],
         values["category_override"], values["note"],
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    title = conn.execute("SELECT title, text FROM items WHERE id=?", (item_id,)).fetchone()
    return {"ok": True, "id": item_id,
            "item": title["title"] or (title["text"] or "")[:80], **changes}


def star_item(conn: sqlite3.Connection, id: int, starred: bool = True) -> dict:
    return _edit_item(conn, id, starred=int(starred))


def add_note(conn: sqlite3.Connection, id: int, note: str) -> dict:
    return _edit_item(conn, id, note=note)


def set_category(conn: sqlite3.Connection, id: int, category: str) -> dict:
    return _edit_item(conn, id, category_override=category)


def create_task(conn: sqlite3.Connection, text: str, due: Optional[str] = None) -> dict:
    from km.taskdriver import add_task

    task_id = add_task(conn, text, due)
    return {"ok": True, "task_id": task_id, "text": text, "due": due}


def complete_task(conn: sqlite3.Connection, task_id: int) -> dict:
    from km.taskdriver import set_status

    set_status(conn, task_id, "done")
    return {"ok": True, "task_id": task_id, "status": "done"}


def get_tasks(conn: sqlite3.Connection, status: str = "open") -> list[dict]:
    from km.taskdriver import list_tasks

    return list_tasks(conn, status)


def queue_reading(conn: sqlite3.Connection, id: int) -> dict:
    """Put an item in today's reading feed ('read later')."""
    from datetime import datetime, timezone

    if not conn.execute("SELECT 1 FROM items WHERE id=?", (id,)).fetchone():
        return {"error": f"no item with id {id}"}
    date = datetime.now(timezone.utc).date().isoformat()
    pos = conn.execute(
        "SELECT coalesce(max(position), -1) + 1 FROM daily_feed WHERE date=?",
        (date,)).fetchone()[0]
    conn.execute(
        "INSERT OR IGNORE INTO daily_feed(date, item_id, reason, position) VALUES (?,?,?,?)",
        (date, id, "queued by the archivist", pos))
    conn.commit()
    return {"ok": True, "id": id, "queued_for": date}


def get_reading_feed(conn: sqlite3.Connection) -> list[dict]:
    from km.feed import get_daily_feed

    return [
        {"id": f["item_id"], "title": f["title"], "reason": f["reason"],
         "read": bool(f["read"]), "url": f.get("url")}
        for f in get_daily_feed(conn)
    ]


def save_collection(
    conn: sqlite3.Connection,
    name: str,
    query: Optional[str] = None,
    kind: Optional[str] = None,
    category: Optional[str] = None,
    domain: Optional[str] = None,
    is_essay: Optional[bool] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> dict:
    """Save a search/filter set as a smart collection in the UI sidebar."""
    import json
    from datetime import datetime, timezone

    filters = {k: v for k, v in {
        "kind": kind, "category": category, "domain": domain,
        "is_essay": is_essay, "date_from": date_from, "date_to": date_to,
    }.items() if v is not None}
    spec = {"query": query or "", "mode": "hybrid", "filters": filters}
    cur = conn.execute(
        "INSERT INTO smart_collections(name, spec, created_at) VALUES (?,?,?)",
        (name[:80], json.dumps(spec), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return {"ok": True, "collection_id": cur.lastrowid, "name": name[:80], "spec": spec}


def export_list(cfg, conn: sqlite3.Connection, title: str, item_ids: list[int]) -> dict:
    """Write a markdown link list to exports/lists/, from explicit item ids."""
    import re

    rows = []
    for item_id in item_ids[:500]:
        row = conn.execute(
            "SELECT title, text, url, domain, created_at FROM items WHERE id=?",
            (int(item_id),),
        ).fetchone()
        if row:
            rows.append(row)
    if not rows:
        return {"error": "none of those item ids exist"}
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "list"
    out_dir = cfg.exports_dir / "lists"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{slug}.md"
    lines = [f"# {title}", ""]
    for row in rows:
        name = row["title"] or (row["text"] or "")[:100] or row["url"]
        date = (row["created_at"] or "")[:10]
        if row["url"]:
            lines.append(f"- [{name}]({row['url']}) ({row['domain'] or ''} {date})".replace("( ", "("))
        else:
            lines.append(f"- {name} ({date})")
    path.write_text("\n".join(lines) + "\n")
    return {"ok": True, "path": str(path), "items": len(rows)}


def fetch_page(cfg, conn: sqlite3.Connection, id: int) -> dict:
    """Fetch one item's article text on demand (stores it for future search)."""
    row = conn.execute("SELECT url FROM items WHERE id=?", (id,)).fetchone()
    if not row:
        return {"error": f"no item with id {id}"}
    if not row["url"]:
        return {"error": "item has no url"}
    try:
        import httpx

        from km.fetch_content import MIN_WORDS, _fetch_one
    except ImportError:
        return {"error": "fetching needs the fetch extras: uv sync --extra fetch"}
    from datetime import datetime, timezone

    cache_dir = cfg.data_dir / "cache" / "content"
    cache_dir.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=15, follow_redirects=True,
                          headers={"User-Agent": "km/0.1 (personal archive tool)"})
    result = _fetch_one(client, cache_dir, row["url"])
    client.close()
    text = (result.get("text") or "").strip()
    words = len(text.split())
    now = datetime.now(timezone.utc).isoformat()
    ok = 1 if (not result.get("error") and words >= MIN_WORDS) else 0
    conn.execute(
        "INSERT OR REPLACE INTO content(item_id, text, word_count, fetched_at, ok) "
        "VALUES (?,?,?,?,?)",
        (id, text if ok else None, words if ok else 0, now, ok))
    if ok:
        conn.execute("DELETE FROM embedding_cache WHERE item_id=?", (id,))
    conn.commit()
    if not ok:
        return {"error": "nothing readable at that url (paywall, JS-only, or gone)"}
    return {"ok": True, "id": id, "word_count": words, "preview": text[:600]}


# ── unified dispatch: one map for the agent and the MCP server ──

READ_TOOLS = {
    "search_archive", "get_item", "list_items", "archive_stats",
    "similar_items", "get_tasks", "get_reading_feed",
}


def run_tool(name: str, conn: sqlite3.Connection, cfg, embedder, args: dict):
    """Execute one archive tool by name. Raises KeyError for unknown names."""
    if name == "search_archive":
        return search_archive(conn, embedder, **args)
    if name == "get_item":
        return get_item(conn, **args)
    if name == "list_items":
        return list_items(conn, **args)
    if name == "archive_stats":
        return archive_stats(conn)
    if name == "similar_items":
        return similar_items(conn, **args)
    if name == "star_item":
        return star_item(conn, **args)
    if name == "add_note":
        return add_note(conn, **args)
    if name == "set_category":
        return set_category(conn, **args)
    if name == "create_task":
        return create_task(conn, **args)
    if name == "complete_task":
        return complete_task(conn, **args)
    if name == "get_tasks":
        return get_tasks(conn, **args)
    if name == "queue_reading":
        return queue_reading(conn, **args)
    if name == "get_reading_feed":
        return get_reading_feed(conn)
    if name == "save_collection":
        return save_collection(conn, **args)
    if name == "export_list":
        return export_list(cfg, conn, **args)
    if name == "fetch_page":
        return fetch_page(cfg, conn, **args)
    raise KeyError(name)
