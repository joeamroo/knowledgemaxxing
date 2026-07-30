"""Safari history: the Mac's History.db, which iCloud fills with every
visit from your iPhone and iPad too (history_visits.origin = 1).

Copy-then-open like Chrome live history, because Safari keeps the file
locked. Timestamps are Cocoa epoch (seconds since 2001-01-01 UTC).
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from km.models import NormalizedItem
from km.urls import canonicalize, domain_of

_COCOA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def _cocoa(seconds: float) -> datetime | None:
    try:
        stamp = _COCOA_EPOCH + timedelta(seconds=float(seconds))
    except (TypeError, ValueError, OverflowError):
        return None
    if stamp.year < 2000 or stamp.year > 2100:
        return None
    return stamp


def parse_path(path: Path, ctx=None) -> Iterator[NormalizedItem]:
    with tempfile.TemporaryDirectory() as tmp:
        db_copy = Path(tmp) / "History.db"
        shutil.copy2(path, db_copy)
        for suffix in ("-wal", "-shm"):
            side = Path(str(path) + suffix)
            if side.exists():
                try:
                    shutil.copy2(side, Path(str(db_copy) + suffix))
                except OSError:
                    pass
        conn = sqlite3.connect(db_copy)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """SELECT i.url, v.title, v.visit_time, v.origin
                   FROM history_visits v
                   JOIN history_items i ON i.id = v.history_item
                   WHERE i.url LIKE 'http%'"""
            ).fetchall()
        finally:
            conn.close()
    for row in rows:
        url = row["url"]
        if not url:
            continue
        device = "iphone-synced" if row["origin"] else "mac"
        yield NormalizedItem(
            kind="visit",
            dedupe_key=f"url:{canonicalize(url)}",
            url=url,
            title=(row["title"] or "")[:300] or None,
            created_at=_cocoa(row["visit_time"]),
            raw={"safari_origin": row["origin"]},
            occurrence_kind="visit",
            occurrence_detail=f"safari: {device}",
        )
