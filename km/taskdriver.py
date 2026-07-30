"""The task driver: what you said you'd do, held against you kindly.

Tasks live in the same database as everything else so the AI secretary
can see your commitments next to your actual behavior. Sources:
- manual adds (CLI or UI)
- harvest: checkbox and TODO lines mined from your Apple Notes
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

_TODO_LINE = re.compile(
    r"^\s*(?:[-*]\s*\[ \]|[-*]\s*TODO[:\s]|TODO[:\s]|\[\s?\])\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)


def add_task(conn: sqlite3.Connection, text: str, due: Optional[str] = None,
             source: str = "manual") -> int:
    cur = conn.execute(
        "INSERT INTO tasks(text, due, status, source, created_at) VALUES (?,?,?,?,?)",
        (text.strip(), due, "open", source, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    return cur.lastrowid


def list_tasks(conn: sqlite3.Connection, status: str = "open") -> list[dict]:
    today = datetime.now(timezone.utc).date().isoformat()
    rows = conn.execute(
        """SELECT * FROM tasks WHERE status=?
           ORDER BY CASE WHEN due IS NULL THEN 1 ELSE 0 END, due, created_at""",
        (status,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["overdue"] = bool(d["due"] and d["due"] < today and d["status"] == "open")
        d["due_today"] = bool(d["due"] and d["due"] == today)
        out.append(d)
    return out


def set_status(conn: sqlite3.Connection, task_id: int, status: str) -> None:
    completed = datetime.now(timezone.utc).isoformat() if status == "done" else None
    conn.execute("UPDATE tasks SET status=?, completed_at=? WHERE id=?",
                 (status, completed, task_id))
    conn.commit()


def harvest_from_notes(conn: sqlite3.Connection) -> list[dict]:
    """Mine TODO/checkbox lines out of notes; skip ones already tracked."""
    existing = {r["text"].lower() for r in conn.execute("SELECT text FROM tasks")}
    added = []
    for row in conn.execute("SELECT title, text FROM items WHERE kind='note' AND text != ''"):
        for match in _TODO_LINE.finditer(row["text"] or ""):
            text = match.group(1).strip().rstrip(".")
            if len(text) < 4 or len(text) > 200 or text.lower() in existing:
                continue
            source = f"note:{(row['title'] or '?')[:60]}"
            add_task(conn, text, source=source)
            existing.add(text.lower())
            added.append({"text": text, "source": source})
    return added


def tasks_for_ai(conn: sqlite3.Connection) -> dict:
    """Compact snapshot for the secretary persona and evidence packs."""
    open_tasks = list_tasks(conn, "open")
    recent_done = conn.execute(
        """SELECT text, completed_at FROM tasks WHERE status='done'
           ORDER BY completed_at DESC LIMIT 10""").fetchall()
    return {
        "overdue": [{"text": t["text"], "due": t["due"]} for t in open_tasks if t["overdue"]],
        "due_today": [t["text"] for t in open_tasks if t["due_today"]],
        "open": [{"text": t["text"], "due": t["due"]} for t in open_tasks[:30]],
        "recently_completed": [r["text"] for r in recent_done],
    }
