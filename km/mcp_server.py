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

from km.search.tool_schemas import TOOL_SCHEMAS
from km.search.tools import READ_TOOLS

# MCP stays read-only by contract: only the tools that SELECT are exposed
TOOLS = [
    {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
    for t in TOOL_SCHEMAS
    if t["name"] in READ_TOOLS
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

    # ── tools (shared implementations in km.search.tools) ──

    def call_tool(self, name: str, args: dict):
        from km.egress import record_egress
        from km.search.tools import run_tool

        if name not in READ_TOOLS:
            raise KeyError(name)  # write tools are chat-only, never MCP
        payload = run_tool(name, self.conn, self.cfg, self.embedder(), args)
        record_egress(self.conn, "mcp", name, payload=payload)
        return payload

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
                try:
                    payload = self.call_tool(name, args)
                except KeyError:
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
