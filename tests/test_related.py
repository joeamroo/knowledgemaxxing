"""Tests for multi-signal related items and quick bookmarking."""
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from km.db import get_db, try_load_sqlite_vec
from km.models import NormalizedItem
from km.search.related import related_items
from km.store import add_source, quick_bookmark, upsert_item

sqlite_vec_available = try_load_sqlite_vec(get_db(":memory:"))
needs_vec = pytest.mark.skipif(not sqlite_vec_available, reason="sqlite-vec not installed")


class FakeEmbedder:
    model_name = "fake-16d"
    dims = 16

    def encode(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dims
            for w in t.lower().split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dims] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out

    def encode_query(self, text):
        return self.encode([text])[0]


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "chrome_live_history", "h", "x")
    return conn, sid


def _visit(conn, sid, key, title, text="", when=None):
    item_id = upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key=f"url:{key}", title=title, text=text,
        url=f"https://{key}.example/p", created_at=when, raw={}), sid)
    conn.commit()
    return item_id


def test_lexical_and_temporal_legs_work_without_embeddings():
    conn, sid = _db()
    base = datetime(2026, 7, 1, 20, 0, tzinfo=timezone.utc)
    anchor = _visit(conn, sid, "anchor", "Fermentation basics",
                    "sourdough starter yeast hydration levain", when=base)
    lexical_twin = _visit(conn, sid, "twin", "Advanced sourdough levain hydration",
                          "yeast starter fermentation", when=base - timedelta(days=300))
    same_session = _visit(conn, sid, "session", "Completely unrelated video",
                          when=base + timedelta(minutes=10))
    _visit(conn, sid, "far", "Gardening tools", "trowel spade",
           when=base + timedelta(days=90))

    hits = related_items(conn, anchor)
    by_id = {h["id"]: h for h in hits}
    assert lexical_twin in by_id
    assert "shared language" in by_id[lexical_twin]["reasons"]
    assert same_session in by_id
    assert "read together" in by_id[same_session]["reasons"]
    assert anchor not in by_id


@needs_vec
def test_semantic_leg_and_reason_tags():
    from km.embedding.store import embed_pending

    conn, sid = _db()
    anchor = _visit(conn, sid, "anchor", "Fermentation basics",
                    "sourdough starter yeast hydration levain")
    semantic = _visit(conn, sid, "sem", "Bread microbiology",
                      "sourdough yeast hydration starter levain culture")
    embed_pending(conn, FakeEmbedder())

    hits = related_items(conn, anchor)
    match = next((h for h in hits if h["id"] == semantic), None)
    assert match is not None
    assert "same meaning" in match["reasons"]


def test_near_dupe_collapse():
    conn, sid = _db()
    anchor = _visit(conn, sid, "anchor", "Fermentation basics", "sourdough yeast starter")
    a = _visit(conn, sid, "dupe1", "Sourdough Guide", "sourdough yeast starter")
    conn.execute("UPDATE items SET domain='same.example' WHERE id IN (?,?)", (a, a))
    b = _visit(conn, sid, "dupe2", "Sourdough Guide", "sourdough yeast starter")
    conn.execute("UPDATE items SET domain='same.example' WHERE id=?", (b,))
    conn.commit()

    hits = related_items(conn, anchor)
    titles = [(h["title"] or "").lower() for h in hits]
    assert titles.count("sourdough guide") == 1


# ── bookmarking ───────────────────────────────────────────

def test_quick_bookmark_creates_item():
    conn, _ = _db()
    item_id = quick_bookmark(conn, "https://blog.example/great-post", title="Great Post")
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    assert row["kind"] == "bookmark"
    assert row["title"] == "Great Post"
    occ = conn.execute(
        "SELECT kind FROM occurrences WHERE item_id=?", (item_id,)).fetchall()
    assert any(o["kind"] == "bookmark" for o in occ)


def test_quick_bookmark_merges_with_existing_visit():
    conn, sid = _db()
    visited = upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key="url:https://blog.example/seen",
        title="Seen Before", url="https://blog.example/seen", raw={}), sid)
    conn.commit()

    from km.urls import canonicalize

    marked = quick_bookmark(conn, "https://blog.example/seen")
    if canonicalize("https://blog.example/seen") == "https://blog.example/seen":
        assert marked == visited  # same item, richer provenance
        kinds = {o["kind"] for o in conn.execute(
            "SELECT kind FROM occurrences WHERE item_id=?", (marked,))}
        assert "bookmark" in kinds


def test_quick_bookmark_rejects_non_http():
    conn, _ = _db()
    with pytest.raises(ValueError):
        quick_bookmark(conn, "not-a-url")
