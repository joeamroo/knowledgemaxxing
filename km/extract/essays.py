"""Essay/blog detection over all URL items. No AI, pure heuristics.

Buckets: essay, youtube, github_pr, excluded, other. Only `essay` sets
items.is_essay=1; youtube and github_pr are kept as separate buckets in
raw scoring metadata, never discarded.
"""
from __future__ import annotations

import fnmatch
import re
import sqlite3
from typing import Optional

_PATH_SIGNALS = ("/p/", "/posts/", "/blog/", "/essays/", "/writing/", "/essay/", "/post/")
_DATE_PATH_RE = re.compile(r"/20\d{2}/\d{1,2}/")
_GITHUB_PR_RE = re.compile(r"github\.com/[^/]+/[^/]+/(pull|issues)/")

_HARD_EXCLUDE_DOMAINS = {
    "google.com", "mail.google.com", "outlook.com", "outlook.live.com",
    "amazon.com", "smile.amazon.com", "netflix.com", "hulu.com", "disneyplus.com",
    "doordash.com", "ubereats.com", "grubhub.com", "instacart.com",
    "localhost", "127.0.0.1", "chase.com", "bankofamerica.com", "wellsfargo.com",
    "paypal.com", "venmo.com",
}
_SOCIAL_HOME_PATHS = {
    "twitter.com": ("", "/home", "/explore", "/notifications", "/messages"),
    "facebook.com": ("", "/home"),
    "instagram.com": ("",),
    "linkedin.com": ("", "/feed"),
    "reddit.com": ("", "/r/all", "/r/popular"),
    "old.reddit.com": ("",),
    "news.ycombinator.com": ("", "/news", "/newest", "/front"),
}


class EssayRules:
    def __init__(self, domains_cfg: dict):
        self.allow = set(domains_cfg.get("essay_domains") or [])
        self.patterns = list(domains_cfg.get("essay_patterns") or [])
        self.exclude = set(domains_cfg.get("exclude_domains") or []) | _HARD_EXCLUDE_DOMAINS

    def bucket(self, url: str, domain: str, title: Optional[str] = None) -> str:
        if not url or not domain:
            return "other"
        d = domain[4:] if domain.startswith("www.") else domain
        path = url.split(domain, 1)[-1].split("?")[0].rstrip("/") if domain in url else ""

        if d in ("youtube.com", "youtu.be", "m.youtube.com"):
            return "youtube"
        if _GITHUB_PR_RE.search(url):
            return "github_pr"
        if d in self.exclude or d.endswith(".googleapis.com"):
            return "excluded"
        if d == "google.com" or d.endswith(".google.com"):
            return "excluded"
        home_paths = _SOCIAL_HOME_PATHS.get(d)
        if home_paths is not None and path in home_paths:
            return "excluded"
        if d in ("localhost",) or d.startswith(("localhost:", "127.", "0.0.0.0", "192.168.")):
            return "excluded"

        if d in self.allow:
            return "essay"
        for pattern in self.patterns:
            if fnmatch.fnmatch(d, pattern):
                return "essay"
        if any(sig in url for sig in _PATH_SIGNALS) or _DATE_PATH_RE.search(url):
            return "essay"
        return "other"


def mark_essays(conn: sqlite3.Connection, domains_cfg: dict) -> dict[str, int]:
    """Bucket every URL item; set is_essay. Returns bucket counts."""
    rules = EssayRules(domains_cfg)
    counts: dict[str, int] = {}
    rows = conn.execute(
        """SELECT id, canonical_url, domain, title FROM items
           WHERE canonical_url IS NOT NULL AND canonical_url != ''"""
    ).fetchall()
    for row in rows:
        # Tweets are tweets, not essays, even though they live on twitter.com;
        # links inside tweets are separate url: items and bucket normally.
        bucket = rules.bucket(row["canonical_url"], row["domain"] or "", row["title"])
        counts[bucket] = counts.get(bucket, 0) + 1
        conn.execute(
            "UPDATE items SET is_essay=? WHERE id=?",
            (1 if bucket == "essay" else 0, row["id"]),
        )
    conn.commit()
    return counts


def verify_fetch(conn: sqlite3.Connection, cfg, limit: int = 200) -> dict[str, int]:
    """Optional --verify-fetch: fetch essay candidates, confirm article-ness
    with trafilatura (word count > 500), and fill in clean title/author.
    Cached in raw fetch files under data/cache/verify/, rate limited."""
    import hashlib
    import json
    import time

    try:
        import httpx
        import trafilatura
    except ImportError as exc:
        raise RuntimeError(
            "verify-fetch needs the fetch extras: uv sync --extra fetch"
        ) from exc

    cache_dir = cfg.data_dir / "cache" / "verify"
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = conn.execute(
        "SELECT id, canonical_url FROM items WHERE is_essay=1 LIMIT ?", (limit,)
    ).fetchall()
    results = {"confirmed": 0, "rejected": 0, "errors": 0}
    client = httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": "km/0.1"})
    for row in rows:
        key = hashlib.sha256(row["canonical_url"].encode()).hexdigest()
        cache_file = cache_dir / f"{key}.json"
        if cache_file.exists():
            meta = json.loads(cache_file.read_text())
        else:
            try:
                resp = client.get(row["canonical_url"])
                extracted = trafilatura.extract(
                    resp.text, output_format="json", with_metadata=True
                )
                meta = json.loads(extracted) if extracted else {}
            except Exception:
                meta = {"error": True}
            cache_file.write_text(json.dumps(meta))
            time.sleep(0.7)  # be polite
        if meta.get("error"):
            results["errors"] += 1
            continue
        words = len((meta.get("text") or "").split())
        if words > 500:
            results["confirmed"] += 1
            updates = []
            if meta.get("title"):
                updates.append(("title", meta["title"]))
            if meta.get("author"):
                updates.append(("author", meta["author"]))
            for col, val in updates:
                conn.execute(f"UPDATE items SET {col}=? WHERE id=? AND ({col} IS NULL OR {col}='')",
                             (val, row["id"]))
        else:
            results["rejected"] += 1
            conn.execute("UPDATE items SET is_essay=0 WHERE id=?", (row["id"],))
    conn.commit()
    client.close()
    return results
