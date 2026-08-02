"""MCP server: let Claude search your whole archive from any session.

A minimal stdio JSON-RPC implementation of the Model Context Protocol,
no SDK dependency. Register once:

    claude mcp add km -- uv run --directory /path/to/knowledgemaxxing km mcp

and every Claude Code session can semantically search your entire
history ("find the passage about X I read years ago") with full
provenance. Read-only: the tools only ever SELECT.

Everything stays local; Claude reads only what a search returns.
"""
from __future__ import annotations

import json
import sys
from typing import Any, Optional

PROTOCOL_VERSION = "2025-06-18"

TOOLS = [
    {
        "name": "search_archive",
        "description": (
            "Hybrid semantic + keyword search over the user's whole digital "
            "history: browser visits, bookmarks, tweets, saves, AI chats, and "
            "fetched article text. Returns items with the exact matching "
            "passage when one exists, plus provenance (which source saw it, "
            "when). Best for half-remembered passages: describe the memory in "
            "natural language. Supports operators in the query: site:, kind:, "
            "cat:, source:, before:/after:<YYYY[-MM[-DD]]>."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language description or keywords"},
                "k": {"type": "integer", "description": "Max results (default 10)"},
                "essays_only": {"type": "boolean", "description": "Only long-form articles/essays"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_item",
        "description": (
            "Fetch one archive item in full by id (from search_archive "
            "results): complete text, fetched article body when available, "
            "category, and every occurrence with source and timestamp."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "Item id"}},
            "required": ["id"],
        },
    },
]


class KmServer:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._conn = None
        self._embedder = None
        self._embedder_failed = False

    @property
    def conn(self):
        if self._conn is None:
            from km.db import get_db

            self._conn = get_db(self.cfg.db_path)
        return self._conn

    def embedder(self):
        if self._embedder is None and not self._embedder_failed:
            try:
                from km.embedding.embedder import get_embedder

                self._embedder = get_embedder(self.cfg)
            except RuntimeError:
                self._embedder_failed = True  # keyword-only, still useful
        return self._embedder

    # ── tools ──────────────────────────────────────────────

    def search_archive(self, query: str, k: int = 10, essays_only: bool = False) -> list[dict]:
        from km.search.hybrid import fetch_results, hybrid_search
        from km.search.keyword import Filters, parse_query

        filters = Filters(is_essay=True if essays_only else None)
        query, filters = parse_query(query, filters)
        passages: dict = {}
        scored = hybrid_search(
            self.conn, query, self.embedder(), filters,
            k=k, candidate_pool=100, passages=passages,
        )
        results = fetch_results(self.conn, scored, passages=passages)
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

    def get_item(self, id: int) -> dict:
        row = self.conn.execute("SELECT * FROM items WHERE id=?", (id,)).fetchone()
        if not row:
            return {"error": f"no item with id {id}"}
        body = self.conn.execute(
            "SELECT text FROM content WHERE item_id=? AND ok=1", (id,)
        ).fetchone()
        occurrences = [
            {
                "kind": o["kind"],
                "source": o["source_kind"],
                "occurred_at": o["occurred_at"],
            }
            for o in self.conn.execute(
                """SELECT o.kind, o.occurred_at, s.kind AS source_kind
                   FROM occurrences o JOIN sources s ON s.id=o.source_id
                   WHERE o.item_id=? ORDER BY o.occurred_at""",
                (id,),
            )
        ]
        cat = self.conn.execute(
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

    # ── protocol plumbing ──────────────────────────────────

    def handle(self, msg: dict) -> Optional[dict]:
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            return self._result(msg_id, {
                "protocolVersion": msg.get("params", {}).get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "km", "version": "0.1"},
            })
        if method in ("notifications/initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return self._result(msg_id, {})
        if method == "tools/list":
            return self._result(msg_id, {"tools": TOOLS})
        if method == "tools/call":
            params = msg.get("params", {})
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                if name == "search_archive":
                    payload = self.search_archive(**args)
                elif name == "get_item":
                    payload = self.get_item(**args)
                else:
                    return self._error(msg_id, -32602, f"unknown tool: {name}")
                return self._result(msg_id, {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}],
                })
            except Exception as exc:  # tool errors go back in-band per MCP
                return self._result(msg_id, {
                    "content": [{"type": "text", "text": f"error: {exc}"}],
                    "isError": True,
                })
        if msg_id is not None:
            return self._error(msg_id, -32601, f"method not found: {method}")
        return None

    @staticmethod
    def _result(msg_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def serve(cfg) -> None:
    """Blocking stdio loop: one JSON-RPC message per line. stdout carries
    ONLY protocol messages; anything human goes to stderr."""
    server = KmServer(cfg)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = server.handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
