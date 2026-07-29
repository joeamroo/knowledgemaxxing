"""Live Chrome History SQLite parser.

Chrome locks the DB while running, so we always copy it to a temp file
first. Timestamps are WebKit epoch (microseconds since 1601-01-01 UTC).
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Iterator

from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.timeutil import webkit_to_dt
from km.urls import canonicalize


def parse_path(path: Path, ctx: ParseContext) -> Iterator[NormalizedItem]:
    profile = ctx.entry.note or path.parent.name
    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=True) as tmp:
        shutil.copy2(path, tmp.name)
        conn = sqlite3.connect(f"file:{tmp.name}?mode=ro&immutable=1", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            try:
                rows = conn.execute(
                    """SELECT u.url, u.title, v.visit_time AS ts
                       FROM visits v JOIN urls u ON u.id = v.url"""
                ).fetchall()
            except sqlite3.DatabaseError:
                # Degraded fallback: one record per URL using last_visit_time
                rows = conn.execute(
                    "SELECT url, title, last_visit_time AS ts FROM urls"
                ).fetchall()
            for row in rows:
                url = row["url"]
                if not url or not url.startswith(("http://", "https://")):
                    continue
                yield NormalizedItem(
                    kind="visit",
                    dedupe_key=f"url:{canonicalize(url)}",
                    url=url,
                    title=row["title"] or None,
                    created_at=webkit_to_dt(row["ts"]) if row["ts"] else None,
                    occurrence_detail=profile,
                )
        finally:
            conn.close()
