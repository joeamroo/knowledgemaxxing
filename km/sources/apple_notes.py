"""Apple Notes source: local, incremental, no cloud round-trip.

Uses JXA (osascript -l JavaScript) against the Notes app. Metadata for
every note is fetched in bulk (fast); bodies are fetched one by one only
for notes modified since the stored cursor, so repeat syncs touch just
what changed. First run prompts macOS for Automation permission
(Terminal -> Notes) and can take a few minutes on large libraries.

Password-protected notes cannot be read and are skipped.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from km.config import Config
from km.models import NormalizedItem
from km.store import add_source, get_scrape_cursor, set_scrape_cursor, upsert_item

_METADATA_JXA = r"""
const app = Application('Notes');
const ids = app.notes.id();
const names = app.notes.name();
const mods = app.notes.modificationDate();
const creates = app.notes.creationDate();
const out = [];
for (let i = 0; i < ids.length; i++) {
  out.push({
    id: ids[i],
    name: names[i],
    modified: mods[i] ? mods[i].toISOString() : null,
    created: creates[i] ? creates[i].toISOString() : null,
  });
}
JSON.stringify(out);
"""

_BODY_JXA_TEMPLATE = r"""
const app = Application('Notes');
const ids = %s;
const out = {};
for (const id of ids) {
  try {
    const note = app.notes.byId(id);
    out[id] = {body: note.body(), folder: (() => {
      try { return note.container().name(); } catch (e) { return null; }
    })()};
  } catch (e) {
    out[id] = {body: null, folder: null, error: String(e)};
  }
}
JSON.stringify(out);
"""


class NotesAccessError(RuntimeError):
    pass


def _run_jxa(script: str, timeout: int = 600) -> str:
    try:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise NotesAccessError("osascript not found (is this macOS?)") from exc
    except subprocess.TimeoutExpired as exc:
        raise NotesAccessError(
            "Notes automation timed out; a very large library can take minutes "
            "on first sync, re-run to continue"
        ) from exc
    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        if "not allowed" in stderr.lower() or "-1743" in stderr:
            raise NotesAccessError(
                "macOS blocked Notes automation. Allow it in System Settings > "
                "Privacy & Security > Automation (your terminal -> Notes), then re-run."
            )
        raise NotesAccessError(f"osascript failed: {stderr[:300]}")
    return proc.stdout.strip()


def html_to_text(body: str) -> str:
    soup = BeautifulSoup(body or "", "lxml")
    text = soup.get_text("\n", strip=True)
    return text


def fetch_changed_notes(since_iso: Optional[str]) -> Iterator[dict]:
    """Yield {id, name, created, modified, body, folder} for notes modified
    after since_iso (all notes when None)."""
    metadata = json.loads(_run_jxa(_METADATA_JXA))
    changed = [
        m for m in metadata
        if m.get("modified") and (since_iso is None or m["modified"] > since_iso)
    ]
    # bodies in chunks so one enormous script never times out
    for start in range(0, len(changed), 40):
        chunk = changed[start:start + 40]
        ids_json = json.dumps([m["id"] for m in chunk])
        bodies = json.loads(_run_jxa(_BODY_JXA_TEMPLATE % ids_json))
        for meta in chunk:
            info = bodies.get(meta["id"]) or {}
            yield {**meta, "body": info.get("body"), "folder": info.get("folder")}


def sync_notes(conn: sqlite3.Connection, cfg: Config) -> tuple[int, int]:
    """Incremental Apple Notes sync. Returns (synced, skipped_locked)."""
    cursor = get_scrape_cursor(conn, "apple_notes")
    source_id, _ = add_source(conn, "apple_notes", "app:Notes", None)
    synced = 0
    locked = 0
    newest = cursor
    for note in fetch_changed_notes(cursor):
        if note["body"] is None:
            locked += 1  # password-protected or unreadable
            continue
        text = html_to_text(note["body"])
        if not text:
            continue
        created = note.get("created")
        item = NormalizedItem(
            kind="note",
            dedupe_key=f"apple-note:{note['id']}",
            title=note.get("name") or text.splitlines()[0][:120],
            text=text[:20000],
            created_at=datetime.fromisoformat(created.replace("Z", "+00:00"))
            if created else None,
            raw={"folder": note.get("folder"), "modified": note.get("modified")},
            occurrence_detail=f"apple notes: {note.get('folder') or 'Notes'}",
        )
        # a re-edited note should refresh its text: delete + re-upsert keeps it simple
        existing = conn.execute(
            "SELECT id FROM items WHERE dedupe_key=?", (item.dedupe_key,)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE items SET title=?, text=?, raw_json=? WHERE id=?",
                (item.title, item.text, json.dumps(item.raw), existing["id"]),
            )
            conn.execute(  # force re-embedding of the new text
                "DELETE FROM embedding_cache WHERE item_id=?", (existing["id"],)
            )
        else:
            upsert_item(conn, item, source_id)
        synced += 1
        if note.get("modified") and (newest is None or note["modified"] > newest):
            newest = note["modified"]
    if newest and newest != cursor:
        set_scrape_cursor(conn, "apple_notes", newest)
    conn.commit()
    return synced, locked
