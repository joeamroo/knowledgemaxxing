from datetime import datetime, timezone

from km.db import get_db
from km.extract.reports import daily_digest, export_best_own_tweets, obsessions_by_year
from km.models import NormalizedItem
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "t", "h")
    upsert_item(conn, NormalizedItem(
        kind="search_query", dedupe_key="search:goodhart law examples",
        text="goodhart law examples",
        created_at=datetime(2024, 3, 1, tzinfo=timezone.utc)), sid)
    upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key="url:https://gwern.net/x", url="https://gwern.net/x",
        created_at=datetime(2024, 3, 2, tzinfo=timezone.utc)), sid)
    upsert_item(conn, NormalizedItem(
        kind="own_tweet", dedupe_key="tweet:1", text="my banger tweet",
        url="https://twitter.com/i/web/status/1",
        created_at=datetime(2023, 7, 25, tzinfo=timezone.utc),
        raw={"favorite_count": "120", "retweet_count": "10"}), sid)
    upsert_item(conn, NormalizedItem(
        kind="like", dedupe_key="tweet:2", text="liked on this very day",
        url="https://twitter.com/i/web/status/2",
        created_at=datetime(2023, 7, 25, tzinfo=timezone.utc)), sid)
    return conn


def test_obsessions_by_year():
    data = obsessions_by_year(_db())
    assert "2024" in data
    terms = dict(data["2024"]["terms"])
    assert "goodhart" in terms
    domains = dict(data["2024"]["domains"])
    assert "gwern.net" in domains


def test_best_own_tweets(tmp_path):
    out = tmp_path / "best.md"
    count = export_best_own_tweets(_db(), out)
    assert count == 1
    content = out.read_text()
    assert "my banger tweet" in content and "120 likes" in content


def test_daily_digest_on_this_day():
    conn = _db()
    digest = daily_digest(conn, today=datetime(2026, 7, 25, tzinfo=timezone.utc))
    labels = [e["label"] for e in digest["on_this_day"]]
    assert any("liked on this very day" in l or "my banger tweet" in l for l in labels)
    assert digest["on_this_day"][0]["years_ago"] == 3
