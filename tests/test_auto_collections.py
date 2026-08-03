"""Tests for auto topic collections and the is_thread filter."""
import hashlib
import json

import pytest

from km.db import get_db, try_load_sqlite_vec
from km.models import NormalizedItem
from km.store import add_source, upsert_item

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


def _corpus(n_per_topic=6):
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "chrome_live_history", "h", "x")
    topics = {
        "fermentation": "sourdough yeast brine pickles culture",
        "astronomy": "telescope nebula orbit galaxy star",
    }
    for topic, words in topics.items():
        for i in range(n_per_topic):
            item_id = upsert_item(conn, NormalizedItem(
                kind="visit", dedupe_key=f"{topic}:{i}",
                title=f"{topic.title()} notes part {i}: {words}",
                text=words, url=f"https://{topic}.example/{i}", raw={}), sid)
            conn.execute("UPDATE items SET is_essay=1, interest_score=2 WHERE id=?", (item_id,))
    conn.commit()
    return conn


@needs_vec
def test_generate_replaces_autos_keeps_manual():
    from km.embedding.store import embed_pending
    from km.search.topics import generate_auto_collections

    conn = _corpus()
    embed_pending(conn, FakeEmbedder())
    conn.execute(
        "INSERT INTO smart_collections(name, spec, created_at) VALUES (?,?,?)",
        ("Hand made", json.dumps({"query": "mine", "filters": {}}), "2026-01-01"))
    conn.commit()

    out = generate_auto_collections(conn, FakeEmbedder(), n_clusters=2, sample=50, min_size=3)
    assert out["created"], out
    first_ids = {c["id"] for c in out["created"]}

    rows = conn.execute("SELECT name, spec FROM smart_collections").fetchall()
    autos = [r for r in rows if json.loads(r["spec"]).get("auto")]
    assert len(autos) == len(first_ids)
    assert all(json.loads(r["spec"])["mode"] == "semantic" for r in autos)
    assert any(r["name"] == "Hand made" for r in rows)

    # regeneration replaces autos (same count, no accumulation), never the manual one
    out2 = generate_auto_collections(conn, FakeEmbedder(), n_clusters=2, sample=50, min_size=3)
    rows2 = conn.execute("SELECT id, name, spec FROM smart_collections").fetchall()
    autos2 = [r for r in rows2 if json.loads(r["spec"]).get("auto")]
    assert len(autos2) == len(out2["created"])
    assert len(rows2) == len(autos2) + 1  # autos replaced, not accumulated
    assert any(r["name"] == "Hand made" for r in rows2)


def test_generate_without_embeddings_errors():
    from km.search.topics import generate_auto_collections

    conn = get_db(":memory:")
    if not sqlite_vec_available:
        assert "error" in generate_auto_collections(conn, None)
        return
    assert "error" in generate_auto_collections(conn, FakeEmbedder())


def test_is_thread_filter():
    from km.search.keyword import Filters, keyword_search

    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "a", "h")
    thread = upsert_item(conn, NormalizedItem(
        kind="like", dedupe_key="tw:1", text="a long thread about ovens", raw={}), sid)
    upsert_item(conn, NormalizedItem(
        kind="like", dedupe_key="tw:2", text="a single tweet about ovens", raw={}), sid)
    conn.execute("UPDATE items SET is_thread=1 WHERE id=?", (thread,))
    conn.commit()

    hits = keyword_search(conn, "ovens", Filters(is_thread=True))
    assert [h for h, _ in hits] == [thread]
