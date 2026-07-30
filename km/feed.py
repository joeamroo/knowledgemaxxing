"""The daily reading feed: a pipeline from your own archive to your morning.

Two halves, mixed:
- NEW: fresh posts from the blogs you demonstrably follow, discovered by
  probing common RSS/Atom endpoints on your most recurrent domains and
  the substacks you visit. Fetched politely, cached in the feeds table.
- OLD: things you saved or skimmed and never actually read, resurfaced:
  never-opened saves, essays you visited once years ago, links buried in
  bookmarked tweets.

build_daily_feed() freezes a dated list into daily_feed so the day's
reading is stable; marking read persists.
"""
from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

from km.models import NormalizedItem
from km.store import add_source, upsert_item
from km.urls import canonicalize

_FEED_PATHS = ("/feed", "/rss", "/feed.xml", "/rss.xml", "/atom.xml", "/index.xml")
_UA = {"User-Agent": "km-feed/1.0 (personal local reading feed)"}


def _http_client():
    import httpx

    return httpx.Client(timeout=10, follow_redirects=True, headers=_UA)


def discover_feed(client, domain: str) -> Optional[str]:
    for path in _FEED_PATHS:
        url = f"https://{domain}{path}"
        try:
            r = client.get(url)
        except Exception:
            continue
        if r.status_code == 200 and ("<rss" in r.text[:2000] or "<feed" in r.text[:2000]):
            return url
    return None


def parse_feed(text: str) -> list[dict]:
    """Minimal RSS 2.0 / Atom parser: title, url, published ISO."""
    out = []
    try:
        root = ET.fromstring(re.sub(r'\sxmlns="[^"]+"', "", text, count=1))
    except ET.ParseError:
        return out
    for item in root.iter("item"):  # RSS
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub = item.findtext("pubDate") or ""
        out.append({"title": title, "url": link, "published": _parse_date(pub)})
    for entry in root.iter("entry"):  # Atom
        title = (entry.findtext("title") or "").strip()
        link_el = entry.find("link")
        link = (link_el.get("href") if link_el is not None else "") or ""
        pub = entry.findtext("published") or entry.findtext("updated") or ""
        out.append({"title": title, "url": link, "published": _parse_date(pub)})
    return [e for e in out if e["url"]]


def _parse_date(raw: str) -> Optional[str]:
    raw = raw.strip()
    if not raw:
        return None
    from email.utils import parsedate_to_datetime

    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


_PLATFORM_DOMAINS = {
    "reddit.com", "old.reddit.com", "en.wikipedia.org", "wikipedia.org",
    "youtube.com", "x.com", "twitter.com", "github.com", "stackoverflow.com",
    "google.com", "facebook.com", "instagram.com", "linkedin.com", "amazon.com",
    "news.ycombinator.com", "imgur.com", "twitch.tv", "netflix.com",
}


def candidate_domains(conn: sqlite3.Connection, limit: int = 60) -> list[str]:
    """Domains worth probing for feeds: places you read essays, substacks,
    and long-recurring reading domains. Platforms are excluded; they are
    not blogs."""
    out: list[str] = []

    # never probed, whatever the browsing history says
    skip_re = re.compile(
        r"(^i\d?\.|^cdn|\.cdn\.|^static|^img|^media\.|wp\.com$|"
        r"porn|sex|xvideos|xnxx|xhamster|nsfw|redtube|onlyfans)")

    def push(domain: str):
        if domain.startswith("www."):
            domain = domain[4:]
        if (domain and domain not in out and domain not in _PLATFORM_DOMAINS
                and "." in domain and not skip_re.search(domain) and len(out) < limit):
            out.append(domain)

    for row in conn.execute(
        """SELECT domain, count(*) c FROM items
           WHERE is_essay=1 AND domain != ''
           AND title IS NOT NULL AND length(title) > 15
           AND title NOT LIKE 'Viewed image%'
           AND lower(url) NOT LIKE '%.jpg%' AND lower(url) NOT LIKE '%.jpeg%'
           AND lower(url) NOT LIKE '%.png%' AND lower(url) NOT LIKE '%.webp%'
           GROUP BY domain HAVING c >= 2 ORDER BY c DESC LIMIT 60"""):
        if not skip_re.search(row["domain"]):
            push(row["domain"])
    for row in conn.execute(
        """SELECT DISTINCT domain FROM items
           WHERE domain LIKE '%.substack.com' AND domain != ''"""):
        push(row["domain"])
    from km.extract.timeline import recurring_domains

    for entry in recurring_domains(conn, min_months=6):
        push(entry["domain"])
    return out


def refresh_feeds(conn: sqlite3.Connection, max_new_probes: int = 15) -> dict:
    """Discover missing feeds, fetch known-good ones, ingest fresh posts."""
    now = datetime.now(timezone.utc).isoformat()
    stats = {"probed": 0, "discovered": 0, "fetched": 0, "new_posts": 0}
    known = {r["domain"]: dict(r) for r in conn.execute("SELECT * FROM feeds")}
    with _http_client() as client:
        for domain in candidate_domains(conn):
            if domain in known or stats["probed"] >= max_new_probes:
                continue
            stats["probed"] += 1
            feed_url = discover_feed(client, domain)
            conn.execute(
                "INSERT OR REPLACE INTO feeds(domain, feed_url, last_fetched, ok) VALUES (?,?,?,?)",
                (domain, feed_url, None, 1 if feed_url else 0))
            if feed_url:
                stats["discovered"] += 1
        conn.commit()

        source_id, _ = add_source(conn, "reading_feed", "rss", "rss")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        for row in conn.execute("SELECT domain, feed_url FROM feeds WHERE ok=1 AND feed_url IS NOT NULL"):
            try:
                r = client.get(row["feed_url"])
                if r.status_code != 200:
                    continue
            except Exception:
                continue
            stats["fetched"] += 1
            for post in parse_feed(r.text)[:20]:
                if post["published"] and post["published"] < cutoff:
                    continue
                canonical = canonicalize(post["url"])
                item_id = upsert_item(conn, NormalizedItem(
                    kind="feed_post",
                    dedupe_key=f"url:{canonical}",
                    url=post["url"], title=post["title"] or post["url"],
                    created_at=datetime.fromisoformat(post["published"]) if post["published"] else None,
                    occurrence_kind="feed_post",
                ), source_id)
                conn.execute("UPDATE items SET domain=? WHERE id=? AND (domain IS NULL OR domain='')",
                             (row["domain"], item_id))
                stats["new_posts"] += 1
            conn.execute("UPDATE feeds SET last_fetched=? WHERE domain=?", (now, row["domain"]))
        conn.commit()
    return stats


def build_daily_feed(conn: sqlite3.Connection, date: Optional[str] = None, size: int = 10) -> int:
    """Freeze today's reading list: new posts up front, buried gems after."""
    date = date or datetime.now(timezone.utc).date().isoformat()
    if conn.execute("SELECT 1 FROM daily_feed WHERE date=?", (date,)).fetchone():
        return conn.execute(
            "SELECT count(*) FROM daily_feed WHERE date=?", (date,)).fetchone()[0]

    picks: list[tuple[int, str]] = []
    seen: set[int] = set()

    def take(sql, params, reason, n):
        for row in conn.execute(sql, params):
            if row["id"] not in seen and len([p for p in picks if p[1] == reason]) < n:
                picks.append((row["id"], reason))
                seen.add(row["id"])

    # fresh from the blogs you follow, newest first, never surfaced before
    take("""SELECT i.id FROM items i WHERE i.kind='feed_post'
            AND i.id NOT IN (SELECT item_id FROM daily_feed)
            ORDER BY i.created_at DESC LIMIT 30""", (), "new today", 4)
    # saved and never opened, best first
    take("""SELECT i.id FROM items i JOIN occurrences o ON o.item_id=i.id
            WHERE o.kind IN ('bookmark','saved_post','bookmark_tweet','linked_from')
            AND i.url IS NOT NULL AND i.is_essay=1
            AND i.id NOT IN (SELECT item_id FROM occurrences WHERE kind='visit')
            AND i.id NOT IN (SELECT item_id FROM daily_feed)
            GROUP BY i.id ORDER BY i.interest_score DESC, RANDOM() LIMIT 30""",
         (), "saved, never read", 3)
    # essays you touched once, years ago (real articles only: no image
    # blobs or search-viewer junk that fooled the essay heuristic)
    take("""SELECT i.id FROM items i WHERE i.is_essay=1 AND i.kind='visit'
            AND i.created_at < datetime('now', '-1 year')
            AND i.title IS NOT NULL AND length(i.title) > 15
            AND i.title NOT LIKE 'Viewed image%'
            AND lower(i.url) NOT LIKE '%.jpg%' AND lower(i.url) NOT LIKE '%.jpeg%'
            AND lower(i.url) NOT LIKE '%.png%' AND lower(i.url) NOT LIKE '%.webp%'
            AND lower(i.url) NOT LIKE '%.gif%'
            AND i.id NOT IN (SELECT item_id FROM daily_feed)
            ORDER BY i.interest_score DESC, RANDOM() LIMIT 20""",
         (), "you read this once, years ago", 2)
    # links buried inside things you saved
    take("""SELECT i.id FROM items i JOIN occurrences o ON o.item_id=i.id
            WHERE o.kind='linked_from' AND i.url IS NOT NULL
            AND i.id NOT IN (SELECT item_id FROM occurrences WHERE kind='visit')
            AND i.id NOT IN (SELECT item_id FROM daily_feed)
            GROUP BY i.id ORDER BY RANDOM() LIMIT 20""", (), "buried in your saves", 1)

    for position, (item_id, reason) in enumerate(picks[:size]):
        conn.execute(
            "INSERT OR IGNORE INTO daily_feed(date, item_id, reason, position) VALUES (?,?,?,?)",
            (date, item_id, reason, position))
    conn.commit()
    return len(picks[:size])


def get_daily_feed(conn: sqlite3.Connection, date: Optional[str] = None) -> list[dict]:
    date = date or datetime.now(timezone.utc).date().isoformat()
    rows = conn.execute(
        """SELECT f.item_id, f.reason, f.read, i.title, i.text, i.url, i.domain,
                  i.kind, i.created_at
           FROM daily_feed f JOIN items i ON i.id=f.item_id
           WHERE f.date=? ORDER BY f.position""", (date,)).fetchall()
    return [dict(r) for r in rows]


def mark_read(conn: sqlite3.Connection, item_id: int, date: Optional[str] = None) -> None:
    date = date or datetime.now(timezone.utc).date().isoformat()
    conn.execute("UPDATE daily_feed SET read=1 WHERE date=? AND item_id=?", (date, item_id))
    conn.commit()
