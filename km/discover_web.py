"""Web discovery: find essays like one you loved, out on the live web.

Two strategies behind one interface:

- "local" (default, no AI): fetch the essay's own page and its site's
  archive pages, harvest outbound links, pull candidate texts, and rank
  them against the essay using local embeddings. The network sees only
  ordinary page fetches; nothing leaves for any AI.

- "ai": Claude with the web_search server tool browses for similar
  essays and explains each pick. Only the essay's title and a short
  excerpt are sent. Needs ANTHROPIC_API_KEY and credits.

Discovered picks are ingested as 'linked' items with an occurrence of
kind 'web_discovery', so they can join the daily reading feed with the
reason "similar to something you loved".
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from km.models import NormalizedItem
from km.store import add_source, upsert_item
from km.urls import canonicalize, domain_of

_UA = {"User-Agent": "km-discovery/1.0 (personal local reading tool)"}
_ARCHIVE_PATHS = ("/archive", "/posts", "/essays", "/articles", "/blog", "/writing", "")
_MIN_ESSAY_CHARS = 1500


def _client():
    import httpx

    return httpx.Client(timeout=10, follow_redirects=True, headers=_UA)


def _page_text(html: str) -> str:
    try:
        import trafilatura

        return trafilatura.extract(html) or ""
    except Exception:
        return ""


def target_representation(conn: sqlite3.Connection, item_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, title, text, url, domain FROM items WHERE id=?", (item_id,)
    ).fetchone()
    if not row:
        return None
    target = {"id": row["id"], "title": row["title"] or "", "url": row["url"],
              "domain": row["domain"], "text": row["text"] or ""}
    if len(target["text"]) < 400 and row["url"]:
        try:
            with _client() as client:
                r = client.get(row["url"])
                if r.status_code == 200:
                    target["text"] = _page_text(r.text)[:6000]
        except Exception:
            pass
    return target


_VARIANT_RE = None


def _is_variant(title: str) -> bool:
    """Translations and mirrors of the same essay are not discoveries."""
    global _VARIANT_RE
    if _VARIANT_RE is None:
        import re

        _VARIANT_RE = re.compile(
            r"translation|翻訳|traduc|übersetz|перевод|ترجمة|apply\.html|comments", re.I)
    return bool(_VARIANT_RE.search(title or ""))


def archive_candidates(conn: sqlite3.Connection, cap: int = 60) -> list[dict]:
    """Unseen essays already in the archive: curated-list links, feed
    posts, and links mined from saves that you never visited. Their
    stored text/titles rank without any fetching."""
    rows = conn.execute(
        """SELECT i.id, i.url, i.title, i.text FROM items i
           WHERE i.kind IN ('linked', 'feed_post') AND i.url IS NOT NULL
           AND i.title IS NOT NULL AND length(i.title) > 8
           AND i.id NOT IN (SELECT item_id FROM occurrences WHERE kind='visit')
           ORDER BY i.interest_score DESC, RANDOM() LIMIT ?""", (cap,)).fetchall()
    return [
        {"url": r["url"], "title": r["title"],
         **({"text": r["text"]} if r["text"] and len(r["text"]) > 200 else {})}
        for r in rows if not _is_variant(r["title"])
    ]


def gather_candidates(conn: sqlite3.Connection, target: dict, cap: int = 40) -> list[dict]:
    """1-hop neighborhood: the essay's outbound links + its site's archive."""
    from km.extract.link_expansion import extract_outbound_links

    known_urls = {
        r["canonical_url"] for r in conn.execute(
            """SELECT i.canonical_url FROM items i
               JOIN occurrences o ON o.item_id=i.id
               WHERE o.kind='visit' AND i.canonical_url IS NOT NULL""")
    }
    candidates: list[dict] = []
    seen: set[str] = {canonicalize(target["url"] or "")}
    pages: list[str] = []
    if target["url"]:
        pages.append(target["url"])
    if target["domain"]:
        pages += [f"https://{target['domain']}{p}" for p in _ARCHIVE_PATHS[:4]]
    with _client() as client:
        for page in pages:
            try:
                r = client.get(page)
                if r.status_code != 200:
                    continue
            except Exception:
                continue
            for link in extract_outbound_links(r.text, page, max_links=60):
                canonical = canonicalize(link["url"])
                if canonical in seen or canonical in known_urls or _is_variant(link["title"]):
                    continue
                seen.add(canonical)
                candidates.append({"url": link["url"], "title": link["title"]})
                if len(candidates) >= cap:
                    return candidates
    return candidates


def rank_by_similarity(embedder, target_text: str, candidates: list[dict],
                       k: int = 8, fetch_texts: bool = True) -> list[dict]:
    """Cosine-rank candidates against the target with local embeddings."""
    if not candidates or not target_text.strip():
        return []
    if fetch_texts:
        kept = []
        with _client() as client:
            for cand in candidates:
                if cand.get("text"):  # archive candidates already carry text
                    kept.append(cand)
                    continue
                try:
                    r = client.get(cand["url"])
                    if r.status_code != 200:
                        continue
                except Exception:
                    continue
                text = _page_text(r.text)
                if len(text) >= _MIN_ESSAY_CHARS:
                    cand["text"] = text[:6000]
                    kept.append(cand)
        candidates = kept
    if not candidates:
        return []
    target_vec = embedder.encode([target_text[:6000]])[0]
    vectors = embedder.encode([c.get("text", c["title"]) for c in candidates])
    scored = []
    for cand, vec in zip(candidates, vectors):
        sim = sum(a * b for a, b in zip(target_vec, vec))  # vectors normalized
        scored.append({**cand, "similarity": round(sim, 4)})
    scored.sort(key=lambda c: -c["similarity"])
    return scored[:k]


def discover_local(conn: sqlite3.Connection, cfg, item_id: int, k: int = 8) -> list[dict]:
    from km.embedding.embedder import get_embedder

    target = target_representation(conn, item_id)
    if not target:
        return []
    candidates = gather_candidates(conn, target) + archive_candidates(conn)

    # essays over forums and news: candidates from the blog canon and
    # curated lists compete first; the rest only fill leftover slots
    from km.feed import SEED_BLOGS

    quality_domains = set(SEED_BLOGS)
    for row in conn.execute(
        """SELECT DISTINCT i.domain FROM items i JOIN occurrences o ON o.item_id=i.id
           WHERE o.detail LIKE 'curated:%' AND i.domain != ''"""):
        quality_domains.add(row["domain"])

    def is_quality(cand):
        d = domain_of(cand["url"])
        return d in quality_domains or d.removeprefix("www.") in quality_domains

    ranked = rank_by_similarity(get_embedder(cfg), target["title"] + "\n" + target["text"],
                                candidates, k=len(candidates) or 1)
    quality = [c for c in ranked if is_quality(c)]
    rest = [c for c in ranked if not is_quality(c)]
    return (quality + rest)[:k]


_AI_PROMPT = """Find {k} essays on the web that someone would love if this one \
mattered to them:

Title: {title}
Excerpt: {excerpt}

Search the web for genuinely similar pieces: same intellectual territory, similar \
depth, ideally from independent writers and blogs rather than news sites. Prefer \
pieces that are freely readable. Reply with JSON only, no prose around it:
[{{"url": "...", "title": "...", "why": "<one line on the connection>"}}]"""


def discover_ai(conn: sqlite3.Connection, cfg, item_id: int, k: int = 6,
                model: Optional[str] = None) -> list[dict]:
    from km.classify.client import get_client, parse_json_response

    target = target_representation(conn, item_id)
    if not target:
        return []
    response = get_client().messages.create(
        model=model or cfg.classification.model,
        max_tokens=1500,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}],
        messages=[{"role": "user", "content": _AI_PROMPT.format(
            k=k, title=target["title"] or target["url"],
            excerpt=(target["text"] or "")[:1200])}],
    )
    text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
    try:
        picks = parse_json_response(text)
    except Exception:
        return []
    out = []
    for p in picks if isinstance(picks, list) else []:
        if isinstance(p, dict) and p.get("url"):
            out.append({"url": p["url"], "title": p.get("title") or p["url"],
                        "why": p.get("why", ""), "similarity": None})
    return out[:k]


def ingest_discoveries(conn: sqlite3.Connection, target_id: int,
                       picks: list[dict], strategy: str) -> int:
    source_id, _ = add_source(conn, "web_discovery", strategy, "discovery")
    count = 0
    for pick in picks:
        canonical = canonicalize(pick["url"])
        item_id = upsert_item(conn, NormalizedItem(
            kind="linked",
            dedupe_key=f"url:{canonical}",
            url=pick["url"], title=pick["title"][:300],
            occurrence_kind="web_discovery",
            occurrence_detail=f"similar_to:{target_id}"
            + (f" · {pick['why'][:120]}" if pick.get("why") else ""),
        ), source_id)
        domain = domain_of(pick["url"])
        conn.execute("UPDATE items SET domain=? WHERE id=? AND (domain IS NULL OR domain='')",
                     (domain, item_id))
        count += 1
    conn.commit()
    return count
