"""Links of links: expand saved list-pages into their constituent links.

Reading-list pages you saved (blogrolls, digests, best-of pages) are
themselves indexes. This fetches each one (rate-limited, cached forever
in data/cache/pages/) and mines its outbound links into the archive as
kind "linked" items, provenance "linked_from <the page>". Second-order
discoveries then flow through essay detection, search, and exports like
everything else.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from km.config import Config
from km.models import NormalizedItem
from km.store import add_source, upsert_item
from km.urls import canonicalize, domain_of

_SKIP_DOMAINS = {
    "twitter.com", "x.com", "t.co", "facebook.com", "instagram.com",
    "youtube.com", "youtu.be", "linkedin.com", "reddit.com", "google.com",
    "amazon.com", "apple.com", "play.google.com", "en.wikipedia.org",
}
_SKIP_PATH_RE = re.compile(
    r"(login|signin|signup|subscribe|privacy|terms|about|contact|cart|"
    r"share|comment|feed|rss|\.(png|jpe?g|gif|svg|css|js|ico|pdf|zip))",
    re.IGNORECASE,
)
_NAV_PARENTS = {"nav", "header", "footer", "aside"}


def extract_outbound_links(html: str, base_url: str, max_links: int = 120) -> list[dict]:
    """Content links from a list-page: external, titled, non-navigational."""
    soup = BeautifulSoup(html, "lxml")
    base_domain = domain_of(base_url)
    out: list[dict] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        if any(parent.name in _NAV_PARENTS for parent in anchor.parents):
            continue
        href = urljoin(base_url, anchor["href"].strip())
        if not href.startswith(("http://", "https://")):
            continue
        domain = domain_of(href)
        if not domain or domain == base_domain or domain in _SKIP_DOMAINS:
            continue
        if _SKIP_PATH_RE.search(href):
            continue
        title = anchor.get_text(" ", strip=True)
        if len(title) < 4 or title.lower() in ("here", "link", "read more", "more"):
            continue
        canonical = canonicalize(href)
        if canonical in seen:
            continue
        seen.add(canonical)
        out.append({"url": href, "title": title[:300]})
        if len(out) >= max_links:
            break
    return out


def _list_pages(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT id, url, canonical_url, title FROM items
           WHERE in_reading_list = 1
           AND url LIKE 'http%'
           AND kind NOT IN ('like','retweet','own_tweet','bookmark_tweet',
                            'chat_conversation','chat_message','search_query')
           ORDER BY interest_score DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()


def expand_links(
    conn: sqlite3.Connection, cfg: Config, limit: int = 120,
    progress: Optional[callable] = None,
) -> dict:
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("link expansion needs the fetch extras: uv sync --extra fetch") from exc

    cache_dir = cfg.data_dir / "cache" / "pages"
    cache_dir.mkdir(parents=True, exist_ok=True)
    source_id, _ = add_source(conn, "link_expansion", "links-of-links", None)
    client = httpx.Client(
        timeout=15, follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh) km/0.1 personal-archive"},
    )
    pages = _list_pages(conn, limit)
    results = {"pages": 0, "links": 0, "errors": 0, "by_page": []}
    for row in pages:
        page_url = row["url"]
        key = hashlib.sha256(page_url.encode()).hexdigest()
        cache_file = cache_dir / f"{key}.html"
        html = None
        if cache_file.exists():
            html = cache_file.read_text(errors="replace")
        else:
            try:
                resp = client.get(page_url)
                if resp.status_code == 200:
                    html = resp.text
                    cache_file.write_text(html)
                else:
                    results["errors"] += 1
            except httpx.HTTPError:
                results["errors"] += 1
            time.sleep(0.8)  # 1-2 requests/second etiquette
        if not html:
            continue
        links = extract_outbound_links(html, page_url)
        for link in links:
            item = NormalizedItem(
                kind="linked",
                dedupe_key=f"url:{canonicalize(link['url'])}",
                url=link["url"],
                title=link["title"],
                raw={"linked_from": page_url, "list_title": row["title"]},
                occurrence_kind="linked_from",
                occurrence_detail=f"found on: {(row['title'] or page_url)[:90]}",
            )
            upsert_item(conn, item, source_id)
        conn.commit()
        results["pages"] += 1
        results["links"] += len(links)
        results["by_page"].append({"page": row["title"] or page_url, "url": page_url,
                                   "found": len(links)})
        if progress:
            progress(results["pages"], len(pages), page_url)
    client.close()
    return results


def export_links_of_links(conn: sqlite3.Connection, out_path) -> int:
    rows = conn.execute(
        """SELECT i.title, i.url, i.raw_json, i.is_essay
           FROM items i
           JOIN occurrences o ON o.item_id = i.id AND o.kind = 'linked_from'
           GROUP BY i.id ORDER BY i.raw_json, i.title"""
    ).fetchall()
    by_page: dict[str, list] = {}
    for row in rows:
        raw = json.loads(row["raw_json"]) if row["raw_json"] else {}
        page = raw.get("list_title") or raw.get("linked_from") or "unknown page"
        by_page.setdefault(page, []).append(row)
    lines = [
        "# Links of links",
        "",
        "Everything found inside the reading-list pages you saved:",
        "second-order discoveries, grouped by the page that lists them.",
        "",
    ]
    total = 0
    for page, page_rows in sorted(by_page.items()):
        lines.append(f"## {page}")
        lines.append("")
        for row in page_rows:
            essay = " *(essay)*" if row["is_essay"] else ""
            lines.append(f"- [{row['title']}]({row['url']}){essay}")
            total += 1
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n")
    return total
