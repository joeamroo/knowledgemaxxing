"""km mentor: an AI reading of the whole archive.

Two personas over the same evidence pack:
- analyst: a psychoanalytic read; drives, avoidances, identity vs behavior
- harsh: a no-BS mentor; where the time actually goes, called out plainly

The evidence pack samples the corpus (likes, own tweets, searches, chat
topics, notes, essay domains, category mix) inside a fixed token budget.
Only text content is sent, consistent with classification privacy rules.
"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timezone

_NOTES_EMPHASIS = """
The person's private Apple Notes (private_notes, given in full) are your primary \
evidence: unperformed writing addressed to nobody, where they plan, vent, list, and \
promise things to themselves. Ground your reading in the notes first: what they write \
to themselves, what they repeat across notes, what they started and abandoned, the \
gap between notes-promises and everything else. Then use likes, searches, chats, and \
reading as corroborating (or contradicting) behavioral evidence."""

PERSONAS = {
    "analyst": """You are a perceptive psychoanalyst reading someone's complete digital \
exhaust: years of what they liked, searched, read, wrote, and noted. You infer the person \
behind the data: their drives, recurring preoccupations, self-image versus revealed \
behavior, what they circle around but avoid, what they aspire to versus what they \
actually spend attention on. Be specific and evidence-based: quote or reference actual \
items from the data for every claim. Be honest but humane; the goal is insight the \
person could not see alone. Structure: 1) Who this person is trying to become, \
2) Recurring preoccupations (with evidence), 3) Tensions and contradictions, \
4) What they avoid, 5) The unasked question they should sit with.""" + _NOTES_EMPHASIS,
    "harsh": """You are a brutally honest mentor who has just read someone's complete \
digital exhaust: years of likes, searches, reading, notes, and AI conversations. \
No flattery, no hedging, no therapy-speak. Tell them what the data actually shows: \
where their attention really goes versus what they claim to care about, the loops \
they are stuck in, the aspirational content they consume as a substitute for acting, \
the gap between input (reading about X) and output (doing X). Every hard claim must \
cite specific evidence from the data. End with marching orders: 3 to 5 blunt, concrete \
directives for the next 90 days, each tied to a pattern you found. You respect them \
enough to not soften anything. Pay special attention to promises they made to \
themselves in their notes and did not keep.""" + _NOTES_EMPHASIS,
}


def _sample(conn: sqlite3.Connection, sql: str, params: tuple = (), n: int = 50) -> list:
    rows = conn.execute(sql, params).fetchall()
    random.seed(42)  # reproducible packs keep re-runs comparable
    return random.sample(rows, n) if len(rows) > n else rows


def build_evidence_pack(conn: sqlite3.Connection) -> dict:
    """Sampled, text-only view of the archive within a token budget."""
    pack: dict = {"generated_at": datetime.now(timezone.utc).isoformat()[:10]}

    from km.store import stats as get_stats

    s = get_stats(conn)
    pack["scale"] = {
        "total_items": s["total_items"],
        "by_kind": s["by_kind"],
        "top_domains": s["top_domains"][:20],
        "categories": s.get("categories") or {},
    }
    # ALL private notes, in full where possible: the primary evidence
    pack["private_notes"] = [
        {
            "title": r["title"],
            "folder": (json.loads(r["raw_json"]) if r["raw_json"] else {}).get("folder"),
            "written": (r["created_at"] or "")[:10],
            "text": (r["text"] or "")[:2500],
        }
        for r in conn.execute(
            "SELECT title, text, raw_json, created_at FROM items WHERE kind='note' ORDER BY created_at"
        )
    ]
    pack["liked_tweets_random"] = [
        r["text"][:280] for r in _sample(
            conn, "SELECT text FROM items WHERE kind='like' AND text != ''", n=140)
    ]
    pack["liked_tweets_recent"] = [
        r["text"][:280] for r in conn.execute(
            """SELECT text FROM items WHERE kind='like' AND text != ''
               ORDER BY created_at DESC LIMIT 40""")
    ]
    pack["own_tweets"] = [
        r["text"][:280] for r in _sample(
            conn, "SELECT text FROM items WHERE kind='own_tweet' AND text != ''", n=60)
    ]
    pack["searches_recent"] = [
        r["text"][:120] for r in conn.execute(
            """SELECT text FROM items WHERE kind='search_query' AND text != ''
               ORDER BY created_at DESC LIMIT 80""")
    ]
    pack["searches_random"] = [
        r["text"][:120] for r in _sample(
            conn, "SELECT text FROM items WHERE kind='search_query' AND text != ''", n=80)
    ]
    pack["chat_topics"] = [
        {"title": r["title"], "opening": (r["text"] or "")[:400]}
        for r in _sample(
            conn, "SELECT title, text FROM items WHERE kind='chat_conversation'", n=50)
    ]
    from km.extract.timeline import compact_timeline_for_ai, recurring_queries

    pack["life_timeline_by_month"] = compact_timeline_for_ai(conn)
    pack["recurring_searches"] = recurring_queries(conn)[:40]
    pack["essays_read"] = [
        r["title"] for r in _sample(
            conn, "SELECT title FROM items WHERE is_essay=1 AND title IS NOT NULL", n=80)
    ]
    pack["saved"] = [
        r["title"] for r in _sample(
            conn,
            """SELECT title FROM items
               WHERE kind IN ('saved_post','bookmark_tweet','bookmark','favorite')
               AND title IS NOT NULL""", n=60)
    ]
    return pack


def run_mentor(conn: sqlite3.Connection, client, model: str, persona: str) -> str:
    if persona not in PERSONAS:
        raise ValueError(f"unknown persona {persona!r}; choose from {list(PERSONAS)}")
    from km.classify.client import call_claude

    pack = build_evidence_pack(conn)
    user = (
        "Here is the person's digital exhaust, sampled from an archive of "
        f"{pack['scale']['total_items']:,} items:\n\n"
        + json.dumps(pack, ensure_ascii=False, indent=1)
        + "\n\nWrite your reading of this person in Markdown. Do not use em dashes."
    )
    return call_claude(client, model, PERSONAS[persona], user, max_tokens=8000)


def estimate_pack_cost(conn: sqlite3.Connection, model: str):
    from km.classify.client import estimate_cost

    pack_json = json.dumps(build_evidence_pack(conn))
    return estimate_cost([pack_json], prompt_overhead_chars=2000, model=model, batch_size=1)
