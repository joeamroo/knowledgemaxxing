"""REST API over the knowledge database. Local only, no auth, no
external calls (the only outbound call is the Claude re-rank behind
POST /api/ask with ai=true, which the user invokes explicitly)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from km.config import Config
from km.search.hybrid import fetch_results, hybrid_search
from km.search.keyword import Filters, keyword_search

_SORTS = {
    "created_at": "i.created_at",
    "domain": "i.domain",
    "title": "i.title",
    "interest_score": "i.interest_score",
    "category": "category",
}


def _filters_from_params(
    kind=None, source=None, category=None, domain=None,
    date_from=None, date_to=None, starred=None, is_essay=None, in_reading_list=None,
    is_thread=None,
) -> Filters:
    return Filters(
        kind=kind, source=source, category=category, domain=domain,
        date_from=date_from, date_to=date_to,
        starred=starred, is_essay=is_essay, in_reading_list=in_reading_list,
        is_thread=is_thread,
    )


def _item_dict(conn: sqlite3.Connection, row: sqlite3.Row, full: bool = False) -> dict:
    d = {
        "id": row["id"], "kind": row["kind"], "url": row["url"],
        "canonical_url": row["canonical_url"], "domain": row["domain"],
        "title": row["title"],
        "text": row["text"] if full else (row["text"] or "")[:280],
        "author": row["author"], "created_at": row["created_at"],
        "is_essay": bool(row["is_essay"]), "is_thread": bool(row["is_thread"]),
        "in_reading_list": bool(row["in_reading_list"]),
        "interest_score": row["interest_score"],
    }
    cat = conn.execute(
        """SELECT coalesce(u.category_override, c.category) cat,
                  u.starred, u.archived, u.note
           FROM items i
           LEFT JOIN classifications c ON c.item_id=i.id
           LEFT JOIN user_edits u ON u.item_id=i.id WHERE i.id=?""",
        (row["id"],),
    ).fetchone()
    d["category"] = cat["cat"] if cat else None
    d["starred"] = bool(cat["starred"]) if cat and cat["starred"] is not None else False
    d["archived"] = bool(cat["archived"]) if cat and cat["archived"] is not None else False
    d["note"] = cat["note"] if cat else None
    occurrences = conn.execute(
        """SELECT o.kind, o.occurred_at, o.detail, s.kind AS source_kind
           FROM occurrences o JOIN sources s ON s.id=o.source_id
           WHERE o.item_id=? ORDER BY o.occurred_at""",
        (row["id"],),
    ).fetchall()
    d["sources"] = sorted({o["source_kind"] for o in occurrences})
    if full:
        d["occurrences"] = [dict(o) for o in occurrences]
        d["raw_json"] = row["raw_json"]
    return d


class PatchItem(BaseModel):
    starred: Optional[bool] = None
    archived: Optional[bool] = None
    note: Optional[str] = None
    category_override: Optional[str] = None


class AskRequest(BaseModel):
    query: str
    ai: bool = False
    k: int = 20
    source: Optional[str] = None
    category: Optional[str] = None
    domain: Optional[str] = None


class TaskIn(BaseModel):
    text: str
    due: Optional[str] = None


class TaskPatch(BaseModel):
    status: Optional[str] = None
    due: Optional[str] = None
    text: Optional[str] = None


class CollectionIn(BaseModel):
    name: str
    spec: dict


class CollectionAI(BaseModel):
    instruction: str


class TalkIn(BaseModel):
    persona: str = "therapist"
    message: str
    new_session: bool = False


class CategoryIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    instruction: Optional[str] = None  # plain words; Claude designs it


class ExportRequest(BaseModel):
    ids: list[int]
    filename: str = "selection.md"


def build_router(cfg: Config, get_conn) -> APIRouter:
    router = APIRouter(prefix="/api")

    def _embedder_or_none():
        try:
            from km.embedding.embedder import get_embedder

            return get_embedder(cfg)
        except (RuntimeError, Exception):
            return None

    @router.get("/items")
    def list_items(
        q: Optional[str] = None,
        mode: str = "keyword",
        kind: Optional[str] = None,
        source: Optional[str] = None,
        category: Optional[str] = None,
        domain: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        starred: Optional[bool] = None,
        is_essay: Optional[bool] = None,
        in_reading_list: Optional[bool] = None,
        is_thread: Optional[bool] = None,
        sort: str = "created_at",
        order: str = "desc",
        cursor: int = Query(0, ge=0),
        page_size: int = Query(50, le=200),
    ):
        conn = get_conn()
        filters = _filters_from_params(
            kind, source, category, domain, date_from, date_to,
            starred, is_essay, in_reading_list, is_thread,
        )
        if q:
            from km.search.keyword import parse_query

            q, filters = parse_query(q, filters)
        if q:
            passages: dict = {}
            if mode in ("semantic", "hybrid"):
                embedder = _embedder_or_none() if mode != "keyword" else None
                scored = hybrid_search(conn, q, embedder, filters, k=cursor + page_size,
                                       candidate_pool=max(200, cursor + page_size),
                                       passages=passages)
            else:
                scored = keyword_search(conn, q, filters, limit=cursor + page_size)
            page = scored[cursor:cursor + page_size]
            items = []
            for item_id, _ in page:
                row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
                if row:
                    d = _item_dict(conn, row)
                    if passages.get(item_id):
                        d["passage"] = passages[item_id][:600]
                    items.append(d)
            return {"items": items, "next_cursor": cursor + page_size if len(page) == page_size else None}

        where_sql, params = filters.sql()
        sort_col = _SORTS.get(sort, "i.created_at")
        direction = "ASC" if order == "asc" else "DESC"
        rows = conn.execute(
            f"""SELECT i.*, (SELECT coalesce(u.category_override, c.category)
                             FROM classifications c
                             LEFT JOIN user_edits u ON u.item_id=c.item_id
                             WHERE c.item_id=i.id LIMIT 1) AS category
                FROM items i WHERE {where_sql}
                ORDER BY {sort_col} {direction} NULLS LAST
                LIMIT ? OFFSET ?""",
            (*params, page_size, cursor),
        ).fetchall()
        items = [_item_dict(conn, row) for row in rows]
        return {
            "items": items,
            "next_cursor": cursor + page_size if len(rows) == page_size else None,
        }

    @router.get("/items/{item_id}")
    def get_item(item_id: int):
        conn = get_conn()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "item not found")
        return _item_dict(conn, row, full=True)

    @router.get("/items/{item_id}/similar")
    def similar(item_id: int, k: int = 6):
        conn = get_conn()
        try:
            from km.embedding.store import similar_items
            hits = similar_items(conn, item_id, limit=k)
        except Exception:
            hits = []
        out = []
        for other_id, distance in hits:
            row = conn.execute("SELECT * FROM items WHERE id=?", (other_id,)).fetchone()
            if row:
                d = _item_dict(conn, row)
                d["distance"] = round(distance, 4)
                out.append(d)
        return {"items": out}

    # ── custom categories: the AI (or you) extends the taxonomy ─────
    @router.get("/categories/custom")
    def custom_categories():
        from km.classify.custom import list_custom

        return {"categories": list_custom(get_conn())}

    @router.post("/categories/custom")
    def create_custom_category(body: CategoryIn):
        from km.classify.custom import assign_local, create_category, create_category_ai

        conn = get_conn()
        if body.instruction and not (body.name and body.description):
            if not cfg.anthropic_api_key:
                raise HTTPException(
                    402, "AI category design needs ANTHROPIC_API_KEY; or give name + description")
            try:
                cat = create_category_ai(conn, cfg, body.instruction)
            except Exception as exc:
                if "credit balance" in str(exc):
                    raise HTTPException(402, "API credits too low; give name + description instead")
                raise HTTPException(502, f"category design failed: {exc}")
        elif body.name and body.description:
            cat = create_category(conn, body.name, body.description)
        else:
            raise HTTPException(400, "give an instruction, or a name and description")
        try:
            assigned = assign_local(conn, cfg, cat["slug"])
        except Exception:
            assigned = 0
        return {**cat, "assigned": assigned}

    # ── entry actions ───────────────────────────────────────────────
    @router.post("/items/{item_id}/readable")
    def readable(item_id: int):
        """Fetch the item's page and extract clean article text (reader mode)."""
        conn = get_conn()
        row = conn.execute("SELECT url, text FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, "item not found")
        if row["text"] and len(row["text"]) > 1200:
            return {"text": row["text"], "source": "archive"}
        if not row["url"]:
            raise HTTPException(422, "no url to fetch")
        try:
            import httpx
            import trafilatura

            r = httpx.get(row["url"], timeout=12, follow_redirects=True,
                          headers={"User-Agent": "km-reader/1.0 (personal reading tool)"})
            text = trafilatura.extract(r.text) or ""
        except Exception as exc:
            raise HTTPException(502, f"could not fetch: {exc}")
        if not text:
            raise HTTPException(422, "no readable article text found")
        if len(text) > len(row["text"] or ""):
            conn.execute("UPDATE items SET text=? WHERE id=?", (text[:60000], item_id))
            conn.commit()
        return {"text": text[:60000], "source": "fetched"}

    @router.post("/feed/queue/{item_id}")
    def feed_queue(item_id: int):
        """Read later: put this item in today's feed."""
        conn = get_conn()
        date = datetime.now(timezone.utc).date().isoformat()
        pos = conn.execute(
            "SELECT coalesce(max(position), -1) + 1 FROM daily_feed WHERE date=?",
            (date,)).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO daily_feed(date, item_id, reason, position) VALUES (?,?,?,?)",
            (date, item_id, "queued by you", pos))
        conn.commit()
        return {"ok": True}

    @router.post("/items/{item_id}/discover")
    def discover(item_id: int, strategy: str = "local", k: int = 6):
        """Find similar essays on the live web (local embeddings or Claude)."""
        from km.discover_web import discover_ai, discover_local, ingest_discoveries

        conn = get_conn()
        try:
            picks = (discover_ai(conn, cfg, item_id, k) if strategy == "ai"
                     else discover_local(conn, cfg, item_id, k))
        except Exception as exc:
            if "credit balance" in str(exc):
                raise HTTPException(402, "Anthropic API credit balance too low")
            raise HTTPException(502, f"discovery failed: {exc}")
        if picks:
            ingest_discoveries(conn, item_id, picks, strategy)
        return {"picks": picks}

    @router.patch("/items/{item_id}")
    def patch_item(item_id: int, patch: PatchItem):
        conn = get_conn()
        if not conn.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
            raise HTTPException(404, "item not found")
        existing = conn.execute(
            "SELECT * FROM user_edits WHERE item_id=?", (item_id,)
        ).fetchone()
        values = {
            "starred": int(patch.starred) if patch.starred is not None
            else (existing["starred"] if existing else 0),
            "archived": int(patch.archived) if patch.archived is not None
            else (existing["archived"] if existing else 0),
            "note": patch.note if patch.note is not None
            else (existing["note"] if existing else None),
            "category_override": patch.category_override if patch.category_override is not None
            else (existing["category_override"] if existing else None),
        }
        conn.execute(
            """INSERT OR REPLACE INTO user_edits
               (item_id, starred, archived, category_override, note, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (item_id, values["starred"], values["archived"],
             values["category_override"], values["note"],
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return _item_dict(conn, row, full=True)

    @router.get("/facets")
    def facets(
        q: Optional[str] = None,
        kind: Optional[str] = None,
        source: Optional[str] = None,
        category: Optional[str] = None,
        domain: Optional[str] = None,
    ):
        conn = get_conn()
        filters = _filters_from_params(kind, source, category, domain)
        where_sql, params = filters.sql()
        base = f"FROM items i WHERE {where_sql}"
        out = {
            "kinds": {
                r["kind"]: r["c"]
                for r in conn.execute(f"SELECT i.kind, count(*) c {base} GROUP BY i.kind", params)
            },
            "domains": {
                r["domain"]: r["c"]
                for r in conn.execute(
                    f"""SELECT i.domain, count(*) c {base} AND i.domain IS NOT NULL
                        GROUP BY i.domain ORDER BY c DESC LIMIT 40""", params)
            },
            "categories": {
                r["cat"]: r["c"]
                for r in conn.execute(
                    f"""SELECT coalesce(u.category_override, c.category) cat, count(DISTINCT c.item_id) c
                        FROM classifications c LEFT JOIN user_edits u ON u.item_id=c.item_id
                        WHERE c.item_id IN (SELECT i.id {base})
                        GROUP BY cat ORDER BY c DESC""", params)
                if r["cat"]
            },
            "sources": {
                r["kind"]: r["c"]
                for r in conn.execute(
                    f"""SELECT s.kind, count(DISTINCT o.item_id) c
                        FROM occurrences o JOIN sources s ON s.id=o.source_id
                        WHERE o.item_id IN (SELECT i.id {base})
                        GROUP BY s.kind ORDER BY c DESC""", params)
            },
        }
        return out

    @router.get("/stats")
    def stats():
        from km.store import stats as get_stats

        conn = get_conn()
        s = get_stats(conn)
        s["items_per_month"] = [
            {"month": r["m"], "count": r["c"]}
            for r in conn.execute(
                """SELECT substr(created_at, 1, 7) m, count(*) c FROM items
                   WHERE created_at IS NOT NULL GROUP BY m ORDER BY m"""
            )
        ]
        s["items_per_day"] = [
            {"day": r["d"], "count": r["c"]}
            for r in conn.execute(
                """SELECT substr(created_at, 1, 10) d, count(*) c FROM items
                   WHERE created_at >= date('now', '-370 days')
                   GROUP BY d ORDER BY d"""
            )
        ]
        from km.extract.rhythms import activity_streaks, hourly_rhythms

        stamp = conn.execute("SELECT count(*) FROM items").fetchone()[0]
        cached = getattr(stats, "_rhythm_cache", None)
        if cached is None or cached[0] != stamp:
            cached = (stamp, hourly_rhythms(conn)["by_hour"], activity_streaks(conn))
            stats._rhythm_cache = cached
        s["by_hour"] = cached[1]
        s["streaks"] = cached[2]
        return s

    @router.get("/digest")
    def digest():
        from km.extract.reports import daily_digest

        conn = get_conn()
        return daily_digest(conn)

    @router.get("/random")
    def random_item(category: Optional[str] = None):
        conn = get_conn()
        where, params = "", []
        if category:
            where = """WHERE i.id IN (
                SELECT c.item_id FROM classifications c
                LEFT JOIN user_edits u ON u.item_id=c.item_id
                WHERE coalesce(u.category_override, c.category) = ?)"""
            params.append(category)
        row = conn.execute(
            f"SELECT * FROM items i {where} ORDER BY RANDOM() LIMIT 1", params
        ).fetchone()
        if not row:
            raise HTTPException(404, "no items")
        return _item_dict(conn, row, full=True)

    @router.post("/ask")
    def api_ask(req: AskRequest):
        conn = get_conn()
        filters = Filters(source=req.source, category=req.category, domain=req.domain)
        embedder = _embedder_or_none()
        pool = 50 if req.ai else req.k
        passages: dict = {}
        scored = hybrid_search(conn, req.query, embedder, filters, k=pool,
                               candidate_pool=100, passages=passages)
        candidates = fetch_results(conn, scored, passages=passages)
        if req.ai and candidates and cfg.anthropic_api_key:
            from km.classify.client import get_client
            from km.search.rerank import rerank

            picks = rerank(get_client(), cfg.classification.model, req.query, candidates,
                           conn=conn, cfg=cfg)
            return {"mode": "ai", "picks": picks, "candidates": candidates[:req.k]}
        return {"mode": "hybrid", "picks": [], "candidates": candidates[:req.k]}

    @router.post("/export")
    def export_selection(req: ExportRequest):
        conn = get_conn()
        lines = ["# Exported selection", ""]
        for item_id in req.ids:
            row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
            if not row:
                continue
            name = row["title"] or (row["text"] or "")[:120] or row["canonical_url"] or ""
            date = (row["created_at"] or "")[:10]
            lines.append(f"- [{name}]({row['url'] or ''}) ({row['kind']}, {date})")
            if row["text"] and row["title"]:
                lines.append(f"  {(row['text'] or '')[:500]}")
        safe = req.filename.replace("/", "_").replace("..", "_") or "selection.md"
        path = cfg.exports_dir / safe
        path.write_text("\n".join(lines) + "\n")
        return {"written": str(path), "count": len(req.ids)}

    # ── continuous sync ─────────────────────────────────────────────
    _sync_state = {"running": False, "last_run": None, "last_result": None}

    def _run_sync():
        from datetime import datetime, timezone

        from km.db import get_db as _get_db
        from km.discover.scanner import scan_chrome_live, scan_roots, scan_safari_live
        from km.extract.essays import mark_essays
        from km.extract.reading_lists import mark_reading_lists
        from km.extract.score import compute_scores
        from km.extract.threads import mark_threads
        from km.ingest import ingest_manifest
        from km.models import Manifest

        conn = _get_db(cfg.db_path, check_same_thread=False)
        stamp = datetime.now(timezone.utc).isoformat()
        new_items = 0
        try:
            live = scan_chrome_live() + [e for e in scan_safari_live() if e.status == "ready"]
            if live:
                new_items += ingest_manifest(conn, Manifest(generated_at=stamp, entries=live), cfg).total_items
            ready = [e for e in scan_roots(cfg) if e.status == "ready" and e.source_type != "generic"]
            if ready:
                new_items += ingest_manifest(conn, Manifest(generated_at=stamp, entries=ready), cfg).total_items
            try:
                from km.sources.apple_notes import sync_notes

                sync_notes(conn, cfg)
            except Exception:
                pass
            try:
                from km.feed import build_daily_feed, refresh_feeds

                refresh_feeds(conn)
                build_daily_feed(conn)
            except Exception:
                pass
            # heuristics first: they mark the essays worth fetching
            mark_essays(conn, cfg.load_domains())
            mark_threads(conn)
            mark_reading_lists(conn)
            compute_scores(conn)
            try:
                from km.fetch_content import fetch_content

                fetch_content(conn, cfg, limit=150)
            except Exception:
                pass
            try:
                from km.embedding.embedder import get_embedder
                from km.embedding.store import embed_pending

                embed_pending(conn, get_embedder(cfg))
            except Exception:
                pass
            _sync_state["last_result"] = f"{new_items:,} new items"
        except Exception as exc:
            _sync_state["last_result"] = f"failed: {exc}"
        finally:
            _sync_state["running"] = False
            _sync_state["last_run"] = datetime.now(timezone.utc).isoformat()

    @router.post("/sync")
    def start_sync():
        import threading

        if _sync_state["running"]:
            return {"status": "already running"}
        _sync_state["running"] = True
        threading.Thread(target=_run_sync, daemon=True).start()
        return {"status": "started"}

    @router.get("/sync/status")
    def sync_status():
        return _sync_state

    # ── tasks: the lock-in list ─────────────────────────────────────
    @router.get("/tasks")
    def tasks(status: str = "open"):
        from km.taskdriver import list_tasks

        return {"tasks": list_tasks(get_conn(), status)}

    @router.post("/tasks")
    def create_task(body: TaskIn):
        from km.taskdriver import add_task

        task_id = add_task(get_conn(), body.text, body.due)
        return {"id": task_id}

    @router.patch("/tasks/{task_id}")
    def patch_task(task_id: int, body: TaskPatch):
        conn = get_conn()
        if body.status:
            from km.taskdriver import set_status

            set_status(conn, task_id, body.status)
        if body.due is not None or body.text is not None:
            if body.due is not None:
                conn.execute("UPDATE tasks SET due=? WHERE id=?", (body.due or None, task_id))
            if body.text is not None:
                conn.execute("UPDATE tasks SET text=? WHERE id=?", (body.text, task_id))
            conn.commit()
        return {"ok": True}

    @router.post("/tasks/harvest")
    def harvest_tasks():
        from km.taskdriver import harvest_from_notes

        added = harvest_from_notes(get_conn())
        return {"added": added}

    # ── the daily reading feed ──────────────────────────────────────
    @router.get("/feed")
    def feed():
        from km.feed import build_daily_feed, get_daily_feed

        conn = get_conn()
        build_daily_feed(conn)
        return {"items": get_daily_feed(conn)}

    @router.post("/feed/refresh")
    def feed_refresh():
        from km.feed import build_daily_feed, refresh_feeds

        conn = get_conn()
        stats = refresh_feeds(conn)
        built = build_daily_feed(conn)
        return {**stats, "feed_size": built}

    @router.post("/feed/read/{item_id}")
    def feed_read(item_id: int):
        from km.feed import mark_read

        mark_read(get_conn(), item_id)
        return {"ok": True}

    # ── smart collections: features you create by asking ───────────
    @router.get("/collections")
    def collections():
        return {"collections": [
            {"id": r["id"], "name": r["name"], "spec": json.loads(r["spec"])}
            for r in get_conn().execute("SELECT * FROM smart_collections ORDER BY id")
        ]}

    @router.post("/collections")
    def create_collection(body: CollectionIn):
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO smart_collections(name, spec, created_at) VALUES (?,?,?)",
            (body.name, json.dumps(body.spec), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return {"id": cur.lastrowid}

    @router.delete("/collections/{cid}")
    def delete_collection(cid: int):
        conn = get_conn()
        conn.execute("DELETE FROM smart_collections WHERE id=?", (cid,))
        conn.commit()
        return {"ok": True}

    @router.post("/collections/auto")
    def auto_collections():
        """Cluster the archive locally and save the topics as collections.
        No API cost; replaces previous auto collections only."""
        from km.search.topics import generate_auto_collections

        embedder = _embedder_or_none()
        if embedder is None:
            raise HTTPException(422, "embeddings unavailable: uv sync --extra embed")
        result = generate_auto_collections(get_conn(), embedder)
        if "error" in result:
            raise HTTPException(422, result["error"])
        return result

    @router.post("/collections/ai")
    def ai_collection(body: CollectionAI):
        """Describe the collection you want in plain words; Claude writes the spec."""
        if not cfg.anthropic_api_key:
            raise HTTPException(402, "ANTHROPIC_API_KEY not set")
        from km.classify.client import get_client, parse_json_response

        prompt = (
            "Turn this request into a saved-search spec for a personal archive. "
            'Reply with JSON only: {"name": "<2-4 words>", "query": "<search words or empty>", '
            '"filters": {optional keys: kind (visit|like|bookmark_tweet|note|chat_conversation|'
            "search_query|feed_post), domain, category (aphorism|natural_law|contrarian|joke|"
            "quote|hot_take|interesting_fact|tool_or_resource|thread|anecdote|personal), "
            'is_essay (true), date_from (YYYY-MM-DD), date_to}}. Request: ' + body.instruction
        )
        try:
            response = get_client().messages.create(
                model=cfg.classification.model, max_tokens=300,
                messages=[{"role": "user", "content": prompt}])
            spec = parse_json_response("".join(b.text for b in response.content if b.type == "text"))
        except Exception as exc:
            raise HTTPException(502, f"AI spec failed: {exc}")
        name = spec.pop("name", body.instruction[:30])
        conn = get_conn()
        cur = conn.execute(
            "INSERT INTO smart_collections(name, spec, created_at) VALUES (?,?,?)",
            (name, json.dumps(spec), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        return {"id": cur.lastrowid, "name": name, "spec": spec}

    # ── the companion: talk to your archive in the browser ──────────
    @router.get("/talk/personas")
    def personas():
        from km.classify.talk import TALK_PERSONAS

        return {"personas": ["archivist", *TALK_PERSONAS.keys()]}

    @router.get("/talk/history")
    def talk_history(persona: str = "therapist"):
        from km.classify.talk import latest_session, load_history

        path = latest_session(cfg.data_dir, persona)
        return {"messages": load_history(path) if path else [],
                "session": path.name if path else None}

    @router.post("/talk/message")
    def talk_message(body: TalkIn):
        from km.classify.talk import (
            TALK_PERSONAS, build_system, latest_session, load_history,
            save_session, summarize_session, talk_turn,
        )

        if body.persona != "archivist" and body.persona not in TALK_PERSONAS:
            raise HTTPException(400, "unknown persona")
        if not cfg.anthropic_api_key:
            raise HTTPException(402, "ANTHROPIC_API_KEY not set (put it in .env)")
        from km.classify.client import get_client

        conn = get_conn()
        client = get_client()
        path = None if body.new_session else latest_session(cfg.data_dir, body.persona)
        if body.new_session:
            prior = latest_session(cfg.data_dir, body.persona)
            if prior:
                try:  # give the next session memory of this one
                    summarize_session(conn, client, cfg.classification.model,
                                      body.persona, prior, load_history(prior))
                except Exception:
                    pass
        messages = load_history(path) if path else []
        messages.append({"role": "user", "content": body.message})
        tool_trace: list[str] = []
        from km.classify.spend import BudgetExceeded, month_spend

        try:
            if body.persona == "archivist":
                from km.classify.agent import run_agent

                reply, tool_trace = run_agent(
                    client, cfg.classification.model, conn,
                    _embedder_or_none(), messages, cfg=cfg,
                )
            else:
                reply = talk_turn(client, cfg.classification.model,
                                  build_system(conn, body.persona), messages,
                                  conn=conn, cfg=cfg)
        except BudgetExceeded as exc:
            raise HTTPException(402, str(exc))
        except Exception as exc:
            detail = str(exc)
            if "credit balance" in detail:
                raise HTTPException(402, "Anthropic API credit balance too low")
            raise HTTPException(502, detail)
        messages.append({"role": "assistant", "content": reply})
        saved = save_session(cfg.data_dir, body.persona, path, messages)
        return {
            "reply": reply, "session": saved.name, "tools_used": tool_trace,
            "spend": {"month_usd": round(month_spend(conn), 2),
                      "budget_usd": cfg.ai_monthly_budget_usd},
        }

    @router.get("/spend")
    def spend():
        from km.classify.spend import month_spend

        return {"month_usd": round(month_spend(get_conn()), 2),
                "budget_usd": cfg.ai_monthly_budget_usd}

    @router.post("/upload")
    async def upload_archive(name: str, request: Request):
        """Accept one archive file (raw body), classify it, ingest it.

        Local-only like the rest of the API; files land in data/uploads/
        and go through the same classify + parse path as km ingest.
        """
        from km.discover.scanner import classify_path, scan_zip
        from km.ingest import ingest_manifest
        from km.models import Manifest

        safe = name.replace("/", "_").replace("..", "_").strip() or "upload.bin"
        uploads = cfg.data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        dest = uploads / safe
        data = await request.body()
        if not data:
            raise HTTPException(400, "empty upload")
        dest.write_bytes(data)

        entry = classify_path(dest)
        if entry is None:
            dest.unlink(missing_ok=True)
            raise HTTPException(
                422,
                "could not recognize this file; km understands Twitter/Takeout/"
                "ChatGPT/Claude/Reddit exports, browser history, and bookmark files",
            )
        entries = [entry]
        if entry.source_type in ("twitter_archive_zip", "takeout_zip"):
            entries += scan_zip(dest)
        manifest = Manifest(
            generated_at=datetime.now(timezone.utc).isoformat(), entries=entries)
        conn = get_conn()
        report = ingest_manifest(conn, manifest, cfg)
        return {
            "file": safe,
            "recognized_as": entry.source_type,
            "ingested": [{"path": p, "items": n} for p, n in report.ingested],
            "items": report.total_items,
            "already_known": len(report.already),
            "skipped": [{"path": p, "reason": r} for p, r in report.skipped],
        }

    return router
