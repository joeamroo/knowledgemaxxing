"""SQLite schema and connection management.

One file (data/knowledge.db) holds everything: sources, deduped items,
per-source occurrences, classifications, user edits, scrape cursors,
FTS5 keyword index, and (when sqlite-vec is installed) embedding vectors.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  path_or_endpoint TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  file_hash TEXT,
  UNIQUE(path_or_endpoint, file_hash)
);

CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,
  url TEXT,
  canonical_url TEXT,
  domain TEXT,
  title TEXT,
  text TEXT,
  author TEXT,
  created_at TEXT,
  raw_json TEXT,
  dedupe_key TEXT UNIQUE NOT NULL,
  is_essay INTEGER DEFAULT 0,
  is_thread INTEGER DEFAULT 0,
  in_reading_list INTEGER DEFAULT 0,
  interest_score REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_canonical ON items(canonical_url);
CREATE INDEX IF NOT EXISTS idx_items_domain ON items(domain);
CREATE INDEX IF NOT EXISTS idx_items_kind ON items(kind);
CREATE INDEX IF NOT EXISTS idx_items_created ON items(created_at);

CREATE TABLE IF NOT EXISTS occurrences(
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id),
  source_id INTEGER NOT NULL REFERENCES sources(id),
  kind TEXT NOT NULL,
  occurred_at TEXT,
  detail TEXT,
  UNIQUE(item_id, source_id, kind, occurred_at)
);
CREATE INDEX IF NOT EXISTS idx_occ_item ON occurrences(item_id);

CREATE TABLE IF NOT EXISTS classifications(
  item_id INTEGER NOT NULL REFERENCES items(id),
  category TEXT NOT NULL,
  subcategories TEXT,
  confidence REAL,
  model TEXT,
  prompt_version TEXT,
  classified_at TEXT,
  PRIMARY KEY(item_id, prompt_version)
);

CREATE TABLE IF NOT EXISTS user_edits(
  item_id INTEGER PRIMARY KEY REFERENCES items(id),
  starred INTEGER DEFAULT 0,
  archived INTEGER DEFAULT 0,
  category_override TEXT,
  note TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS url_resolutions(
  short_url TEXT PRIMARY KEY,
  resolved_url TEXT,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS scrape_state(
  scraper TEXT PRIMARY KEY,
  cursor TEXT,
  last_run_at TEXT
);

CREATE TABLE IF NOT EXISTS embedding_cache(
  item_id INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  model TEXT NOT NULL,
  embedded_at TEXT,
  PRIMARY KEY(item_id, model)
);

CREATE TABLE IF NOT EXISTS tasks(
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  due TEXT,
  status TEXT NOT NULL DEFAULT 'open',  -- open | done | dropped
  source TEXT,                          -- manual | note:<title> | ai
  created_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS feeds(
  domain TEXT PRIMARY KEY,
  feed_url TEXT,
  last_fetched TEXT,
  ok INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS daily_feed(
  date TEXT NOT NULL,
  item_id INTEGER NOT NULL REFERENCES items(id),
  reason TEXT,
  position INTEGER,
  read INTEGER DEFAULT 0,
  PRIMARY KEY(date, item_id)
);

CREATE TABLE IF NOT EXISTS custom_categories(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL,   -- what belongs here; drives zero-shot assignment
  created_at TEXT NOT NULL,
  source TEXT                  -- ai | manual
);

CREATE TABLE IF NOT EXISTS companion_notes(
  id INTEGER PRIMARY KEY,
  persona TEXT NOT NULL,
  session_file TEXT NOT NULL UNIQUE,
  date TEXT NOT NULL,
  summary TEXT NOT NULL,        -- what was discussed, threads, follow-ups
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feed_ecology(
  domain TEXT PRIMARY KEY,
  population REAL DEFAULT 1.0,  -- fed when you read a source, starved when you skip
  updated TEXT
);

CREATE TABLE IF NOT EXISTS egress(
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  channel TEXT NOT NULL,        -- archivist | mcp | export-json | export-vault | export-list
  detail TEXT,                  -- tool or file involved
  item_count INTEGER DEFAULT 0,
  item_ids TEXT                 -- json array, capped
);

CREATE TABLE IF NOT EXISTS ai_spend(
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  model TEXT,
  context TEXT,            -- archivist | talk:<persona> | rerank | summary
  input_tokens INTEGER DEFAULT 0,
  output_tokens INTEGER DEFAULT 0,
  cache_creation INTEGER DEFAULT 0,
  cache_read INTEGER DEFAULT 0,
  cost_usd REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS smart_collections(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  spec TEXT NOT NULL,      -- json: {query?, mode?, filters?}
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content(
  item_id INTEGER PRIMARY KEY REFERENCES items(id),
  text TEXT,
  word_count INTEGER,
  fetched_at TEXT,
  ok INTEGER DEFAULT 1      -- 0: fetch failed or nothing readable there
);

-- expression index: julianday() is format-agnostic across the mixed ISO
-- timestamp styles different sources write, and it makes time-window joins
-- (the "read together" relatedness leg) sargable
CREATE INDEX IF NOT EXISTS idx_occurrences_jd ON occurrences(julianday(occurred_at));

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
  title, text, content=items, content_rowid=id
);

CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
  text, content=content, content_rowid=item_id
);

CREATE TRIGGER IF NOT EXISTS content_ai AFTER INSERT ON content BEGIN
  INSERT INTO content_fts(rowid, text) VALUES (new.item_id, coalesce(new.text,''));
END;
CREATE TRIGGER IF NOT EXISTS content_ad AFTER DELETE ON content BEGIN
  INSERT INTO content_fts(content_fts, rowid, text)
  VALUES ('delete', old.item_id, coalesce(old.text,''));
END;
CREATE TRIGGER IF NOT EXISTS content_au AFTER UPDATE OF text ON content BEGIN
  INSERT INTO content_fts(content_fts, rowid, text)
  VALUES ('delete', old.item_id, coalesce(old.text,''));
  INSERT INTO content_fts(rowid, text) VALUES (new.item_id, coalesce(new.text,''));
END;

CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
  INSERT INTO items_fts(rowid, title, text)
  VALUES (new.id, coalesce(new.title,''), coalesce(new.text,''));
END;
CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, text)
  VALUES ('delete', old.id, coalesce(old.title,''), coalesce(old.text,''));
END;
CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE OF title, text ON items BEGIN
  INSERT INTO items_fts(items_fts, rowid, title, text)
  VALUES ('delete', old.id, coalesce(old.title,''), coalesce(old.text,''));
  INSERT INTO items_fts(rowid, title, text)
  VALUES (new.id, coalesce(new.title,''), coalesce(new.text,''));
END;
"""


def get_db(path: Path | str, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) the knowledge database.

    check_same_thread=False is required for the web UI, where FastAPI's
    threadpool serves sync endpoints from multiple threads; WAL mode plus
    a busy timeout keeps that safe for the UI's read-heavy load.
    """
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def try_load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension if installed. Returns success."""
    try:
        import sqlite_vec  # type: ignore
    except ImportError:
        return False
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    return True
