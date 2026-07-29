"""Tests for curiosity, rhythms, reading debt, and rewind."""
from datetime import datetime, timezone

from km.db import get_db
from km.extract.curiosity import is_question, questions_by_year
from km.extract.debt import reading_debt
from km.extract.rewind import new_obsessions, year_rewind
from km.extract.rhythms import activity_streaks, hourly_rhythms
from km.models import NormalizedItem
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "t", "t", "h")
    return conn, sid


def _put(conn, sid, kind, key, *, text=None, title=None, url=None, dt=None, raw=None):
    upsert_item(conn, NormalizedItem(
        kind=kind, dedupe_key=key, text=text, title=title, url=url,
        created_at=dt, raw=raw or {}), sid)


def test_is_question():
    assert is_question("how to get over a breakup")
    assert is_question("why do cats purr")
    assert is_question("is aspirin good for acne?")
    assert not is_question("django tutorial")
    assert not is_question("how to")  # too short
    assert not is_question("weather houston")


def test_questions_by_year_dedupes_and_counts():
    conn, sid = _db()
    for month, text in [(1, "how to learn rust"), (3, "How to learn Rust?"),
                        (5, "why is the sky blue")]:
        _put(conn, sid, "search_query", f"search:{month}:{text}", text=text,
             dt=datetime(2024, month, 2, tzinfo=timezone.utc))
    years = questions_by_year(conn)
    assert len(years["2024"]) == 2
    rust = next(e for e in years["2024"] if "rust" in e["question"].lower())
    assert rust["count"] == 2 and len(rust["months"]) == 2


def test_hourly_rhythms_skips_dateonly_midnight():
    conn, sid = _db()
    # real 2am activity, twice
    for i in range(2):
        _put(conn, sid, "search_query", f"s:{i}", text="late night",
             dt=datetime(2025, 1, 1 + i, 2, 30, tzinfo=timezone.utc))
    # date-only midnight rows must not count
    _put(conn, sid, "visit", "url:https://a.com/x", url="https://a.com/x",
         dt=datetime(2025, 1, 3, 0, 0, 0, tzinfo=timezone.utc))
    rhythms = hourly_rhythms(conn, tz="UTC")
    assert rhythms["total_timed"] == 2
    assert rhythms["by_hour"][2] == 2
    assert rhythms["by_hour"][0] == 0


def test_activity_streaks():
    conn, sid = _db()
    for day in (1, 2, 3, 7):
        _put(conn, sid, "search_query", f"s:{day}", text="x",
             dt=datetime(2025, 5, day, 12, 0, tzinfo=timezone.utc))
    streaks = activity_streaks(conn)
    assert streaks["active_days"] == 4
    assert streaks["longest"] == 3
    assert streaks["current"] == 1


def test_reading_debt_excludes_visited():
    conn, sid = _db()
    _put(conn, sid, "bookmark", "url:https://a.com/read", url="https://a.com/read",
         title="Read me", text="word " * 440,
         dt=datetime(2024, 1, 1, tzinfo=timezone.utc))
    # saved AND visited: not debt
    _put(conn, sid, "bookmark", "url:https://b.com/done", url="https://b.com/done",
         title="Done", dt=datetime(2024, 1, 1, tzinfo=timezone.utc))
    _put(conn, sid, "visit", "url:https://b.com/done", url="https://b.com/done",
         dt=datetime(2024, 2, 1, tzinfo=timezone.utc))
    debt = reading_debt(conn)
    assert debt["count"] == 1
    assert debt["items"][0]["url"] == "https://a.com/read"
    assert debt["items"][0]["words"] == 440


def test_rewind_new_obsessions():
    conn, sid = _db()
    # background term, heavy in prior years
    for i in range(8):
        _put(conn, sid, "search_query", f"old:{i}", text="python tutorial",
             dt=datetime(2023, 1 + i % 12, 3, tzinfo=timezone.utc))
    # new obsession in 2025
    for i in range(6):
        _put(conn, sid, "search_query", f"new:{i}", text="rust borrow checker",
             dt=datetime(2025, 1 + i, 3, tzinfo=timezone.utc))
    _put(conn, sid, "search_query", "again", text="python tutorial",
         dt=datetime(2025, 2, 3, tzinfo=timezone.utc))
    terms = [e["term"] for e in new_obsessions(conn, "2025")]
    assert "rust" in terms and "borrow" in terms
    assert "python" not in terms
    data = year_rewind(conn, "2025")
    assert data["total"] == 7
