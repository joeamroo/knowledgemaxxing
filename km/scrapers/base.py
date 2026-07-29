"""Scraper base: pacing, backoff, cursors, raw snapshots, clean stops.

Etiquette rules from the spec: 1-2 requests/second with jittered delays,
exponential backoff on errors, resumable cursors in scrape_state, save
raw snapshots to data/raw/, and on any login wall / captcha / rate limit
/ unknown layout: stop cleanly, save progress, explain. Never loop or
hammer.
"""
from __future__ import annotations

import json
import random
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from km.config import Config
from km.models import NormalizedItem
from km.store import add_source, get_scrape_cursor, set_scrape_cursor, upsert_item


class CleanStop(Exception):
    """Raised when a scraper must stop: progress is saved, reason is shown."""


class BaseScraper:
    name: str = "base"
    source_kind: str = "scrape"

    def __init__(self, conn: sqlite3.Connection, cfg: Config, context) -> None:
        self.conn = conn
        self.cfg = cfg
        self.context = context
        self.items_saved = 0
        self._raw_dir: Optional[Path] = None
        self._source_id: Optional[int] = None
        self._backoff = 2.0

    # pacing

    def pause(self, lo: float = 0.5, hi: float = 1.0) -> None:
        """Jittered delay keeping us at 1-2 requests/second."""
        time.sleep(random.uniform(lo, hi))

    def backoff(self) -> None:
        time.sleep(self._backoff + random.uniform(0, 1))
        self._backoff = min(self._backoff * 2, 60)

    def reset_backoff(self) -> None:
        self._backoff = 2.0

    # persistence

    @property
    def source_id(self) -> int:
        if self._source_id is None:
            self._source_id, _ = add_source(
                self.conn, self.source_kind, f"scraper:{self.name}", None
            )
        return self._source_id

    @property
    def cursor(self) -> Optional[str]:
        return get_scrape_cursor(self.conn, self.name)

    @cursor.setter
    def cursor(self, value: Optional[str]) -> None:
        set_scrape_cursor(self.conn, self.name, value)
        self.conn.commit()

    def save_item(self, item: NormalizedItem) -> int:
        item_id = upsert_item(self.conn, item, self.source_id)
        self.conn.commit()
        self.items_saved += 1
        return item_id

    def save_raw(self, name: str, content: str | bytes | dict) -> Path:
        """Snapshot raw HTML/JSON so parsers can be fixed and re-run offline."""
        if self._raw_dir is None:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self._raw_dir = self.cfg.data_dir / "raw" / self.name / stamp
            self._raw_dir.mkdir(parents=True, exist_ok=True)
        path = self._raw_dir / name
        if isinstance(content, dict):
            path.write_text(json.dumps(content, indent=2, default=str))
        elif isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)
        return path

    def stop(self, reason: str) -> None:
        """Stop cleanly: cursor and items are already committed."""
        raise CleanStop(
            f"{self.name} stopped: {reason}. "
            f"{self.items_saved} items saved this run; progress is stored, "
            "re-running will resume where it left off."
        )

    # subclasses implement

    def run(self) -> int:
        raise NotImplementedError
