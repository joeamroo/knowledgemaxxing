"""km rewind: a year in review built from your own traces.

The interesting part is not the top-N of the year (obsessions.md has
that) but what was NEW: terms that barely existed in prior years and
exploded, domains discovered for the first time, and the notes, chats,
and tweets that defined the year.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter

from km.extract.reports import _STOPWORDS

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'-]{2,}")


def _term_counts_by_year(conn: sqlite3.Connection) -> dict[str, Counter]:
    years: dict[str, Counter] = {}
    for row in conn.execute(
        """SELECT substr(created_at, 1, 4) y, text FROM items
           WHERE kind='search_query' AND created_at IS NOT NULL AND text != ''"""
    ):
        if not (row["y"] or "").startswith("20"):
            continue
        counter = years.setdefault(row["y"], Counter())
        for word in _WORD_RE.findall(row["text"].lower()):
            if word not in _STOPWORDS:
                counter[word] += 1
    return years


def new_obsessions(conn: sqlite3.Connection, year: str, top_n: int = 25) -> list[dict]:
    """Search terms that belong to this year: frequent now, rare before."""
    years = _term_counts_by_year(conn)
    this_year = years.get(year, Counter())
    before: Counter = Counter()
    for other_year, counter in years.items():
        if other_year < year:
            before.update(counter)
    out = []
    for term, count in this_year.most_common(400):
        if count >= 5 and before.get(term, 0) <= max(2, count // 5):
            out.append({"term": term, "count": count, "before": before.get(term, 0)})
        if len(out) >= top_n:
            break
    return out


def first_seen_domains(conn: sqlite3.Connection, year: str, min_visits: int = 10) -> list[dict]:
    """Domains you discovered this year and then kept visiting."""
    rows = conn.execute(
        """SELECT domain, substr(min(created_at), 1, 4) first_year, count(*) c
           FROM items WHERE kind='visit' AND created_at IS NOT NULL
           AND domain != '' GROUP BY domain HAVING first_year = ? AND c >= ?
           ORDER BY c DESC LIMIT 25""",
        (year, min_visits),
    ).fetchall()
    return [{"domain": r["domain"], "visits": r["c"]} for r in rows]


def year_rewind(conn: sqlite3.Connection, year: str) -> dict:
    counts = {
        row["kind"]: row["c"]
        for row in conn.execute(
            """SELECT kind, count(*) c FROM items
               WHERE substr(created_at, 1, 4) = ? GROUP BY kind""",
            (year,),
        )
    }
    notes = [
        row["title"] for row in conn.execute(
            """SELECT title FROM items WHERE kind='note' AND title != ''
               AND substr(created_at, 1, 4) = ? ORDER BY created_at""", (year,))
    ]
    chats = [
        row["title"] for row in conn.execute(
            """SELECT title FROM items WHERE kind='chat_conversation' AND title != ''
               AND substr(created_at, 1, 4) = ? ORDER BY created_at""", (year,))
    ]
    tweets = []
    for row in conn.execute(
        """SELECT text, url, raw_json FROM items WHERE kind='own_tweet'
           AND substr(created_at, 1, 4) = ? AND raw_json IS NOT NULL""", (year,)):
        try:
            raw = json.loads(row["raw_json"])
            score = int(raw.get("favorite_count") or 0) + 2 * int(raw.get("retweet_count") or 0)
        except (json.JSONDecodeError, ValueError):
            continue
        if score > 0:
            tweets.append({"text": row["text"], "url": row["url"], "score": score})
    tweets.sort(key=lambda t: -t["score"])
    return {
        "year": year,
        "counts": counts,
        "total": sum(counts.values()),
        "new_obsessions": new_obsessions(conn, year),
        "new_domains": first_seen_domains(conn, year),
        "notes": notes,
        "chats": chats,
        "best_tweets": tweets[:10],
    }


def export_rewind(conn: sqlite3.Connection, year: str, out_path) -> dict:
    data = year_rewind(conn, year)
    lines = [
        f"# {year} rewind",
        "",
        f"{data['total']:,} traces this year: "
        + ", ".join(f"{c:,} {k.replace('_', ' ')}s" for k, c in
                    sorted(data["counts"].items(), key=lambda kv: -kv[1])),
        "",
    ]
    if data["new_obsessions"]:
        lines += ["## New obsessions (rare before, big this year)", ""]
        for entry in data["new_obsessions"]:
            lines.append(
                f"- {entry['term']}: {entry['count']} searches "
                f"(only {entry['before']} in all prior years)")
        lines.append("")
    if data["new_domains"]:
        lines += ["## Places discovered this year", ""]
        for entry in data["new_domains"]:
            lines.append(f"- {entry['domain']} ({entry['visits']} visits)")
        lines.append("")
    if data["notes"]:
        lines += [f"## Every note written in {year} ({len(data['notes'])})", ""]
        lines += [f"- {t}" for t in data["notes"]]
        lines.append("")
    if data["chats"]:
        lines += [f"## Every AI conversation started in {year} ({len(data['chats'])})", ""]
        lines += [f"- {t}" for t in data["chats"]]
        lines.append("")
    if data["best_tweets"]:
        lines += ["## Your tweets that landed", ""]
        for tweet in data["best_tweets"]:
            text = " ".join((tweet["text"] or "").split())
            lines.append(f"- {text} ({tweet['score']} engagement, {tweet['url']})")
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n")
    return {"total": data["total"], "new_obsessions": len(data["new_obsessions"])}
