"""Full-archive JSON/JSONL export.

The whole point of km is that your data stays yours, so it has to come
back OUT as cleanly as it went in. One record per item with resolved
category, user edits, and full occurrence provenance (which source saw
it, how, and when). JSONL by default so the file streams and greps well
at any size; a plain JSON array on request.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_ITEM_FIELDS = (
    "id", "kind", "url", "canonical_url", "domain", "title", "text",
    "author", "created_at", "dedupe_key", "is_essay", "is_thread",
    "in_reading_list", "interest_score",
)


def _record(conn: sqlite3.Connection, row: sqlite3.Row, include_raw: bool) -> dict:
    rec = {f: row[f] for f in _ITEM_FIELDS}
    rec["is_essay"] = bool(rec["is_essay"])
    rec["is_thread"] = bool(rec["is_thread"])
    rec["in_reading_list"] = bool(rec["in_reading_list"])

    cls = conn.execute(
        """SELECT category, subcategories, confidence, model, classified_at
           FROM classifications WHERE item_id=?
           ORDER BY classified_at DESC LIMIT 1""",
        (row["id"],),
    ).fetchone()
    edit = conn.execute(
        "SELECT starred, archived, category_override, note FROM user_edits WHERE item_id=?",
        (row["id"],),
    ).fetchone()

    category = None
    if edit and edit["category_override"]:
        category = edit["category_override"]
    elif cls:
        category = cls["category"]
    rec["category"] = category
    if cls:
        rec["classification"] = {
            "category": cls["category"],
            "subcategories": cls["subcategories"],
            "confidence": cls["confidence"],
            "model": cls["model"],
            "classified_at": cls["classified_at"],
        }
    if edit and (edit["starred"] or edit["archived"] or edit["note"] or edit["category_override"]):
        rec["user_edits"] = {
            "starred": bool(edit["starred"]),
            "archived": bool(edit["archived"]),
            "category_override": edit["category_override"],
            "note": edit["note"],
        }

    rec["occurrences"] = [
        {
            "source": o["source_kind"],
            "kind": o["occ_kind"],
            "occurred_at": o["occurred_at"],
            "detail": o["detail"],
        }
        for o in conn.execute(
            """SELECT o.kind AS occ_kind, o.occurred_at, o.detail, s.kind AS source_kind
               FROM occurrences o JOIN sources s ON s.id=o.source_id
               WHERE o.item_id=? ORDER BY o.occurred_at""",
            (row["id"],),
        ).fetchall()
    ]

    if include_raw and row["raw_json"]:
        try:
            rec["raw"] = json.loads(row["raw_json"])
        except (json.JSONDecodeError, TypeError):
            rec["raw"] = row["raw_json"]
    return rec


def export_json(
    conn: sqlite3.Connection,
    out_path: Path,
    fmt: str = "jsonl",
    include_raw: bool = False,
    limit: int | None = None,
) -> dict:
    """Dump every item to out_path. Returns {"items": n, "path": out_path}."""
    sql = "SELECT * FROM items ORDER BY id"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql).fetchall()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        if fmt == "json":
            fh.write("[\n")
            for i, row in enumerate(rows):
                rec = _record(conn, row, include_raw)
                sep = ",\n" if i < len(rows) - 1 else "\n"
                fh.write(json.dumps(rec, ensure_ascii=False) + sep)
            fh.write("]\n")
        else:
            for row in rows:
                rec = _record(conn, row, include_raw)
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"items": len(rows), "path": out_path}
