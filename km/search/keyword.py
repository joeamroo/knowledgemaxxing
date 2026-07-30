"""FTS5 keyword search with the shared filter set."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Filters:
    kind: Optional[str] = None
    source: Optional[str] = None      # source kind (chrome_live_history, twitter_archive, ...)
    category: Optional[str] = None
    domain: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    starred: Optional[bool] = None
    is_essay: Optional[bool] = None
    in_reading_list: Optional[bool] = None
    archived: Optional[bool] = False  # default: hide archived

    def sql(self) -> tuple[str, list]:
        where, params = [], []
        if self.kind:
            where.append("i.kind = ?"); params.append(self.kind)
        if self.domain:
            where.append("i.domain = ?"); params.append(self.domain)
        if self.date_from:
            where.append("i.created_at >= ?"); params.append(self.date_from)
        if self.date_to:
            where.append("i.created_at <= ?"); params.append(self.date_to)
        if self.is_essay is not None:
            where.append("i.is_essay = ?"); params.append(1 if self.is_essay else 0)
        if self.in_reading_list is not None:
            where.append("i.in_reading_list = ?"); params.append(1 if self.in_reading_list else 0)
        if self.category:
            where.append(
                """i.id IN (
                     SELECT c.item_id FROM classifications c
                     LEFT JOIN user_edits u ON u.item_id = c.item_id
                     WHERE coalesce(u.category_override, c.category) = ?
                   )"""
            )
            params.append(self.category)
        if self.source:
            where.append(
                """i.id IN (
                     SELECT o.item_id FROM occurrences o
                     JOIN sources s ON s.id = o.source_id WHERE s.kind LIKE ?
                   )"""
            )
            params.append(f"%{self.source}%")
        if self.starred is not None:
            if self.starred:
                where.append("i.id IN (SELECT item_id FROM user_edits WHERE starred=1)")
            else:
                where.append("i.id NOT IN (SELECT item_id FROM user_edits WHERE starred=1)")
        if self.archived is not None and not self.archived:
            where.append("i.id NOT IN (SELECT item_id FROM user_edits WHERE archived=1)")
        return (" AND ".join(where) if where else "1=1"), params


_OPERATORS = ("site:", "domain:", "kind:", "cat:", "category:", "before:", "after:", "source:")


def parse_query(query: str, filters: Optional[Filters] = None) -> tuple[str, Filters]:
    """Pull search operators out of the query text into Filters.

    Supported: site:/domain:<host>, kind:<kind>, cat:/category:<cat>,
    source:<source>, before:<YYYY[-MM[-DD]]>, after:<YYYY[-MM[-DD]]>.
    Explicit filters already set win over operators in the text.
    """
    filters = filters or Filters()
    kept: list[str] = []
    for token in query.split():
        lower = token.lower()
        op = next((o for o in _OPERATORS if lower.startswith(o) and len(token) > len(o)), None)
        if op is None:
            kept.append(token)
            continue
        value = token[len(op):]
        if op in ("site:", "domain:") and not filters.domain:
            filters.domain = value.lower()
        elif op == "kind:" and not filters.kind:
            filters.kind = value.lower()
        elif op in ("cat:", "category:") and not filters.category:
            filters.category = value.lower()
        elif op == "source:" and not filters.source:
            filters.source = value.lower()
        elif op in ("before:", "after:"):
            parts = value.split("-")
            if not all(p.isdigit() for p in parts) or not (4 <= len(parts[0]) <= 4):
                kept.append(token)
                continue
            if op == "after:" and not filters.date_from:
                filters.date_from = value
            elif op == "before:" and not filters.date_to:
                # pad so before:2021 means before the year is over
                filters.date_to = value + {1: "-12-31", 2: "-31", 3: ""}[len(parts)]
        else:
            kept.append(token)
    return " ".join(kept), filters


def _fts_escape(query: str) -> str:
    """Quote each term so user input never breaks FTS5 syntax.

    Terms are OR'd: BM25 still ranks documents matching more terms higher,
    but a fuzzy query no longer requires every word to appear (AND
    semantics made half-memory queries miss their targets entirely).
    """
    terms = [t.replace('"', '""') for t in query.split() if t.strip()]
    return " OR ".join(f'"{t}"' for t in terms)


def keyword_search(
    conn: sqlite3.Connection,
    query: str,
    filters: Optional[Filters] = None,
    limit: int = 100,
) -> list[tuple[int, float]]:
    """Return [(item_id, bm25_rank)] best-first."""
    filters = filters or Filters()
    where_sql, params = filters.sql()
    rows = conn.execute(
        f"""SELECT i.id, bm25(items_fts) AS rank
            FROM items_fts f JOIN items i ON i.id = f.rowid
            WHERE items_fts MATCH ? AND {where_sql}
            ORDER BY rank LIMIT ?""",
        (_fts_escape(query), *params, limit),
    ).fetchall()
    return [(r["id"], r["rank"]) for r in rows]
