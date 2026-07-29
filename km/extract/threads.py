"""Thread detection.

Signals: self-reply chains in my own tweets, thread markers in
liked/bookmarked tweet text, and conversation ids captured by the X
bookmarks scraper.
"""
from __future__ import annotations

import json
import re
import sqlite3

_MARKER_RE = re.compile(r"(🧵|\b1/\d*\b|^1/|\ba thread\b|\bthread:\s|\(thread\))", re.IGNORECASE)


def mark_threads(conn: sqlite3.Connection) -> int:
    marked = 0

    # 1. Self-reply chains: my tweet replying to another of my tweets
    own_ids = {
        r["dedupe_key"].split(":", 1)[1]
        for r in conn.execute("SELECT dedupe_key FROM items WHERE kind='own_tweet'")
    }
    for row in conn.execute(
        "SELECT id, raw_json FROM items WHERE kind IN ('own_tweet','retweet') AND raw_json IS NOT NULL"
    ).fetchall():
        try:
            raw = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        reply_to = raw.get("in_reply_to_status_id")
        if reply_to and str(reply_to) in own_ids:
            for target in (row["id"],):
                conn.execute("UPDATE items SET is_thread=1 WHERE id=?", (target,))
            # the root of the chain is a thread too
            conn.execute(
                "UPDATE items SET is_thread=1 WHERE dedupe_key=?", (f"tweet:{reply_to}",)
            )
            marked += 1

    # 2. Marker text in any tweet-kind item
    for row in conn.execute(
        """SELECT id, text FROM items
           WHERE kind IN ('like','retweet','own_tweet','bookmark_tweet')
           AND text IS NOT NULL AND is_thread=0"""
    ).fetchall():
        if _MARKER_RE.search(row["text"]):
            conn.execute("UPDATE items SET is_thread=1 WHERE id=?", (row["id"],))
            marked += 1

    # 3. Conversation ids from the X bookmarks scraper: a bookmarked tweet whose
    # conversation_id differs from its own id is part of a thread
    for row in conn.execute(
        """SELECT id, dedupe_key, raw_json FROM items
           WHERE kind='bookmark_tweet' AND raw_json IS NOT NULL AND is_thread=0"""
    ).fetchall():
        try:
            raw = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        conv = raw.get("conversation_id")
        tweet_id = row["dedupe_key"].split(":", 1)[1]
        if conv and str(conv) != tweet_id:
            conn.execute("UPDATE items SET is_thread=1 WHERE id=?", (row["id"],))
            marked += 1

    conn.commit()
    return marked


def reconstruct_threads(conn: sqlite3.Connection) -> list[dict]:
    """Assemble full threads as ordered documents.

    Two sources: my own self-reply chains (in_reply_to pointing at my own
    tweets), and bookmarked tweets sharing a conversation_id (partial
    threads: only the parts I bookmarked, in order).
    """
    threads: list[dict] = []

    # own self-reply chains
    own: dict[str, dict] = {}
    for row in conn.execute(
        "SELECT id, dedupe_key, text, url, created_at, raw_json FROM items WHERE kind='own_tweet'"
    ).fetchall():
        tweet_id = row["dedupe_key"].split(":", 1)[1]
        try:
            raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        except json.JSONDecodeError:
            raw = {}
        own[tweet_id] = {
            "id": tweet_id, "item_id": row["id"], "text": row["text"] or "",
            "url": row["url"], "created_at": row["created_at"] or "",
            "parent": str(raw.get("in_reply_to_status_id") or "") or None,
        }
    children: dict[str, list[dict]] = {}
    for tweet in own.values():
        if tweet["parent"] and tweet["parent"] in own:
            children.setdefault(tweet["parent"], []).append(tweet)
    roots = [
        t for t in own.values()
        if (t["parent"] is None or t["parent"] not in own) and t["id"] in children
    ]
    for root in sorted(roots, key=lambda t: t["created_at"]):
        parts = [root]
        frontier = [root]
        seen = {root["id"]}
        while frontier:
            nxt: list[dict] = []
            for node in frontier:
                for child in children.get(node["id"], []):
                    if child["id"] not in seen:
                        seen.add(child["id"])
                        parts.append(child)
                        nxt.append(child)
            frontier = nxt
        parts.sort(key=lambda t: t["created_at"])
        if len(parts) >= 2:
            threads.append({"kind": "own", "parts": parts, "url": root["url"],
                            "date": root["created_at"][:10]})

    # bookmarked conversations: >= 2 bookmarks from the same conversation
    conversations: dict[str, list[dict]] = {}
    for row in conn.execute(
        """SELECT dedupe_key, text, url, author, created_at, raw_json
           FROM items WHERE kind='bookmark_tweet' AND raw_json IS NOT NULL"""
    ).fetchall():
        try:
            raw = json.loads(row["raw_json"])
        except json.JSONDecodeError:
            continue
        conv = raw.get("conversation_id")
        if conv:
            conversations.setdefault(str(conv), []).append({
                "id": row["dedupe_key"].split(":", 1)[1],
                "text": row["text"] or "", "url": row["url"],
                "author": row["author"], "created_at": row["created_at"] or "",
            })
    for conv_id, parts in conversations.items():
        if len(parts) >= 2:
            parts.sort(key=lambda t: t["created_at"])
            threads.append({"kind": "bookmarked", "parts": parts,
                            "url": parts[0]["url"], "date": parts[0]["created_at"][:10],
                            "author": parts[0].get("author")})
    return threads


def export_threads(conn: sqlite3.Connection, out_path) -> int:
    threads = reconstruct_threads(conn)
    lines = ["# Threads, reconstructed", ""]
    own = [t for t in threads if t["kind"] == "own"]
    booked = [t for t in threads if t["kind"] == "bookmarked"]
    if own:
        lines += [f"## My threads ({len(own)})", ""]
        for thread in own:
            first_line = thread["parts"][0]["text"].splitlines()[0][:100]
            lines.append(f"### {first_line} ({thread['date']})")
            lines.append("")
            for i, part in enumerate(thread["parts"], 1):
                text = " ".join(part["text"].split())
                lines.append(f"{i}. {text}")
            lines.append(f"\n[link]({thread['url']})\n")
    if booked:
        lines += [f"## Bookmarked threads, the parts I saved ({len(booked)})", ""]
        for thread in booked:
            author = f"@{thread['author']} " if thread.get("author") else ""
            first_line = thread["parts"][0]["text"].splitlines()[0][:100]
            lines.append(f"### {author}{first_line} ({thread['date']})")
            lines.append("")
            for i, part in enumerate(thread["parts"], 1):
                text = " ".join(part["text"].split())
                lines.append(f"{i}. {text}")
            lines.append(f"\n[link]({thread['url']})\n")
    out_path.write_text("\n".join(lines) + "\n")
    return len(threads)
