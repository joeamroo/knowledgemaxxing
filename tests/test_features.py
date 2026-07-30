"""Tests for query syntax, similar items, and wrapped."""
from datetime import datetime, timezone

from km.db import get_db
from km.models import NormalizedItem
from km.search.keyword import Filters, parse_query
from km.store import add_source, upsert_item


def test_parse_query_operators():
    clean, f = parse_query("goodhart site:gwern.net kind:like before:2021 after:2019-06")
    assert clean == "goodhart"
    assert f.domain == "gwern.net"
    assert f.kind == "like"
    assert f.date_to == "2021-12-31"
    assert f.date_from == "2019-06"


def test_parse_query_keeps_unknown_and_invalid():
    clean, f = parse_query("http://a.com before:soon site:")
    assert "http://a.com" in clean and "before:soon" in clean and "site:" in clean
    assert f.date_to is None and f.domain is None


def test_parse_query_explicit_filters_win():
    _, f = parse_query("x site:b.com", Filters(domain="a.com"))
    assert f.domain == "a.com"


def test_similar_items_returns_neighbors():
    from km.embedding.store import ensure_vec_tables, serialize_f32, similar_items

    conn = get_db(":memory:")
    if not ensure_vec_tables(conn, 4):
        return  # sqlite-vec not installed in this env
    sid, _ = add_source(conn, "t", "t", "h")
    vecs = {
        "a": [1.0, 0.0, 0.0, 0.0],
        "b": [0.9, 0.1, 0.0, 0.0],
        "c": [0.0, 0.0, 1.0, 0.0],
    }
    ids = {}
    for key, vec in vecs.items():
        item_id = upsert_item(conn, NormalizedItem(
            kind="note", dedupe_key=f"apple-note:{key}", title=key, text=key,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)), sid)
        ids[key] = item_id
        cur = conn.execute(
            "INSERT INTO embedding_chunks(item_id, chunk_idx) VALUES (?, 0)", (item_id,))
        conn.execute("INSERT INTO vec_items(rowid, embedding) VALUES (?,?)",
                     (cur.lastrowid, serialize_f32(vec)))
    hits = similar_items(conn, ids["a"], limit=2)
    assert hits and hits[0][0] == ids["b"]
    assert ids["a"] not in [h[0] for h in hits]


def test_wrapped_renders():
    from km.exporters.wrapped import render_wrapped, wrapped_data

    conn = get_db(":memory:")
    sid, _ = add_source(conn, "t", "t", "h")
    for i in range(10):
        upsert_item(conn, NormalizedItem(
            kind="search_query", dedupe_key=f"s:{i}", text="rust borrow checker",
            created_at=datetime(2025, 1 + i % 6, 3, 14, 30, tzinfo=timezone.utc)), sid)
    data = wrapped_data(conn, "2025")
    page = render_wrapped(data)
    assert "2025" in page and "10" in page
    assert data["total"] == 10 and data["active_days"] >= 1
    assert "<script" not in page  # stays self-contained and inert
