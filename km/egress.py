"""Egress ledger: a bill of lading for everything that leaves the archive.

Local-first is a promise you should be able to audit, not take on vibes.
Every channel through which archive content crosses the boundary (the
archivist agent reading items into an API call, an MCP client, a file
export) records what left: when, through what, how many items, which ids.
`km egress` renders the ledger; nothing here ever blocks, it only records.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

_MAX_IDS = 200


def _extract_item_ids(payload) -> list[int]:
    """Best-effort item ids from a tool payload shape (list of dicts,
    {"items": [...]}, or a single item dict)."""
    ids: list[int] = []

    def walk(node):
        if len(ids) >= _MAX_IDS:
            return
        if isinstance(node, dict):
            item_id = node.get("id")
            if isinstance(item_id, int):
                ids.append(item_id)
            for key in ("items", "messages_parent", "chat_conversations", "search_queries"):
                if isinstance(node.get(key), list):
                    walk(node[key])
        elif isinstance(node, list):
            for entry in node:
                walk(entry)

    walk(payload)
    return ids


def record_egress(
    conn: sqlite3.Connection,
    channel: str,
    detail: str,
    payload=None,
    item_ids=None,
    count=None,
) -> None:
    """Append one ledger row. Pass payload for automatic id extraction, or
    explicit item_ids/count for exports. Never raises: a ledger hiccup must
    not break the operation it is recording."""
    try:
        ids = list(item_ids) if item_ids is not None else _extract_item_ids(payload)
        conn.execute(
            "INSERT INTO egress(at, channel, detail, item_count, item_ids) VALUES (?,?,?,?,?)",
            (
                datetime.now(timezone.utc).isoformat(), channel, detail,
                count if count is not None else len(ids),
                json.dumps(ids[:_MAX_IDS]),
            ),
        )
        conn.commit()
    except Exception:
        pass


def egress_report(conn: sqlite3.Connection, limit: int = 30) -> dict:
    """Totals by channel plus the most recent ledger rows."""
    by_channel = {
        r["channel"]: {"events": r["n"], "items": r["total"]}
        for r in conn.execute(
            """SELECT channel, count(*) n, coalesce(sum(item_count), 0) total
               FROM egress GROUP BY channel ORDER BY total DESC"""
        )
    }
    recent = [
        {"at": r["at"], "channel": r["channel"], "detail": r["detail"],
         "items": r["item_count"]}
        for r in conn.execute(
            "SELECT at, channel, detail, item_count FROM egress ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    ]
    return {"by_channel": by_channel, "recent": recent}
