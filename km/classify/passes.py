"""Pluggable classification passes.

A Pass bundles a category set, a prompt template, and a version. Future
passes (emotional/thematic tagging, essay-seed extraction) plug in here
without touching the runner. Classifications cache per (item, pass
version): nothing is reclassified unless prompt_version changes.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from km.classify.client import call_claude, parse_json_response


@dataclass
class Pass:
    name: str
    version: str
    categories: list[str]
    system_prompt: str
    # SQL returning (id, text) rows for items this pass should classify
    select_sql: str
    format_item: Callable[[sqlite3.Row], str] = field(
        default=lambda row: (row["text"] or "")[:1000]
    )

    @property
    def prompt_version(self) -> str:
        return f"{self.name}:{self.version}"


@dataclass
class RunResult:
    classified: int = 0
    failed: int = 0
    batches: int = 0


def pending_items(conn: sqlite3.Connection, p: Pass, limit: Optional[int] = None) -> list[sqlite3.Row]:
    sql = f"""
        SELECT i.* FROM ({p.select_sql}) i
        WHERE i.id NOT IN (
            SELECT item_id FROM classifications WHERE prompt_version = ?
        )
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (p.prompt_version,)).fetchall()


def run_pass(
    conn: sqlite3.Connection,
    client,
    p: Pass,
    model: str,
    batch_size: int = 40,
    limit: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> RunResult:
    rows = pending_items(conn, p, limit)
    result = RunResult()
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        payload = [
            {"id": row["id"], "text": p.format_item(row)}
            for row in batch
        ]
        user_msg = (
            "Classify each item below. Respond with ONLY a JSON array, one object "
            'per item: {"id": <id>, "category": "<primary>", "secondary": ["<optional tags>"], '
            '"confidence": <0.0-1.0>}. No prose, no code fences.\n\n'
            + json.dumps(payload, ensure_ascii=False)
        )
        text = call_claude(client, model, p.system_prompt, user_msg)
        parsed = parse_json_response(text)
        result.batches += 1
        by_id = {row["id"] for row in batch}
        now = datetime.now(timezone.utc).isoformat()
        seen: set[int] = set()
        if isinstance(parsed, list):
            for entry in parsed:
                if not isinstance(entry, dict):
                    continue
                item_id = entry.get("id")
                category = entry.get("category")
                if item_id not in by_id or category not in p.categories:
                    continue
                secondary = [
                    s for s in (entry.get("secondary") or []) if s in p.categories
                ]
                conn.execute(
                    """INSERT OR REPLACE INTO classifications
                       (item_id, category, subcategories, confidence, model,
                        prompt_version, classified_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        item_id, category, json.dumps(secondary) if secondary else None,
                        float(entry.get("confidence") or 0.0), model,
                        p.prompt_version, now,
                    ),
                )
                seen.add(item_id)
                result.classified += 1
        # items the model skipped or mangled: mark as other so the run resumes
        # cleanly, but with confidence 0 so they are easy to re-run later
        for row in batch:
            if row["id"] not in seen:
                conn.execute(
                    """INSERT OR REPLACE INTO classifications
                       (item_id, category, subcategories, confidence, model,
                        prompt_version, classified_at)
                       VALUES (?,?,NULL,0,?,?,?)""",
                    (row["id"], "other", model, p.prompt_version, now),
                )
                result.failed += 1
        conn.commit()
        if progress:
            progress(start + len(batch), len(rows))
    return result
