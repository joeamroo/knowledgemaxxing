"""Personal analytics reports: obsessions by year, best own tweets, daily digest."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone

_STOPWORDS = set(
    """the a an and or of to in for on with how what is are was were why when
    where who which do does did can could should would will i my me you your
    it its this that these those from by at as be been being have has had not
    no vs versus best top new near me free online download login sign
    definition meaning example examples list""".split()
)


def obsessions_by_year(conn: sqlite3.Connection, per_year: int = 15) -> dict[str, dict]:
    """Top search terms and reading domains per year."""
    years: dict[str, dict] = {}
    for row in conn.execute(
        """SELECT substr(created_at, 1, 4) y, text FROM items
           WHERE kind='search_query' AND created_at IS NOT NULL AND text != ''"""
    ):
        year = row["y"]
        if not year or not year.startswith("20"):
            continue
        bucket = years.setdefault(year, {"terms": Counter(), "domains": Counter()})
        for word in re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", row["text"].lower()):
            if word not in _STOPWORDS:
                bucket["terms"][word] += 1
    for row in conn.execute(
        """SELECT substr(i.created_at, 1, 4) y, i.domain, count(*) c FROM items i
           WHERE i.kind='visit' AND i.created_at IS NOT NULL AND i.domain != ''
           AND i.domain NOT IN ('google.com','www.google.com')
           GROUP BY y, i.domain"""
    ):
        year = row["y"]
        if not year or not year.startswith("20"):
            continue
        years.setdefault(year, {"terms": Counter(), "domains": Counter()})
        years[year]["domains"][row["domain"]] += row["c"]
    return {
        year: {
            "terms": bucket["terms"].most_common(per_year),
            "domains": bucket["domains"].most_common(per_year),
        }
        for year, bucket in sorted(years.items())
    }


def export_obsessions(conn: sqlite3.Connection, out_path) -> int:
    data = obsessions_by_year(conn)
    lines = [
        "# Obsessions, year by year",
        "",
        "What you searched for and where you actually read, per year.",
        "",
    ]
    for year, bucket in data.items():
        lines.append(f"## {year}")
        lines.append("")
        if bucket["terms"]:
            terms = ", ".join(f"{t} ({c})" for t, c in bucket["terms"])
            lines.append(f"**Searched:** {terms}")
            lines.append("")
        if bucket["domains"]:
            domains = ", ".join(f"{d} ({c})" for d, c in bucket["domains"])
            lines.append(f"**Read:** {domains}")
            lines.append("")
    out_path.write_text("\n".join(lines) + "\n")
    return len(data)


def export_best_own_tweets(conn: sqlite3.Connection, out_path, top_n: int = 100) -> int:
    """My own tweets ranked by engagement (fav + 2x RT from the archive)."""
    scored = []
    for row in conn.execute(
        "SELECT text, url, created_at, raw_json FROM items WHERE kind='own_tweet' AND raw_json IS NOT NULL"
    ):
        try:
            raw = json.loads(row["raw_json"])
            favs = int(raw.get("favorite_count") or 0)
            rts = int(raw.get("retweet_count") or 0)
        except (json.JSONDecodeError, ValueError):
            continue
        engagement = favs + 2 * rts
        if engagement > 0:
            scored.append((engagement, favs, rts, row))
    scored.sort(key=lambda entry: -entry[0])
    lines = ["# My best tweets, by engagement", ""]
    for engagement, favs, rts, row in scored[:top_n]:
        text = " ".join((row["text"] or "").split())
        date = (row["created_at"] or "")[:10]
        lines.append(f"- {text}")
        lines.append(f"  {favs} likes · {rts} RTs · [{date}]({row['url']})")
    out_path.write_text("\n".join(lines) + "\n")
    return min(len(scored), top_n)


def daily_digest(conn: sqlite3.Connection, today: datetime | None = None) -> dict:
    """A memory mix: on-this-day across years, plus resurfaced gems."""
    today = today or datetime.now(timezone.utc)
    month_day = today.strftime("%m-%d")
    digest: dict = {"date": today.date().isoformat(), "on_this_day": [], "gems": []}
    for row in conn.execute(
        """SELECT kind, title, text, url, created_at FROM items
           WHERE strftime('%m-%d', created_at) = ?
           AND kind IN ('like','bookmark_tweet','note','bookmark','saved_post','own_tweet')
           AND substr(created_at, 1, 4) != ?
           ORDER BY RANDOM() LIMIT 6""",
        (month_day, str(today.year)),
    ):
        years_ago = today.year - int(row["created_at"][:4])
        digest["on_this_day"].append({
            "years_ago": years_ago, "kind": row["kind"],
            "label": row["title"] or (row["text"] or "")[:200], "url": row["url"],
        })
    for label, sql in [
        ("a piece of wisdom", """SELECT i.title, i.text, i.url FROM items i
            JOIN classifications c ON c.item_id=i.id
            WHERE c.category IN ('aphorism','natural_law','contrarian')
            ORDER BY RANDOM() LIMIT 1"""),
        ("an essay you saved but may never have read", """SELECT i.title, i.text, i.url
            FROM items i JOIN occurrences o ON o.item_id=i.id
            WHERE i.is_essay=1 AND o.kind IN ('bookmark','bookmark_tweet','saved_post','linked_from')
            AND i.id NOT IN (SELECT item_id FROM occurrences WHERE kind='visit')
            ORDER BY RANDOM() LIMIT 1"""),
        ("a note from your past self", """SELECT title, text, url FROM items
            WHERE kind='note' ORDER BY RANDOM() LIMIT 1"""),
    ]:
        row = conn.execute(sql).fetchone()
        if row:
            digest["gems"].append({
                "label": label,
                "title": row["title"] or (row["text"] or "")[:160],
                "snippet": (row["text"] or "")[:280],
                "url": row["url"],
            })
    return digest
