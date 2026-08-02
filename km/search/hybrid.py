"""Hybrid retrieval: three legs merged with reciprocal rank fusion.

1. BM25 over item titles/text (items_fts)
2. BM25 over fetched article bodies (content_fts), passage snippets kept
3. Vector cosine over passage chunks, matching chunk text kept

Filters compose with every leg. Results carry the best matching passage
so "find that paragraph I read somewhere" answers with the paragraph.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from km.search.keyword import Filters, content_keyword_search, keyword_search

RRF_K = 60


def rrf_merge(ranked_lists: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: score(id) = sum over lists of 1/(k + rank)."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, item_id in enumerate(ranked, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda pair: pair[1], reverse=True)


def _apply_filters(conn: sqlite3.Connection, item_ids: list[int], filters: Filters) -> list[int]:
    if not item_ids:
        return []
    where_sql, params = filters.sql()
    placeholders = ",".join("?" for _ in item_ids)
    keep = {
        r["id"]
        for r in conn.execute(
            f"SELECT i.id FROM items i WHERE i.id IN ({placeholders}) AND {where_sql}",
            (*item_ids, *params),
        )
    }
    return [i for i in item_ids if i in keep]


def hybrid_search(
    conn: sqlite3.Connection,
    query: str,
    embedder=None,
    filters: Optional[Filters] = None,
    k: int = 20,
    candidate_pool: int = 100,
    passages: Optional[dict] = None,
) -> list[tuple[int, float]]:
    """Return [(item_id, rrf_score)] top-k. Falls back to keyword-only
    when no embedder or no vector table is available.

    Pass a dict as `passages` to receive {item_id: matching passage text}
    for every hit where a specific passage matched (vector chunk or
    article-body snippet). Vector passages win: they are the semantic
    match the query actually landed on.
    """
    filters = filters or Filters()
    keyword_ids = [item_id for item_id, _ in keyword_search(conn, query, filters, candidate_pool)]

    content_hits = content_keyword_search(conn, query, filters, candidate_pool)
    content_ids = [item_id for item_id, _, _ in content_hits]

    vector_ids: list[int] = []
    vector_passages: dict[int, str] = {}
    if embedder is not None:
        from km.embedding.store import ensure_vec_tables, vector_search

        if ensure_vec_tables(conn, embedder.dims):
            raw = vector_search(conn, embedder.encode_query(query), candidate_pool)
            allowed = set(_apply_filters(conn, [h["item_id"] for h in raw], filters))
            for hit in raw:
                if hit["item_id"] in allowed:
                    vector_ids.append(hit["item_id"])
                    if hit["passage"]:
                        vector_passages[hit["item_id"]] = hit["passage"]

    if passages is not None:
        # exact-term snippets read better than a whole semantic chunk, so the
        # content-FTS passage wins when both legs hit the same item
        for item_id, passage in vector_passages.items():
            passages.setdefault(item_id, passage)
        for item_id, _, snip in content_hits:
            if snip:
                passages[item_id] = snip

    merged = rrf_merge([lst for lst in (keyword_ids, content_ids, vector_ids) if lst])
    return merged[:k]


def fetch_results(
    conn: sqlite3.Connection,
    scored: list[tuple[int, float]],
    passages: Optional[dict] = None,
) -> list[dict]:
    out = []
    for item_id, score in scored:
        row = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            continue
        category_row = conn.execute(
            """SELECT coalesce(u.category_override, c.category) cat
               FROM items i
               LEFT JOIN classifications c ON c.item_id=i.id
               LEFT JOIN user_edits u ON u.item_id=i.id
               WHERE i.id=?""",
            (item_id,),
        ).fetchone()
        sources = [
            r["kind"]
            for r in conn.execute(
                """SELECT DISTINCT s.kind FROM occurrences o
                   JOIN sources s ON s.id=o.source_id WHERE o.item_id=?""",
                (item_id,),
            )
        ]
        text = row["text"] or ""
        passage = (passages or {}).get(item_id)
        snippet = passage or (text[:280] + ("..." if len(text) > 280 else ""))
        out.append(
            {
                "id": item_id,
                "score": score,
                "kind": row["kind"],
                "title": row["title"],
                "snippet": snippet[:600],
                "passage": passage,
                "url": row["url"],
                "domain": row["domain"],
                "created_at": row["created_at"],
                "category": category_row["cat"] if category_row else None,
                "sources": sources,
            }
        )
    return out
