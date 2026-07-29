"""Optional short-link resolution (t.co, bit.ly), cached in url_resolutions.

Network access only happens behind --resolve-links; every resolved URL is
cached so a link is never fetched twice.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

SHORTENER_DOMAINS = {"t.co", "bit.ly", "buff.ly", "ow.ly", "tinyurl.com", "goo.gl", "trib.al"}


def is_short_link(url: str) -> bool:
    from km.urls import domain_of

    return domain_of(url) in SHORTENER_DOMAINS


def resolve_short_links(conn: sqlite3.Connection, urls: list[str], rate_delay: float = 0.5) -> dict[str, str]:
    """Resolve short links via HEAD requests, consulting the cache first.
    Returns {short_url: resolved_url} for everything resolvable."""
    import time

    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("link resolution needs the fetch extras: uv sync --extra fetch") from exc

    out: dict[str, str] = {}
    todo = []
    for url in urls:
        row = conn.execute(
            "SELECT resolved_url FROM url_resolutions WHERE short_url=?", (url,)
        ).fetchone()
        if row:
            out[url] = row["resolved_url"]
        elif is_short_link(url):
            todo.append(url)

    if not todo:
        return out
    client = httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": "km/0.1"})
    now = datetime.now(timezone.utc).isoformat()
    for url in todo:
        try:
            resp = client.head(url)
            resolved = str(resp.url)
        except httpx.HTTPError:
            resolved = url  # cache the failure as identity so we never retry forever
        conn.execute(
            "INSERT OR REPLACE INTO url_resolutions VALUES (?,?,?)", (url, resolved, now)
        )
        out[url] = resolved
        time.sleep(rate_delay)
    conn.commit()
    client.close()
    return out


def resolve_tweet_links(conn: sqlite3.Connection) -> int:
    """Resolve unresolved t.co links found in tweet text; store results in
    the cache table for extraction to use."""
    import re

    tco = re.compile(r"https?://t\.co/\w+")
    urls: set[str] = set()
    for row in conn.execute(
        """SELECT text FROM items
           WHERE kind IN ('like','retweet','own_tweet','bookmark_tweet')
           AND text LIKE '%t.co/%'"""
    ):
        urls.update(tco.findall(row["text"] or ""))
    resolved = resolve_short_links(conn, sorted(urls))
    return len(resolved)
