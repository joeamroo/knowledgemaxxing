from datetime import datetime, timezone

from km.db import get_db
from km.models import NormalizedItem
from km.store import add_source, get_scrape_cursor, set_scrape_cursor, stats, upsert_item


def make_db():
    return get_db(":memory:")


def test_schema_creates_and_fts_works():
    conn = make_db()
    sid, existed = add_source(conn, "test", "/tmp/x.json", "abc")
    assert not existed
    item = NormalizedItem(
        kind="visit", dedupe_key="url:https://guzey.com/post",
        url="https://guzey.com/post", title="Great essay", text="body words here",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    upsert_item(conn, item, sid)
    rows = conn.execute(
        "SELECT rowid FROM items_fts WHERE items_fts MATCH 'essay'"
    ).fetchall()
    assert len(rows) == 1


def test_source_idempotence():
    conn = make_db()
    sid1, existed1 = add_source(conn, "test", "/tmp/x.json", "abc")
    sid2, existed2 = add_source(conn, "test", "/tmp/x.json", "abc")
    assert sid1 == sid2 and not existed1 and existed2
    # same path, different hash: new source (file changed)
    sid3, existed3 = add_source(conn, "test", "/tmp/x.json", "def")
    assert sid3 != sid1 and not existed3


def test_dedupe_same_key_two_sources():
    conn = make_db()
    s1, _ = add_source(conn, "chrome", "/tmp/history", "h1")
    s2, _ = add_source(conn, "twitter_archive", "/tmp/like.js", "h2")
    a = NormalizedItem(
        kind="visit", dedupe_key="url:https://guzey.com/post",
        url="https://guzey.com/post?utm_source=tw", title="Essay",
        created_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )
    b = NormalizedItem(
        kind="like", dedupe_key="url:https://guzey.com/post",
        url="https://guzey.com/post", text="longer text than before ok",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    id1 = upsert_item(conn, a, s1)
    id2 = upsert_item(conn, b, s2)
    assert id1 == id2
    row = conn.execute("SELECT * FROM items WHERE id=?", (id1,)).fetchone()
    assert row["kind"] == "like"  # higher intent wins
    assert row["title"] == "Essay"  # gap filled
    assert row["created_at"].startswith("2024-01-01")  # earliest first-seen
    occ = conn.execute("SELECT count(*) c FROM occurrences WHERE item_id=?", (id1,)).fetchall()
    assert occ[0]["c"] == 2


def test_reingest_same_occurrence_is_noop():
    conn = make_db()
    sid, _ = add_source(conn, "chrome", "/tmp/history", "h1")
    item = NormalizedItem(
        kind="visit", dedupe_key="url:https://a.com/x", url="https://a.com/x",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    upsert_item(conn, item, sid)
    upsert_item(conn, item, sid)
    assert conn.execute("SELECT count(*) c FROM occurrences").fetchone()["c"] == 1


def test_scrape_cursor_roundtrip():
    conn = make_db()
    assert get_scrape_cursor(conn, "x_bookmarks") is None
    set_scrape_cursor(conn, "x_bookmarks", "tweet:123")
    assert get_scrape_cursor(conn, "x_bookmarks") == "tweet:123"
    set_scrape_cursor(conn, "x_bookmarks", "tweet:456")
    assert get_scrape_cursor(conn, "x_bookmarks") == "tweet:456"


def test_stats_shape():
    conn = make_db()
    sid, _ = add_source(conn, "chrome", "/tmp/history", "h1")
    upsert_item(
        conn,
        NormalizedItem(kind="visit", dedupe_key="url:https://a.com/x", url="https://a.com/x"),
        sid,
    )
    s = stats(conn)
    assert s["total_items"] == 1
    assert s["by_kind"] == {"visit": 1}
    assert s["by_source_kind"] == {"chrome": 1}
