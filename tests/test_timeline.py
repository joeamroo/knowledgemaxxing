from datetime import datetime, timezone

from km.db import get_db
from km.extract.timeline import compact_timeline_for_ai, monthly_signals, recurring_queries
from km.models import NormalizedItem
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "t", "t", "h")
    entries = [
        ("search:how to get over breakup", "search_query", "how to get over breakup", 2025, 1),
        ("search:how get over a breakup fast", "search_query", "how to get over breakup", 2025, 4),
        ("search:get over breakup", "search_query", "how to get over breakup", 2025, 9),
        ("search:django tutorial", "search_query", "django tutorial", 2025, 1),
        ("apple-note:n1", "note", None, 2025, 1),
        ("chat:c1", "chat_conversation", None, 2025, 1),
        ("url:https://gwern.net/a", "visit", None, 2025, 1),
        ("url:https://gwern.net/b", "visit", None, 2025, 1),
    ]
    for key, kind, text, year, month in entries:
        upsert_item(conn, NormalizedItem(
            kind=kind, dedupe_key=key, text=text,
            title="Note title" if kind == "note" else ("Chat about jobs" if kind == "chat_conversation" else None),
            url=key.split("url:")[-1] if key.startswith("url:") else None,
            created_at=datetime(year, month, 5, tzinfo=timezone.utc)), sid)
    return conn


def test_monthly_signals():
    months = monthly_signals(_db(), min_items=1)
    jan = months["2025-01"]
    assert any(t == "breakup" for t, _ in jan["search_terms"])
    assert "Note title" in jan["notes"]
    assert "Chat about jobs" in jan["chats"]
    assert any(d == "gwern.net" for d, _ in jan["domains"])


def test_recurring_queries_span_months():
    recurring = recurring_queries(_db(), min_months=3)
    assert len(recurring) == 1
    assert "breakup" in recurring[0]["query"]
    assert recurring[0]["span"] == 3
    # one-off searches are not loops
    assert all("django" not in r["query"] for r in recurring)


def test_compact_timeline_shape():
    compact = compact_timeline_for_ai(_db())
    assert compact and compact[0]["month"] == "2025-01"
    assert "searching" in compact[0]
