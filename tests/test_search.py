from datetime import datetime, timezone

from km.db import get_db
from km.embedding.chunking import chunk_text, content_for_item
from km.models import NormalizedItem
from km.search.hybrid import fetch_results, hybrid_search, rrf_merge
from km.search.keyword import Filters, keyword_search
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "scraper:test", "h")
    items = [
        ("tweet:1", "like", None,
         "Everyone says work hard but people kept replying with contradictory advice pairs like take it easy"),
        ("tweet:2", "like", None, "A joke about compilers walking into a bar"),
        ("url:https://guzey.com/why-blog", "visit", "Why you should start a blog", None),
    ]
    for key, kind, title, text in items:
        upsert_item(
            conn,
            NormalizedItem(
                kind=kind, dedupe_key=key, title=title, text=text,
                url="https://guzey.com/why-blog" if key.startswith("url") else None,
                created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
            sid,
        )
    return conn


def test_rrf_merge_basic():
    # item 10 is rank 1 in both lists: must win
    merged = rrf_merge([[10, 20, 30], [10, 30, 40]])
    assert merged[0][0] == 10
    ids = [i for i, _ in merged]
    assert set(ids) == {10, 20, 30, 40}
    # 30 appears in both lists (ranks 3 and 2), 20 only once at rank 2
    assert ids.index(30) < ids.index(20)


def test_rrf_scores_formula():
    merged = dict(rrf_merge([[1], [1]], k=60))
    assert abs(merged[1] - 2 / 61) < 1e-9


def test_keyword_search_and_filters():
    conn = _db()
    hits = keyword_search(conn, "contradictory advice")
    assert len(hits) == 1
    hits_filtered = keyword_search(conn, "contradictory advice", Filters(kind="visit"))
    assert hits_filtered == []


def test_keyword_search_special_chars_safe():
    conn = _db()
    # would crash FTS5 syntax if unescaped
    assert keyword_search(conn, 'advice AND "pairs" (weird)') is not None


def test_hybrid_falls_back_to_keyword():
    conn = _db()
    scored = hybrid_search(conn, "contradictory advice", embedder=None, k=5)
    assert scored
    results = fetch_results(conn, scored)
    assert "contradictory advice" in results[0]["snippet"]
    assert results[0]["sources"] == ["twitter_archive"]


def test_chunking_tweet_single_chunk():
    conn = _db()
    row = conn.execute("SELECT * FROM items WHERE dedupe_key='tweet:1'").fetchone()
    chunks = content_for_item(row)
    assert len(chunks) == 1


def test_chunk_text_splits_with_prefix():
    text = ("A paragraph about things. " * 50 + "\n\n") * 4
    chunks = chunk_text(text, prefix="My conversation: ")
    assert len(chunks) > 1
    assert all(c.startswith("My conversation: ") for c in chunks)
    assert all(len(c) <= 2100 for c in chunks)
