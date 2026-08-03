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

# A starter canon of blogs worth following even before your own history
# suggests them. Merged into feed discovery alongside your real trail;
# extend or override with `feeds.seed_blogs` in config.yaml.
SEED_BLOGS = [
    "paulgraham.com", "gwern.net", "astralcodexten.com", "slatestarcodex.com",
    "marginalrevolution.com", "stratechery.com", "nabeelqu.co", "guzey.com",
    "patrickcollison.com", "danluu.com", "jsomers.net", "sive.rs",
    "applieddivinitystudies.com", "thezvi.substack.com", "worksinprogress.co",
    "commoncog.com", "fs.blog", "waitbutwhy.com", "meltingasphalt.com",
    "kk.org", "calnewport.com", "benkuhn.net", "juliagalef.com",
    "overcomingbias.com", "putanumonit.com", "experimental-history.com",
    "rootsofprogress.org", "nintil.com", "slimemoldtimemold.com",
]

# Lists of lists: pages whose whole point is pointing at great reading.
# km enrich mines their links into your essay pool.
CURATED_LISTS = [
    "https://nabeelqu.co/reading-lists",
    "https://nabeelqu.co/advice",
    "https://patrickcollison.com/bookshelf",
    "https://guzey.com/favorite/blog-posts/",
    "https://gwern.net/about",
    "https://www.benkuhn.net/weeklyessays/",
    "https://aaronsw.com/weblog/fullarchive",
    "https://slimemoldtimemold.com/links/",
]


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
    for domain in SEED_BLOGS:
        push(domain)
    return out


def enrich_from_curated_lists(conn: sqlite3.Connection, limit_pages: int = 10) -> dict:
    """Mine the curated lists-of-lists into the essay pool.

    Each list page's content links become 'linked' items (reading-list
    marked), which flow into the feed's buried-gems bucket and the Essays
    collection once heuristics run.
    """
    from km.extract.link_expansion import extract_outbound_links

    source_id, _ = add_source(conn, "curated_lists", "km-enrich", "curated")
    stats = {"pages": 0, "links": 0}
    with _http_client() as client:
        for list_url in CURATED_LISTS[:limit_pages]:
            try:
                r = client.get(list_url)
                if r.status_code != 200:
                    continue
            except Exception:
                continue
            stats["pages"] += 1
            for link in extract_outbound_links(r.text, list_url):
                canonical = canonicalize(link["url"])
                item_id = upsert_item(conn, NormalizedItem(
                    kind="linked",
                    dedupe_key=f"url:{canonical}",
                    url=link["url"], title=link.get("title") or link["url"],
                    occurrence_kind="linked_from",
                    occurrence_detail=f"curated:{list_url}",
                ), source_id)
                conn.execute(
                    "UPDATE items SET in_reading_list=1 WHERE id=?", (item_id,))
                stats["links"] += 1
    conn.commit()
    return stats


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


def feedback_source(conn: sqlite3.Connection, item_id: int, direction: int) -> Optional[dict]:
    """Explicit feed feedback: 'more of this source' (+1) or 'less' (-1).
    The ONLY thing that moves a source's population; km never adjusts it
    on its own. Bounded [0.1, 10]. Returns the new state or None."""
    row = conn.execute("SELECT domain FROM items WHERE id=?", (item_id,)).fetchone()
    if not row or not row["domain"]:
        return None
    step = 1.0 if direction > 0 else -1.0
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO feed_ecology(domain, population, updated)
           VALUES (?, ?, ?)
           ON CONFLICT(domain) DO UPDATE
           SET population = min(10.0, max(0.1, population + ?)), updated = ?""",
        (row["domain"], max(0.1, 1.0 + step), now, step, now))
    conn.commit()
    pop = conn.execute(
        "SELECT population FROM feed_ecology WHERE domain=?", (row["domain"],)
    ).fetchone()["population"]
    return {"domain": row["domain"], "population": round(pop, 2)}


def igniting_topics(
    conn: sqlite3.Connection,
    days: int = 14,
    baseline_days: int = 90,
    min_count: int = 5,
    ratio: float = 3.0,
) -> list[dict]:
    """Quorum sensing over your own attention: domains whose recent activity
    density crossed a threshold against their own baseline. A topic announces
    itself the moment it goes from stray to swarm."""
    rows = conn.execute(
        """SELECT domain,
                  sum(created_at >= datetime('now', ?)) AS recent,
                  sum(created_at < datetime('now', ?)
                      AND created_at >= datetime('now', ?)) AS baseline
           FROM items
           WHERE domain IS NOT NULL
             AND created_at >= datetime('now', ?)
           GROUP BY domain""",
        (f"-{days} days", f"-{days} days",
         f"-{baseline_days + days} days", f"-{baseline_days + days} days"),
    ).fetchall()
    out = []
    for r in rows:
        recent = r["recent"] or 0
        if recent < min_count:
            continue
        recent_rate = recent / days
        base_rate = (r["baseline"] or 0) / baseline_days
        if recent_rate >= ratio * max(base_rate, 1 / baseline_days):
            out.append({
                "domain": r["domain"], "recent": recent,
                "per_day": round(recent_rate, 2),
                "baseline_per_day": round(base_rate, 2),
            })
    out.sort(key=lambda t: -t["per_day"])
    return out[:8]


def consolidation_pick(conn: sqlite3.Connection) -> Optional[int]:
    """Sleep-phase replay: one item from years ago that lives in the same
    embedding neighborhood as something you touched this week. The value is
    the juxtaposition, not the item."""
    try:
        from km.embedding.store import similar_items
    except ImportError:
        return None
    recent = conn.execute(
        """SELECT i.id FROM items i JOIN embedding_chunks c ON c.item_id = i.id
           WHERE i.created_at >= datetime('now', '-7 days')
           GROUP BY i.id ORDER BY RANDOM() LIMIT 5""",
    ).fetchall()
    for row in recent:
        for neighbor_id, _ in similar_items(conn, row["id"], limit=8):
            old = conn.execute(
                """SELECT id FROM items WHERE id=?
                   AND created_at < datetime('now', '-3 years')
                   AND id NOT IN (SELECT item_id FROM daily_feed)""",
                (neighbor_id,),
            ).fetchone()
            if old:
                return old["id"]
    return None


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

    # fresh from the blogs you follow: population-weighted (sources you
    # actually read outcompete ones you skip), then newest first
    take("""SELECT i.id FROM items i
            LEFT JOIN feed_ecology e ON e.domain = i.domain
            WHERE i.kind='feed_post'
            AND i.id NOT IN (SELECT item_id FROM daily_feed)
            ORDER BY coalesce(e.population, 1.0) DESC, i.created_at DESC
            LIMIT 30""", (), "new today", 4)
    # sleep-phase consolidation: something old that echoes this week
    pair = consolidation_pick(conn)
    if pair is not None and pair not in seen:
        picks.append((pair, "echoes something from this week"))
        seen.add(pair)
    # quorum sensing: a topic of yours just went from stray to swarm
    for topic in igniting_topics(conn)[:1]:
        take("""SELECT i.id FROM items i
                WHERE i.domain = ? AND i.is_essay = 1
                AND i.id NOT IN (SELECT item_id FROM daily_feed)
                ORDER BY i.created_at DESC LIMIT 5""",
             (topic["domain"],), f"swarming: {topic['domain']}", 1)
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
    # web discoveries: essays found because you loved something similar
    take("""SELECT i.id FROM items i JOIN occurrences o ON o.item_id=i.id
            WHERE o.kind='web_discovery' AND i.url IS NOT NULL
            AND i.id NOT IN (SELECT item_id FROM occurrences WHERE kind='visit')
            AND i.id NOT IN (SELECT item_id FROM daily_feed)
            GROUP BY i.id ORDER BY RANDOM() LIMIT 10""",
         (), "similar to something you loved", 1)

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


def _xml_escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def export_opml(conn: sqlite3.Connection, out_path) -> int:
    """Write every discovered feed to an OPML file importable by any RSS reader.

    km discovers the RSS on blogs you actually read; this lets you take those
    subscriptions with you. Returns the number of feeds written.
    """
    from pathlib import Path

    out_path = Path(out_path)
    rows = conn.execute(
        "SELECT domain, feed_url FROM feeds WHERE feed_url IS NOT NULL AND ok=1 "
        "ORDER BY domain"
    ).fetchall()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<opml version="2.0">',
        "  <head><title>km discovered feeds</title></head>",
        "  <body>",
    ]
    for row in rows:
        domain = _xml_escape(row["domain"])
        url = _xml_escape(row["feed_url"])
        lines.append(
            f'    <outline type="rss" text="{domain}" title="{domain}" xmlUrl="{url}"/>'
        )
    lines += ["  </body>", "</opml>"]
    out_path.write_text("\n".join(lines) + "\n")
    return len(rows)


def import_opml(conn: sqlite3.Connection, in_path) -> dict:
    """Seed the feeds table from a reader's OPML export (the export's inverse).

    Walks every <outline> with an xmlUrl, at any nesting depth (readers group
    feeds in folders). Domain comes from htmlUrl when present, else from the
    feed URL host. Existing domains are left untouched so a re-import never
    clobbers km's own discoveries. Returns {"added": n, "skipped": n}.
    """
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from urllib.parse import urlparse

    root = ET.parse(Path(in_path)).getroot()
    added = skipped = 0
    for outline in root.iter("outline"):
        feed_url = (outline.get("xmlUrl") or "").strip()
        if not feed_url:
            continue
        site = (outline.get("htmlUrl") or "").strip()
        host = urlparse(site or feed_url).netloc.lower()
        domain = host[4:] if host.startswith("www.") else host
        if not domain:
            skipped += 1
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO feeds(domain, feed_url, last_fetched, ok) "
            "VALUES (?, ?, NULL, 1)",
            (domain, feed_url),
        )
        if cur.rowcount:
            added += 1
        else:
            skipped += 1
    conn.commit()
    return {"added": added, "skipped": skipped}
