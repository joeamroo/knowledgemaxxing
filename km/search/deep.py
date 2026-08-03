"""Deep semantic retrieval for large corpora.

Single-query top-100 hybrid search is fine for lookups; it is not how
you find an essay by meaning among half a million items. deep_search
does what a patient human would:

1. Several query phrasings (verbatim, content words, clause splits),
   each run through all three retrieval legs with candidate pools of
   hundreds, fused with RRF.
2. A local cross-encoder rereads (query, passage) pairs together and
   rescores the survivors: the precision stage cosine similarity lacks.

All local, no API cost. One tool call for the agent instead of five
round-trips of retried searches.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

_STOPWORDS = frozenset(
    "a an and are as at be but by for from had has have i in is it its me my of on or "
    "our so that the their there this to was we were what when where which who will "
    "with you your about into over under just really very thing things stuff some "
    "read reading remember reading something somewhere essay article post blog piece".split()
)


def query_variants(query: str, max_variants: int = 4) -> list[str]:
    """Cheap local paraphrase surrogates: same meaning, different token mix."""
    variants = [query.strip()]
    content = " ".join(
        w for w in re.findall(r"[A-Za-z0-9']+", query.lower()) if w not in _STOPWORDS
    )
    if content and content != variants[0].lower():
        variants.append(content)
    # split on clause boundaries: each half retrieves independently
    clauses = [c.strip() for c in re.split(r"[,;:]| about | that | where ", query) if len(c.strip()) > 12]
    for clause in clauses:
        if clause.lower() not in {v.lower() for v in variants}:
            variants.append(clause)
    return variants[:max_variants]


def deep_search(
    conn: sqlite3.Connection,
    embedder,
    query: str,
    k: int = 25,
    essays_only: bool = False,
    kind: Optional[str] = None,
    domain: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    pool: int = 400,
    rerank_top: int = 120,
    reranker=None,
    use_reranker: bool = True,
) -> list[dict]:
    """Multi-variant, big-pool, cross-encoder-reranked search.

    Returns [{id, title, passage, url, domain, kind, first_seen,
    relevance}] best-first. reranker=None + use_reranker=True lazily
    loads the local cross-encoder (skipped gracefully if unavailable).
    """
    from km.search.hybrid import rrf_merge, _apply_filters
    from km.search.keyword import Filters, content_keyword_search, keyword_search, parse_query

    base_filters = Filters(
        kind=kind, domain=domain, date_from=date_from, date_to=date_to,
        is_essay=True if essays_only else None,
    )
    query, base_filters = parse_query(query, base_filters)

    ranked_lists: list[list[int]] = []
    passages: dict[int, str] = {}

    vec_ok = False
    if embedder is not None:
        from km.embedding.store import ensure_vec_tables

        vec_ok = ensure_vec_tables(conn, embedder.dims)

    for variant in query_variants(query):
        kw = [i for i, _ in keyword_search(conn, variant, base_filters, pool)]
        if kw:
            ranked_lists.append(kw)
        content_hits = content_keyword_search(conn, variant, base_filters, pool)
        if content_hits:
            ranked_lists.append([i for i, _, _ in content_hits])
            for item_id, _, snip in content_hits:
                passages.setdefault(item_id, snip)
        if vec_ok:
            from km.embedding.store import vector_search

            raw = vector_search(conn, embedder.encode_query(variant), pool)
            allowed = set(_apply_filters(conn, [h["item_id"] for h in raw], base_filters))
            vec_ids = []
            for hit in raw:
                if hit["item_id"] in allowed:
                    vec_ids.append(hit["item_id"])
                    if hit["passage"]:
                        # vector passages win: they are the semantic match
                        passages[hit["item_id"]] = hit["passage"]
            if vec_ids:
                ranked_lists.append(vec_ids)

    if not ranked_lists:
        return []
    fused = rrf_merge(ranked_lists)

    # hydrate the rerank window, collapsing near-dupes (same title+domain
    # saved under different urls reads as noise, not recall)
    candidates = []
    seen_keys: set[tuple] = set()
    for item_id, rrf_score in fused[:rerank_top]:
        row = conn.execute(
            "SELECT id, kind, title, text, url, domain, created_at FROM items WHERE id=?",
            (item_id,),
        ).fetchone()
        if not row:
            continue
        key = ((row["title"] or "").strip().lower(), row["domain"])
        if key[0] and key in seen_keys:
            continue
        seen_keys.add(key)
        passage = passages.get(item_id) or (row["text"] or "")[:600] or (row["title"] or "")
        candidates.append({
            "id": row["id"],
            "title": row["title"] or (row["text"] or "")[:100] or None,
            "passage": passage[:600] or None,
            "url": row["url"],
            "domain": row["domain"],
            "kind": row["kind"],
            "first_seen": (row["created_at"] or "")[:10] or None,
            "relevance": round(rrf_score, 4),
        })

    if use_reranker and reranker is None:
        from km.search.local_rerank import get_reranker

        reranker = get_reranker()
    if reranker is not None and candidates:
        from km.search.local_rerank import rerank_pairs

        texts = [f"{c['title'] or ''}. {c['passage'] or ''}" for c in candidates]
        scores = rerank_pairs(reranker, query, texts)
        for c, s in zip(candidates, scores):
            c["relevance"] = round(s, 3)
        candidates.sort(key=lambda c: c["relevance"], reverse=True)

    return candidates[:k]
