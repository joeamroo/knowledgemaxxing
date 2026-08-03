"""Tests for deep_search (multi-variant retrieval + rerank) and map_topics."""
import hashlib

import pytest

from km.db import get_db, try_load_sqlite_vec
from km.models import NormalizedItem
from km.search.deep import deep_search, query_variants
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


class FakeReranker:
    """Scores by shared-word count with the query."""

    def predict(self, pairs):
        return [
            float(len(set(q.lower().split()) & set(t.lower().split())))
            for q, t in pairs
        ]


def _corpus():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "chrome_live_history", "h", "x")
    essays = [
        ("Why Software Estimates Fail", "estimation planning fallacy deadlines slip"),
        ("The Craft of Fermentation", "sourdough starters wild yeast patience"),
        ("Notes on Attention", "focus deep work distraction economy"),
        ("Compound Interest of Habits", "small actions compound over years"),
        ("Garden Design Principles", "paths borders perennial structure"),
    ]
    ids = {}
    for i, (title, text) in enumerate(essays):
        item_id = upsert_item(conn, NormalizedItem(
            kind="visit", dedupe_key=f"url:{i}", title=title, text=text,
            url=f"https://blog{i}.example/post", raw={}), sid)
        conn.execute(
            "UPDATE items SET is_essay=1, domain=?, interest_score=? WHERE id=?",
            (f"blog{i}.example", 5 - i, item_id))
        ids[title] = item_id
    conn.commit()
    return conn, ids


def test_query_variants_expand_meaningfully():
    variants = query_variants("that essay about small habits that compound over the years")
    assert variants[0].startswith("that essay")
    assert any("habits compound years" in v or "compound" in v for v in variants[1:])
    assert len(variants) <= 4
    assert query_variants("dogs") == ["dogs"]


def test_deep_search_keyword_only_no_reranker():
    conn, ids = _corpus()
    hits = deep_search(conn, None, "habits that compound", use_reranker=False)
    assert hits and hits[0]["id"] == ids["Compound Interest of Habits"]


def test_deep_search_fake_reranker_reorders():
    conn, ids = _corpus()
    hits = deep_search(conn, None, "wild yeast sourdough patience",
                       reranker=FakeReranker())
    assert hits[0]["id"] == ids["The Craft of Fermentation"]
    assert hits[0]["relevance"] >= hits[-1]["relevance"]


@needs_vec
def test_deep_search_with_vectors_and_filters():
    from km.embedding.store import embed_pending

    conn, ids = _corpus()
    emb = FakeEmbedder()
    embed_pending(conn, emb)
    hits = deep_search(conn, emb, "deep work and the distraction economy",
                       essays_only=True, use_reranker=False)
    assert any(h["id"] == ids["Notes on Attention"] for h in hits[:3])
    none = deep_search(conn, emb, "attention", domain="nowhere.example",
                       use_reranker=False)
    assert none == []


@needs_vec
def test_map_topics_clusters_and_labels():
    from km.embedding.store import embed_pending
    from km.search.topics import map_topics

    conn, _ = _corpus()
    emb = FakeEmbedder()
    embed_pending(conn, emb)
    out = map_topics(conn, emb, essays_only=True, n_clusters=3, sample=50)
    assert "clusters" in out, out
    assert out["sampled"] == 5
    assert sum(c["count"] for c in out["clusters"]) == 5
    for cluster in out["clusters"]:
        assert cluster["label"]
        assert cluster["items"] and cluster["items"][0]["title"]


def test_map_topics_needs_enough_items():
    from km.search.topics import map_topics

    conn = get_db(":memory:")
    if not sqlite_vec_available:
        assert "error" in map_topics(conn, None)
        return
    assert "error" in map_topics(conn, FakeEmbedder())
