"""Ingest: manifest -> parsers -> knowledge.db.

Idempotent: every file is hashed (zip members hash the member bytes) and
a (path, hash) pair already in sources is skipped. Malformed files log to
skipped.log and never crash the run.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from km.config import Config
from km.models import Manifest, ManifestEntry
from km.parsers import chrome_history
from km.parsers.base import NeedsMappingError, ParseContext
from km.parsers.registry import PARSERS

log = logging.getLogger(__name__)


class IngestReport:
    def __init__(self) -> None:
        self.ingested: list[tuple[str, int]] = []
        self.skipped: list[tuple[str, str]] = []
        self.already: list[str] = []

    @property
    def total_items(self) -> int:
        return sum(n for _, n in self.ingested)


def _read_entry_bytes(entry: ManifestEntry) -> bytes:
    if entry.zip_member:
        with zipfile.ZipFile(entry.path) as zf:
            return zf.read(entry.zip_member)
    return Path(entry.path).read_bytes()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_skipped_log(cfg: Config, skipped: list[tuple[str, str]]) -> None:
    if not skipped:
        return
    log_path = cfg.project_root / "skipped.log"
    stamp = datetime.now(timezone.utc).isoformat()
    with open(log_path, "a") as f:
        for path, reason in skipped:
            f.write(f"{stamp}\t{path}\t{reason}\n")


def ingest_manifest(
    conn: sqlite3.Connection, manifest: Manifest, cfg: Config,
    include_generic: bool = False,
) -> IngestReport:
    from km.store import add_source, upsert_item

    report = IngestReport()
    for entry in manifest.entries:
        if entry.source_type == "generic" and not include_generic:
            report.skipped.append(
                (entry.display_path, "generic URL file, review and re-run with --include-generic")
            )
            continue
        if entry.status == "needs_download":
            report.skipped.append((entry.display_path, "iCloud file not downloaded yet"))
            continue
        if entry.status == "needs_mapping" and entry.path not in cfg.column_mappings:
            report.skipped.append(
                (entry.display_path, "columns need a mapping in config.yaml")
            )
            continue
        if entry.status == "unsupported":
            report.skipped.append((entry.display_path, entry.note or "unsupported"))
            continue
        if entry.source_type in ("twitter_archive_zip", "takeout_zip", "zip"):
            continue  # their members are separate manifest entries

        ctx = ParseContext(entry=entry, config=cfg)
        try:
            if entry.source_type in ("chrome_live_history", "safari_history"):
                path = Path(entry.path)
                file_hash = _hash_file(path)
                source_id, existed = add_source(conn, entry.source_type, entry.path, file_hash)
                if existed:
                    report.already.append(entry.display_path)
                    continue
                if entry.source_type == "safari_history":
                    from km.parsers import safari_history

                    live_parse = safari_history.parse_path
                else:
                    live_parse = chrome_history.parse_path
                count = 0
                for item in live_parse(path, ctx):
                    upsert_item(conn, item, source_id)
                    count += 1
            else:
                parser = PARSERS.get(entry.source_type)
                if parser is None:
                    report.skipped.append(
                        (entry.display_path, f"no parser for {entry.source_type}")
                    )
                    continue
                data = _read_entry_bytes(entry)
                file_hash = _hash_bytes(data)
                source_id, existed = add_source(
                    conn, entry.source_type, entry.display_path, file_hash
                )
                if existed:
                    report.already.append(entry.display_path)
                    continue
                count = 0
                for item in parser(data, ctx):
                    upsert_item(conn, item, source_id)
                    count += 1
            conn.commit()
            report.ingested.append((entry.display_path, count))
        except NeedsMappingError as exc:
            conn.rollback()
            report.skipped.append((entry.display_path, f"needs column mapping: {exc}"))
        except Exception as exc:  # malformed files must never crash the run
            conn.rollback()
            log.warning("failed to ingest %s: %s", entry.display_path, exc)
            report.skipped.append((entry.display_path, f"{type(exc).__name__}: {exc}"))
    _write_skipped_log(cfg, report.skipped)
    return report
