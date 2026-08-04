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


def get_chat_messages(
    conn: sqlite3.Connection,
    id: int,
    role: Optional[str] = None,
    max_messages: int = 100,
) -> dict:
    """Structured messages of one chat_conversation item.

    Transcripts are stored as "role: text" blocks (ChatGPT uses user/,
    Claude uses human/); this splits them back into messages so
    "the questions I asked in that conversation" is one tool call with
    role="user" instead of eyeballing a truncated wall of text.
    """
    import re

    row = conn.execute(
        "SELECT kind, title, text, created_at, raw_json FROM items WHERE id=?", (id,)
    ).fetchone()
    if not row:
        return {"error": f"no item with id {id}"}
    if row["kind"] != "chat_conversation":
        return {"error": f"item {id} is a {row['kind']}, not a chat conversation"}

    messages: list[dict] = []
    for block in (row["text"] or "").split("\n\n"):
        m = re.match(r"^(user|human|assistant|tool|system|unknown):\s*(.*)$", block, re.S)
        if m:
            r = "user" if m.group(1) == "human" else m.group(1)
            messages.append({"role": r, "text": m.group(2).strip()})
        elif messages:
            # continuation paragraph of the previous message
            messages[-1]["text"] += "\n\n" + block
    total = len(messages)
    if role:
        want = "user" if role == "human" else role
        messages = [m for m in messages if m["role"] == want]
    matched = len(messages)
    messages = [{**m, "text": m["text"][:2000]} for m in messages[:max_messages]]

    provider = None
    try:
        import json as _json

        provider = (_json.loads(row["raw_json"]) or {}).get("provider")
    except (ValueError, TypeError):
        pass
    return {
        "id": id,
        "title": row["title"],
        "provider": provider,
        "date": (row["created_at"] or "")[:10] or None,
        "total_messages": total,
        "returned": len(messages),
        "matched_role": matched if role else None,
        "messages": messages,
    }


def get_item(
    conn: sqlite3.Connection,
    id: int,
    offset: int = 0,
    max_chars: int = 12_000,
) -> dict:
    """One item in full: text, fetched article body, category, occurrences.

    Long texts paginate: offset/max_chars window both text fields, and the
    result says how much is left so a follow-up call can continue."""
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

    def window(value):
        if not value:
            return value, 0
        piece = value[offset:offset + max_chars]
        return piece, max(0, len(value) - offset - len(piece))

    text, text_rest = window(row["text"])
    article, article_rest = window(body["text"] if body else None)
    out = {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "text": text,
        "article_body": article,
        "url": row["url"],
        "domain": row["domain"],
        "author": row["author"],
        "created_at": row["created_at"],
        "category": cat["cat"] if cat else None,
        "occurrences": occurrences,
    }
    if text_rest or article_rest or offset:
        out["pagination"] = {
            "offset": offset,
            "text_chars_remaining": text_rest,
            "article_chars_remaining": article_rest,
            "next_offset": offset + max_chars if (text_rest or article_rest) else None,
        }
    return out


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
        f"""SELECT i.id, i.kind, i.title, i.text, i.author, i.url, i.domain,
                   i.created_at, i.interest_score
            FROM items i WHERE {where_sql}
            ORDER BY {order} NULLS LAST LIMIT ?""",
        (*params, min(int(limit), 200)),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "kind": r["kind"],
            "title": r["title"],
            # tweets, notes, and search queries ARE their text: return enough
            # of it that a roster is readable without a fetch per item
            "text": (r["text"] or "")[:300] or None,
            "author": r["author"],
            "url": r["url"],
            "domain": r["domain"],
            "first_seen": (r["created_at"] or "")[:10] or None,
            "interest": round(r["interest_score"] or 0, 1),
        }
        for r in rows
    ]


def get_items(
    conn: sqlite3.Connection,
    ids: list[int],
    max_chars_each: int = 2000,
) -> dict:
    """Bulk fetch: full text of up to 50 items in one call, any kind.

    The batch equivalent of get_item for retrospectives: after rostering
    tweets/notes/searches with list_items or a search, pull all their
    full texts at once instead of one round-trip per item."""
    out, missing = [], []
    for item_id in list(ids)[:50]:
        row = conn.execute(
            """SELECT id, kind, title, text, author, url, domain, created_at
               FROM items WHERE id=?""",
            (int(item_id),),
        ).fetchone()
        if not row:
            missing.append(int(item_id))
            continue
        text = row["text"] or ""
        out.append({
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "text": text[:max_chars_each] or None,
            "truncated": len(text) > max_chars_each,
            "author": row["author"],
            "url": row["url"],
            "domain": row["domain"],
            "first_seen": (row["created_at"] or "")[:10] or None,
        })
    result = {"items": out, "returned": len(out)}
    if missing:
        result["missing_ids"] = missing
    if len(ids) > 50:
        result["note"] = f"capped at 50 of {len(ids)} requested ids"
    return result


def period_summary(
    conn: sqlite3.Connection,
    date_from: str,
    date_to: str,
) -> dict:
    """One-call orientation for a time window, across every artifact kind:
    what they tweeted/saved/searched/read, where, and about what. The
    starting move for any 'what was going on with me between X and Y'."""
    window = "created_at >= ? AND created_at <= ?"
    params = (date_from, date_to)

    total = conn.execute(
        f"SELECT count(*) c FROM items WHERE {window}", params).fetchone()["c"]
    by_kind = dict(conn.execute(
        f"""SELECT kind, count(*) c FROM items WHERE {window}
            GROUP BY kind ORDER BY c DESC""", params).fetchall())
    top_domains = dict(conn.execute(
        f"""SELECT domain, count(*) c FROM items
            WHERE {window} AND domain IS NOT NULL
            GROUP BY domain ORDER BY c DESC LIMIT 12""", params).fetchall())
    top_categories = dict(conn.execute(
        f"""SELECT coalesce(u.category_override, c.category) cat, count(*) n
            FROM items i
            JOIN classifications c ON c.item_id = i.id
            LEFT JOIN user_edits u ON u.item_id = i.id
            WHERE i.{window}
            GROUP BY cat ORDER BY n DESC LIMIT 10""", params).fetchall())
    searches = [
        {"id": r["id"], "query": (r["text"] or "")[:120],
         "date": (r["created_at"] or "")[:10]}
        for r in conn.execute(
            f"""SELECT id, text, created_at FROM items
                WHERE {window} AND kind='search_query'
                ORDER BY created_at LIMIT 40""", params)
    ]
    chats = [
        {"id": r["id"], "title": r["title"], "date": (r["created_at"] or "")[:10]}
        for r in conn.execute(
            f"""SELECT id, title, created_at FROM items
                WHERE {window} AND kind='chat_conversation'
                ORDER BY created_at LIMIT 40""", params)
    ]
    by_month = dict(conn.execute(
        f"""SELECT substr(created_at, 1, 7) m, count(*) c FROM items
            WHERE {window} GROUP BY m ORDER BY m""", params).fetchall())
    return {
        "window": {"from": date_from, "to": date_to},
        "total_items": total,
        "by_kind": by_kind,
        "by_month": by_month,
        "top_domains": top_domains,
        "top_categories": top_categories,
        "search_queries": searches,
        "chat_conversations": chats,
        "note": "use list_items/get_items for tweets and saves in this window; "
                "get_chat_messages for any chat listed above",
    }


def find_episodes(
    conn: sqlite3.Connection,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    gap_minutes: int = 45,
    min_items: int = 8,
    limit: int = 10,
) -> list[dict]:
    """Rabbit-hole detection: stitch browsing visits into episodes.

    Items are a poor unit for 'what was I doing that night'; a session of
    40 visits in 90 minutes is the real object. Groups visits by time gap,
    keeps the big ones, and names each by its dominant domains."""
    where = ["kind='visit'", "created_at IS NOT NULL"]
    params: list = []
    if date_from:
        where.append("created_at >= ?"); params.append(date_from)
    if date_to:
        where.append("created_at <= ?"); params.append(date_to)
    rows = conn.execute(
        f"""SELECT id, title, domain, created_at FROM items
            WHERE {" AND ".join(where)}
            ORDER BY created_at LIMIT 20000""",
        params,
    ).fetchall()

    from datetime import datetime as _dt

    episodes: list[list] = []
    current: list = []
    last_ts = None
    for r in rows:
        try:
            ts = _dt.fromisoformat(r["created_at"].replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        if last_ts is not None and (ts - last_ts).total_seconds() > gap_minutes * 60:
            if len(current) >= min_items:
                episodes.append(current)
            current = []
        current.append(r)
        last_ts = ts
    if len(current) >= min_items:
        episodes.append(current)

    episodes.sort(key=len, reverse=True)
    out = []
    for ep in episodes[:limit]:
        from collections import Counter

        domains = Counter(r["domain"] for r in ep if r["domain"])
        titles = [r["title"] for r in ep if r["title"]][:6]
        out.append({
            "start": ep[0]["created_at"][:16],
            "end": ep[-1]["created_at"][:16],
            "visits": len(ep),
            "top_domains": dict(domains.most_common(4)),
            "sample_titles": titles,
            "item_ids": [r["id"] for r in ep[:50]],
        })
    return out


def social_graph_changes(
    conn: sqlite3.Connection,
    relation: str = "following",
    limit: int = 50,
) -> dict:
    """What changed in your X social graph between archive snapshots.

    Each archive is a snapshot; an account seen in an older export but not
    the newest one is someone you unfollowed (or who unfollowed you, for
    followers). Only possible because every archive gets ingested and every
    snapshot leaves an occurrence.
    """
    kind = {
        "following": "x_following", "follower": "x_follower",
        "blocked": "x_blocked", "muted": "x_muted",
    }.get(relation)
    if not kind:
        return {"error": "relation must be following, follower, blocked, or muted"}

    newest = conn.execute(
        """SELECT max(o.occurred_at) d FROM occurrences o
           JOIN items i ON i.id = o.item_id WHERE i.kind = ?""",
        (kind,),
    ).fetchone()["d"]
    if not newest:
        return {"error": f"no {relation} snapshots ingested yet"}

    rows = conn.execute(
        """SELECT i.id, i.url, i.raw_json,
                  min(o.occurred_at) first_seen, max(o.occurred_at) last_seen,
                  count(DISTINCT o.occurred_at) snapshots
           FROM items i JOIN occurrences o ON o.item_id = i.id
           WHERE i.kind = ? GROUP BY i.id""",
        (kind,),
    ).fetchall()

    current, gone = [], []
    for r in rows:
        entry = {
            "id": r["id"], "url": r["url"],
            "first_seen": (r["first_seen"] or "")[:10],
            "last_seen": (r["last_seen"] or "")[:10],
        }
        (current if r["last_seen"] == newest else gone).append(entry)
    gone.sort(key=lambda e: e["last_seen"], reverse=True)
    return {
        "relation": relation,
        "newest_snapshot": newest[:10],
        "current": len(current),
        "gone_since": len(gone),
        "gone": gone[:limit],
        "note": "gone = present in an older archive but absent from the newest one",
    }


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
    """Related items via three fused signals (same meaning, shared language,
    read together), each result tagged with why it is related."""
    from km.search.related import related_items

    hits = related_items(conn, id, k=k)
    if not hits:
        return [{"note": "nothing related found (item may be empty or unknown)"}]
    return hits


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
    from km.egress import record_egress

    record_egress(conn, "export-list", str(path),
                  item_ids=[int(i) for i in item_ids[:500]], count=len(rows))
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
    "search_archive", "deep_search", "map_topics", "get_item", "get_items",
    "get_chat_messages", "list_items", "period_summary", "find_episodes",
    "social_graph_changes", "archive_stats", "similar_items", "get_tasks",
    "get_reading_feed",
}


def run_tool(name: str, conn: sqlite3.Connection, cfg, embedder, args: dict):
    """Execute one archive tool by name. Raises KeyError for unknown names."""
    if name == "search_archive":
        return search_archive(conn, embedder, **args)
    if name == "deep_search":
        from km.search.deep import deep_search

        return deep_search(conn, embedder, **args)
    if name == "map_topics":
        from km.search.topics import map_topics

        return map_topics(conn, embedder, **args)
    if name == "get_item":
        return get_item(conn, **args)
    if name == "get_items":
        return get_items(conn, **args)
    if name == "get_chat_messages":
        return get_chat_messages(conn, **args)
    if name == "period_summary":
        return period_summary(conn, **args)
    if name == "find_episodes":
        return find_episodes(conn, **args)
    if name == "social_graph_changes":
        return social_graph_changes(conn, **args)
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
