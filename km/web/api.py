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
) -> Filters:
    return Filters(
        kind=kind, source=source, category=category, domain=domain,
        date_from=date_from, date_to=date_to,
        starred=starred, is_essay=is_essay, in_reading_list=in_reading_list,
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
        sort: str = "created_at",
        order: str = "desc",
        cursor: int = Query(0, ge=0),
        page_size: int = Query(50, le=200),
    ):
        conn = get_conn()
        filters = _filters_from_params(
            kind, source, category, domain, date_from, date_to,
            starred, is_essay, in_reading_list,
        )
        if q:
            if mode in ("semantic", "hybrid"):
                embedder = _embedder_or_none() if mode != "keyword" else None
                scored = hybrid_search(conn, q, embedder, filters, k=cursor + page_size,
                                       candidate_pool=max(200, cursor + page_size))
            else:
                scored = keyword_search(conn, q, filters, limit=cursor + page_size)
            page = scored[cursor:cursor + page_size]
            items = []
            for item_id, _ in page:
                row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
                if row:
                    items.append(_item_dict(conn, row))
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
        scored = hybrid_search(conn, req.query, embedder, filters, k=pool, candidate_pool=100)
        candidates = fetch_results(conn, scored)
        if req.ai and candidates and cfg.anthropic_api_key:
            from km.classify.client import get_client
            from km.search.rerank import rerank

            picks = rerank(get_client(), cfg.classification.model, req.query, candidates)
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
