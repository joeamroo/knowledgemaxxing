"""Retrospective tools across every artifact kind: tweets, notes, searches."""
from datetime import datetime, timezone

from km.db import get_db
from km.models import NormalizedItem
from km.search.tools import get_items, list_items, period_summary
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "a", "h")
    ids = {}
    seed = [
        ("like", "tw:1", None, "leetcode grind is a skill separate from engineering", "swe_sage", "2025-10-05"),
        ("bookmark_tweet", "tw:2", None, "system design interviews reward rehearsed frameworks " * 8, "hiring_hank", "2025-11-12"),
        ("search_query", "q:1", None, "how to answer behavioral interview questions", None, "2025-11-20"),
        ("note", "n:1", "Post-interview notes", "Froze on the rate limiter question again.", None, "2025-12-02"),
        ("chat_conversation", "chat:x", "Mock interview review",
         "user: what went wrong?\n\nassistant: pacing.", None, "2026-01-10"),
        ("visit", "url:v", "Grokking the System Design Interview", None, None, "2025-09-15"),
    ]
    for kind, key, title, text, author, date in seed:
        item_id = upsert_item(conn, NormalizedItem(
            kind=kind, dedupe_key=key, title=title, text=text, author=author,
            url="https://x.example/p" if kind == "visit" else None,
            created_at=datetime.fromisoformat(date + "T12:00:00+00:00"), raw={}), sid)
        ids[key] = item_id
    conn.execute("UPDATE items SET domain='educative.io' WHERE dedupe_key='url:v'")
    conn.commit()
    return conn, ids


def test_list_items_carries_tweet_text_and_author():
    conn, ids = _db()
    tweets = list_items(conn, kind="like")
    assert tweets[0]["text"].startswith("leetcode grind")
    assert tweets[0]["author"] == "swe_sage"
    # long tweet text truncates at 300 in the roster
    long_row = list_items(conn, kind="bookmark_tweet")[0]
    assert len(long_row["text"]) == 300


def test_get_items_bulk_any_kind():
    conn, ids = _db()
    out = get_items(conn, [ids["tw:1"], ids["n:1"], ids["q:1"], 99999])
    assert out["returned"] == 3
    assert out["missing_ids"] == [99999]
    kinds = {i["kind"] for i in out["items"]}
    assert kinds == {"like", "note", "search_query"}
    full = next(i for i in out["items"] if i["kind"] == "note")
    assert "rate limiter" in full["text"]


def test_get_items_caps_and_truncation():
    conn, ids = _db()
    out = get_items(conn, [ids["tw:2"]], max_chars_each=50)
    assert out["items"][0]["truncated"] is True
    capped = get_items(conn, list(range(1, 60)))
    assert "capped at 50" in capped.get("note", "")


def test_period_summary_maps_the_window():
    conn, ids = _db()
    s = period_summary(conn, "2025-09-01", "2026-03-01")
    assert s["total_items"] == 6
    assert s["by_kind"]["like"] == 1 and s["by_kind"]["note"] == 1
    assert "2025-11" in s["by_month"]
    assert s["top_domains"].get("educative.io") == 1
    assert s["search_queries"][0]["query"].startswith("how to answer")
    assert s["chat_conversations"][0]["title"] == "Mock interview review"
    assert s["chat_conversations"][0]["id"] == ids["chat:x"]
    # window edges respected
    narrow = period_summary(conn, "2025-12-01", "2025-12-31")
    assert narrow["total_items"] == 1
