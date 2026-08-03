"""Tests for the brainstorm batch: feed ecology, egress ledger, coverage
audit, link rot, negative selection, quick capture, episodes, resurface."""
from datetime import datetime, timedelta, timezone

from km.db import get_db
from km.models import NormalizedItem
from km.store import add_source, quick_note, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "chrome_live_history", "h", "x")
    return conn, sid


def _item(conn, sid, key, kind="visit", domain=None, created=None, **kw):
    item_id = upsert_item(conn, NormalizedItem(
        kind=kind, dedupe_key=key, created_at=created,
        title=kw.pop("title", f"Item {key}"), url=kw.pop("url", f"https://x.example/{key}"),
        **kw), sid)
    if domain:
        conn.execute("UPDATE items SET domain=? WHERE id=?", (domain, item_id))
    conn.commit()
    return item_id


# ── feed ecology ──────────────────────────────────────────

def test_ecology_feeds_on_read_and_starves_unread():
    from km.feed import feed_ecology_starve, mark_read

    conn, sid = _db()
    read_item = _item(conn, sid, "a", kind="feed_post", domain="loved.blog")
    skipped = _item(conn, sid, "b", kind="feed_post", domain="ignored.blog")
    conn.execute("INSERT INTO daily_feed(date, item_id, reason, position) VALUES ('2026-08-01', ?, 'new today', 0)", (read_item,))
    conn.execute("INSERT INTO daily_feed(date, item_id, reason, position) VALUES ('2026-08-01', ?, 'new today', 1)", (skipped,))
    conn.commit()

    mark_read(conn, read_item, date="2026-08-01")
    feed_ecology_starve(conn, "2026-08-02")

    pop = {r["domain"]: r["population"] for r in conn.execute("SELECT * FROM feed_ecology")}
    assert pop["loved.blog"] > 1.0
    assert pop["ignored.blog"] < 1.0


def test_ecology_population_bounds():
    from km.feed import mark_read

    conn, sid = _db()
    item = _item(conn, sid, "a", kind="feed_post", domain="d.blog")
    conn.execute("INSERT INTO daily_feed(date, item_id, reason, position) VALUES ('2026-08-01', ?, 'x', 0)", (item,))
    for _ in range(20):
        mark_read(conn, item, date="2026-08-01")
    pop = conn.execute("SELECT population FROM feed_ecology WHERE domain='d.blog'").fetchone()[0]
    assert pop <= 10.0


def test_igniting_topics_detects_swarm():
    from km.feed import igniting_topics

    conn, sid = _db()
    now = datetime.now(timezone.utc)
    # steady baseline domain: a few hits spread over 90 days
    for i in range(6):
        _item(conn, sid, f"base{i}", domain="steady.com",
              created=now - timedelta(days=20 + i * 10))
    # swarming domain: 8 hits in the last week, nothing before
    for i in range(8):
        _item(conn, sid, f"hot{i}", domain="newobsession.io",
              created=now - timedelta(days=i % 7))
    hot = igniting_topics(conn, days=14, baseline_days=90, min_count=5, ratio=3.0)
    domains = [t["domain"] for t in hot]
    assert "newobsession.io" in domains
    assert "steady.com" not in domains


# ── egress ledger ─────────────────────────────────────────

def test_egress_records_and_reports():
    from km.egress import egress_report, record_egress

    conn, sid = _db()
    a = _item(conn, sid, "a")
    record_egress(conn, "archivist", "search_archive",
                  payload=[{"id": a, "title": "x"}, {"id": 99}])
    record_egress(conn, "export-json", "/tmp/x.jsonl", count=500)
    report = egress_report(conn)
    assert report["by_channel"]["archivist"]["items"] == 2
    assert report["by_channel"]["export-json"]["items"] == 500
    assert len(report["recent"]) == 2


def test_egress_never_raises_on_bad_input():
    from km.egress import record_egress

    conn, _ = _db()
    record_egress(conn, "archivist", "tool", payload=object())  # unwalkable
    assert conn.execute("SELECT count(*) FROM egress").fetchone()[0] == 1


# ── coverage + link rot ───────────────────────────────────

def test_coverage_reports_gaps():
    from km.audit import coverage

    conn, sid = _db()
    for i in range(60):
        _item(conn, sid, f"v{i}", created=datetime(2020, 1, 1, tzinfo=timezone.utc))
    report = coverage(conn)
    assert report["totals"]["items"] == 60
    assert report["by_kind"][0]["kind"] == "visit"
    assert not report["healthy"]  # nothing embedded
    assert report["by_year"][0]["year"] == "2020"


def test_dead_saves_lists_rot():
    from km.fetch_content import dead_saves

    conn, sid = _db()
    saved = _item(conn, sid, "s", kind="bookmark")
    fine = _item(conn, sid, "f", kind="bookmark")
    conn.execute("INSERT INTO content(item_id, text, word_count, fetched_at, ok) VALUES (?, NULL, 0, 'now', 0)", (saved,))
    conn.execute("INSERT INTO content(item_id, text, word_count, fetched_at, ok) VALUES (?, 'body', 1, 'now', 1)", (fine,))
    conn.commit()
    rot = dead_saves(conn)
    assert [r["id"] for r in rot] == [saved]


# ── quick capture ─────────────────────────────────────────

def test_quick_note_lands_on_timeline():
    conn, _ = _db()
    item_id = quick_note(conn, "Interview prep: revisit rate limiting\nwith the token bucket writeup")
    row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    assert row["kind"] == "note"
    assert row["title"].startswith("Interview prep")
    assert "token bucket" in row["text"]
    # two notes in the same second stay distinct
    other = quick_note(conn, "another thought")
    assert other != item_id


def test_quick_note_rejects_empty():
    import pytest

    conn, _ = _db()
    with pytest.raises(ValueError):
        quick_note(conn, "   ")


# ── episodes ──────────────────────────────────────────────

def test_find_episodes_stitches_sessions():
    from km.search.tools import find_episodes

    conn, sid = _db()
    base = datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc)
    # a 10-visit rabbit hole, 5 minutes apart
    for i in range(10):
        _item(conn, sid, f"rh{i}", domain="wiki.example",
              created=base + timedelta(minutes=5 * i), title=f"Deep dive {i}")
    # a lone visit 3 hours later: not an episode
    _item(conn, sid, "lone", created=base + timedelta(hours=4))

    eps = find_episodes(conn, min_items=8)
    assert len(eps) == 1
    assert eps[0]["visits"] == 10
    assert eps[0]["top_domains"] == {"wiki.example": 10}
    assert len(eps[0]["item_ids"]) == 10


# ── negative selection (cluster culling) ─────────────────

def test_negative_selection_culls_self_matching_labels(monkeypatch):
    import json

    from km.search import topics

    conn, _ = _db()
    conn.execute(
        "INSERT INTO smart_collections(name, spec, created_at) VALUES (?,?,?)",
        ("fermentation sourdough", json.dumps({"query": "x", "filters": {}}), "2026-01-01"))
    conn.commit()

    monkeypatch.setattr(topics, "map_topics", lambda *a, **k: {
        "sampled": 20,
        "clusters": [
            {"label": "sourdough / fermentation / yeast", "count": 9, "items": []},
            {"label": "telescope / orbit / nebula", "count": 9, "items": []},
        ],
    })
    out = topics.generate_auto_collections(conn, embedder=None, min_size=3)
    names = [c["name"] for c in out["created"]]
    assert any("telescope" in n for n in names)
    assert not any("sourdough" in n for n in names)  # culled: recognizes self
