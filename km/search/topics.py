"""Topic mapping: cluster a slice of the corpus by embedding.

Search answers "where is X"; this answers "what is here". Groups the
matching items into semantic clusters (spherical k-means over the
stored chunk vectors, all local) and labels each with its distinctive
title words, so "map my AI essays" comes back as named neighborhoods
with exemplar items instead of a 400-row list.
"""
from __future__ import annotations

import re
import sqlite3
import struct
from collections import Counter
from typing import Optional

_LABEL_STOP = frozenset(
    "a an and are as at be but by for from has have how in is it of on or that the "
    "this to what when why with you your not no was were will its using use new one "
    "two guide intro notes essay part why how".split()
)


def _vectors_for(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, list[float]]:
    out = {}
    for cid in chunk_ids:
        row = conn.execute("SELECT embedding FROM vec_items WHERE rowid=?", (cid,)).fetchone()
        if row:
            blob = row["embedding"]
            out[cid] = list(struct.unpack(f"{len(blob) // 4}f", blob))
    return out


def _kmeans(vectors: list[list[float]], n_clusters: int, iters: int = 12) -> list[int]:
    """Spherical k-means (vectors are already L2-normalized). Deterministic
    seeding: evenly spaced initial centroids."""
    import numpy as np

    X = np.asarray(vectors, dtype="float32")
    n = len(X)
    n_clusters = max(1, min(n_clusters, n))
    centroids = X[np.linspace(0, n - 1, n_clusters, dtype=int)].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(iters):
        sims = X @ centroids.T
        new_labels = sims.argmax(axis=1)
        if (new_labels == labels).all():
            break
        labels = new_labels
        for c in range(n_clusters):
            members = X[labels == c]
            if len(members):
                centroid = members.mean(axis=0)
                norm = float((centroid ** 2).sum()) ** 0.5 or 1.0
                centroids[c] = centroid / norm
    return labels.tolist()


def _label(titles: list[str], global_counts: Counter) -> str:
    """Distinctive title words: frequent here, rare elsewhere."""
    local = Counter(
        w for t in titles
        for w in re.findall(r"[a-z0-9']+", (t or "").lower())
        if len(w) > 2 and w not in _LABEL_STOP
    )
    scored = sorted(
        local.items(),
        key=lambda kv: kv[1] / (1 + global_counts.get(kv[0], 0) - kv[1]),
        reverse=True,
    )
    return " / ".join(w for w, _ in scored[:3]) or "misc"


def generate_auto_collections(
    conn: sqlite3.Connection,
    embedder,
    n_clusters: int = 10,
    sample: int = 600,
    min_size: int = 5,
) -> dict:
    """Cluster the corpus and persist each topic as a smart collection.

    Auto collections carry {"auto": true} in their spec and are wholesale
    replaced on regeneration; hand-made collections are never touched.
    Each collection's spec is a semantic query built from the cluster
    label, so clicking one runs a live search rather than freezing a
    snapshot of item ids.
    """
    import json
    from datetime import datetime, timezone

    result = map_topics(conn, embedder, essays_only=False,
                        n_clusters=n_clusters, sample=sample)
    if "error" in result:
        return result

    # thymic negative selection: a cluster that mostly recognizes "self"
    # (an existing collection, or an earlier cluster this run) is culled
    # instead of shown; only genuinely new topics survive
    def tokens(label: str) -> set:
        return {t for t in re.split(r"[^a-z0-9']+", label.lower()) if len(t) > 2}

    existing_names = [
        r["name"] for r in conn.execute("SELECT name FROM smart_collections")
    ]
    self_sets = [tokens(n) for n in existing_names if tokens(n)]
    kept = []
    for cluster in result["clusters"]:
        if cluster["count"] < min_size or cluster["label"] == "misc":
            continue
        toks = tokens(cluster["label"])
        if toks and any(
            len(toks & s) / len(toks | s) >= 0.5 for s in self_sets if s
        ):
            continue
        kept.append(cluster)
        self_sets.append(toks)

    # replace prior autos only
    for row in conn.execute("SELECT id, spec FROM smart_collections").fetchall():
        try:
            if json.loads(row["spec"]).get("auto"):
                conn.execute("DELETE FROM smart_collections WHERE id=?", (row["id"],))
        except (ValueError, TypeError):
            continue

    now = datetime.now(timezone.utc).isoformat()
    created = []
    for cluster in kept:
        name = cluster["label"].replace(" / ", " · ")[:60]
        spec = {
            "query": cluster["label"].replace(" / ", " "),
            "mode": "semantic",
            "filters": {},
            "auto": True,
            "size_at_generation": cluster["count"],
        }
        cur = conn.execute(
            "INSERT INTO smart_collections(name, spec, created_at) VALUES (?,?,?)",
            (name, json.dumps(spec), now))
        created.append({"id": cur.lastrowid, "name": name, "count": cluster["count"]})
    conn.commit()
    return {"created": created, "sampled": result["sampled"]}


def map_topics(
    conn: sqlite3.Connection,
    embedder,
    query: Optional[str] = None,
    essays_only: bool = True,
    n_clusters: int = 6,
    sample: int = 400,
) -> dict:
    """Cluster the corpus slice matching `query` (or the whole essay/item
    space when query is None) into labeled topic groups."""
    from km.db import try_load_sqlite_vec

    if not try_load_sqlite_vec(conn):
        return {"error": "sqlite-vec not installed: uv sync --extra embed"}

    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='embedding_chunks'"
    ).fetchone():
        return {"error": "nothing embedded yet; run km sync first"}

    essay_join = "JOIN items i ON i.id = c.item_id" + (" AND i.is_essay=1" if essays_only else "")
    if query and embedder is not None:
        qvec = embedder.encode_query(query)
        rows = conn.execute(
            f"""SELECT c.id AS chunk_id, c.item_id FROM (
                  SELECT rowid, distance FROM vec_items
                  WHERE embedding MATCH ? AND k = ?
                ) v JOIN embedding_chunks c ON c.id = v.rowid
                {essay_join}
                GROUP BY c.item_id ORDER BY min(v.distance)""",
            (struct.pack(f"{len(qvec)}f", *qvec), sample * 2),
        ).fetchall()[:sample]
    else:
        rows = conn.execute(
            f"""SELECT c.id AS chunk_id, c.item_id FROM embedding_chunks c
                {essay_join}
                WHERE c.chunk_idx = 0
                ORDER BY i.interest_score DESC LIMIT ?""",
            (sample,),
        ).fetchall()
    if len(rows) < 4:
        return {"error": "not enough embedded items for a map; run km sync first"}

    vectors = _vectors_for(conn, [r["chunk_id"] for r in rows])
    ordered = [(r["item_id"], vectors[r["chunk_id"]]) for r in rows if r["chunk_id"] in vectors]
    if len(ordered) < 4:
        return {"error": "vectors missing for the sampled items"}
    labels = _kmeans([v for _, v in ordered], n_clusters)

    items_meta = {}
    for item_id, _ in ordered:
        row = conn.execute(
            "SELECT title, text, url, domain, interest_score FROM items WHERE id=?",
            (item_id,),
        ).fetchone()
        items_meta[item_id] = row

    global_counts = Counter(
        w for m in items_meta.values()
        for w in re.findall(r"[a-z0-9']+", (m["title"] or "").lower())
        if len(w) > 2 and w not in _LABEL_STOP
    )

    clusters: dict[int, list[int]] = {}
    for (item_id, _), label in zip(ordered, labels):
        clusters.setdefault(label, []).append(item_id)

    out = []
    for label_id, item_ids in sorted(clusters.items(), key=lambda kv: -len(kv[1])):
        metas = [items_meta[i] for i in item_ids]
        exemplars = sorted(item_ids, key=lambda i: -(items_meta[i]["interest_score"] or 0))[:8]
        out.append({
            "label": _label([m["title"] or "" for m in metas], global_counts),
            "count": len(item_ids),
            "items": [
                {"id": i,
                 "title": items_meta[i]["title"] or (items_meta[i]["text"] or "")[:80],
                 "url": items_meta[i]["url"], "domain": items_meta[i]["domain"]}
                for i in exemplars
            ],
        })
    return {"sampled": len(ordered), "clusters": out}
