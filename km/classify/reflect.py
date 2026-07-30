"""km reflect: an AI reading of your recent traces.

Where km mentor reads the whole archive, reflect reads the last few
weeks: what you searched at 2am, what you wrote, what you kept coming
back to, and says what it sees. Meant as a recurring ritual.

Only item text goes to the API, never paths or identity metadata.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone

_REFLECT_SYSTEM = """You are a perceptive, warm, direct companion who has just read \
someone's digital traces from the last few weeks: their searches, private notes, AI \
conversations, reading, and saves. Write them a short reflection (250 to 400 words):
- Open with what actually preoccupied them, named plainly, not a summary list.
- Surface one pattern they probably have not noticed (timing, repetition, contradiction \
between what they say they care about and where the hours went).
- Quote at most two of their own traces back to them, verbatim, when it sharpens the point.
- End with one gentle, concrete question worth sitting with this week.
No flattery, no therapy-speak, no bullet points, no em dashes. Write in second person."""


def gather_recent(conn: sqlite3.Connection, days: int = 30) -> dict:
    """Compact pack of the last N days. Text only."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    pack: dict = {"days": days}
    pack["searches"] = [
        r["text"] for r in conn.execute(
            """SELECT text FROM items WHERE kind='search_query' AND created_at >= ?
               ORDER BY created_at DESC LIMIT 200""", (since,))
    ]
    pack["notes"] = [
        {"title": r["title"], "text": (r["text"] or "")[:1500]}
        for r in conn.execute(
            """SELECT title, text FROM items WHERE kind='note' AND created_at >= ?
               ORDER BY created_at DESC LIMIT 30""", (since,))
    ]
    pack["chats"] = [
        r["title"] for r in conn.execute(
            """SELECT title FROM items WHERE kind='chat_conversation'
               AND created_at >= ? AND title != '' ORDER BY created_at DESC LIMIT 60""",
            (since,))
    ]
    domains = Counter(
        r["domain"] for r in conn.execute(
            """SELECT domain FROM items WHERE kind='visit' AND created_at >= ?
               AND domain != ''""", (since,))
    )
    pack["reading"] = [d for d, _ in domains.most_common(25)]
    pack["late_night_searches"] = [
        r["text"] for r in conn.execute(
            """SELECT text FROM items WHERE kind='search_query' AND created_at >= ?
               AND substr(created_at, 12, 2) IN ('05','06','07','08','09')
               ORDER BY created_at DESC LIMIT 40""", (since,))
    ]  # 05-09 UTC is roughly midnight-4am US Central
    pack["saved"] = [
        r["title"] or (r["text"] or "")[:120]
        for r in conn.execute(
            """SELECT title, text FROM items
               WHERE kind IN ('bookmark','bookmark_tweet','saved_post')
               AND created_at >= ? ORDER BY created_at DESC LIMIT 40""", (since,))
    ]
    return pack


def estimate_reflect_cost(pack: dict) -> float:
    import json

    tokens = len(json.dumps(pack, ensure_ascii=False)) / 4 + 600
    return tokens / 1e6 * 3 + 0.015


def run_reflect(conn: sqlite3.Connection, client, model: str, days: int = 30) -> str:
    import json

    pack = gather_recent(conn, days)
    response = client.messages.create(
        model=model,
        max_tokens=900,
        system=_REFLECT_SYSTEM,
        messages=[{
            "role": "user",
            "content": f"Their last {days} days of traces:\n" + json.dumps(pack, ensure_ascii=False),
        }],
    )
    return "".join(b.text for b in response.content if b.type == "text")
