"""Reading list detection and aggregation across all sources."""
from __future__ import annotations

import json
import re
import sqlite3

from km.discover.patterns import URL_RE
from km.urls import domain_of

_TITLE_PATTERNS = re.compile(
    r"(reading list|blogroll|best books|favorite essays|favourite essays|"
    r"links\b|digest|syllabus|canon\b|recommended reading|book recommendations|"
    r"what to read|best essays|favorite blogs)",
    re.IGNORECASE,
)
_URL_PATTERNS = re.compile(
    r"(reading-?list|blogroll|best-?books|favorite-?essays|links|digest|syllabus|canon)",
    re.IGNORECASE,
)
_CHAT_ASK_RE = re.compile(
    r"(recommend|reading list|what should i read|best books on|books about|"
    r"essays about|who should i read)",
    re.IGNORECASE,
)


def _tweet_external_links(row: sqlite3.Row) -> list[str]:
    urls: set[str] = set()
    if row["raw_json"]:
        try:
            raw = json.loads(row["raw_json"])
            urls.update(raw.get("expanded_urls") or [])
            urls.update(raw.get("urls") or [])
        except json.JSONDecodeError:
            pass
    if row["text"]:
        urls.update(URL_RE.findall(row["text"]))
    return [
        u for u in urls
        if domain_of(u) not in ("twitter.com", "x.com", "t.co", "mobile.twitter.com")
    ]


def mark_reading_lists(conn: sqlite3.Connection) -> int:
    marked = 0

    # 1. Page titles/URLs that look like lists (covers HN favorites and Reddit
    # saves pointing at list-type pages, since those are URL items too).
    # Search engines, job boards, and app UIs match the word patterns but are
    # never reading lists.
    never_lists = (
        "google.com", "bing.com", "duckduckgo.com", "linkedin.com",
        "claude.ai", "chatgpt.com", "notion.so", "canvas.uh.edu",
        "teams.cdn.office.net", "x.com", "twitter.com", "youtube.com",
    )
    for row in conn.execute(
        """SELECT id, title, canonical_url, domain FROM items
           WHERE canonical_url IS NOT NULL AND in_reading_list=0
           AND kind NOT IN ('like','retweet','own_tweet','bookmark_tweet',
                            'chat_conversation','chat_message','search_query')"""
    ).fetchall():
        domain = row["domain"] or ""
        if any(domain == d or domain.endswith("." + d) for d in never_lists):
            continue
        title = row["title"] or ""
        url = row["canonical_url"] or ""
        if _TITLE_PATTERNS.search(title) or _URL_PATTERNS.search(url.split("//", 1)[-1]):
            conn.execute("UPDATE items SET in_reading_list=1 WHERE id=?", (row["id"],))
            marked += 1

    # 2. Tweets with 2+ distinct external links
    for row in conn.execute(
        """SELECT id, text, raw_json FROM items
           WHERE kind IN ('like','retweet','own_tweet','bookmark_tweet')
           AND in_reading_list=0"""
    ).fetchall():
        if len(set(_tweet_external_links(row))) >= 2:
            conn.execute("UPDATE items SET in_reading_list=1 WHERE id=?", (row["id"],))
            marked += 1

    # 3. AI chats where my messages ask for recommendations
    for row in conn.execute(
        """SELECT id, text FROM items
           WHERE kind='chat_conversation' AND in_reading_list=0 AND text IS NOT NULL"""
    ).fetchall():
        user_lines = [
            line for line in row["text"].splitlines()
            if line.startswith(("user:", "human:"))
        ]
        if any(_CHAT_ASK_RE.search(line) for line in user_lines):
            conn.execute("UPDATE items SET in_reading_list=1 WHERE id=?", (row["id"],))
            marked += 1

    conn.commit()
    return marked


def aggregate_reading_lists(conn: sqlite3.Connection) -> list[dict]:
    """Deduped reading-list entries with provenance, for reading-lists.md."""
    out = []
    for row in conn.execute(
        """SELECT i.id, i.title, i.canonical_url, i.url, i.text, i.kind,
                  i.domain, i.raw_json
           FROM items i WHERE i.in_reading_list=1
           ORDER BY i.domain, i.title"""
    ).fetchall():
        sources = [
            f"{r['kind']}" + (f" ({r['detail']})" if r["detail"] else "")
            for r in conn.execute(
                """SELECT o.kind, s.kind AS source_kind, o.detail
                   FROM occurrences o JOIN sources s ON s.id=o.source_id
                   WHERE o.item_id=?""",
                (row["id"],),
            ).fetchall()
        ]
        links = []
        if row["kind"] in ("like", "retweet", "own_tweet", "bookmark_tweet"):
            links = sorted(set(_tweet_external_links(row)))
        out.append(
            {
                "id": row["id"],
                "title": row["title"] or (row["text"] or "")[:120] or row["canonical_url"],
                "url": row["url"] or row["canonical_url"],
                "kind": row["kind"],
                "domain": row["domain"],
                "sources": sources,
                "links": links,
            }
        )
    return out
