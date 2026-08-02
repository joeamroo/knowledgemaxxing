"""Tests for passage-level recall: content pipeline, overlap chunking,
chunk-text storage, dims migration, and the passage-carrying search path."""
import hashlib

import pytest

from km.db import get_db, try_load_sqlite_vec
from km.embedding.chunking import chunk_text, content_for_item
from km.models import NormalizedItem
from km.store import add_source, upsert_item

sqlite_vec_available = try_load_sqlite_vec(get_db(":memory:"))
needs_vec = pytest.mark.skipif(not sqlite_vec_available, reason="sqlite-vec not installed")


class FakeEmbedder:
    """Deterministic 8-dim embedder: hash-bucket bag of words."""
    model_name = "fake-8d"
    dims = 8

    def encode(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dims
            for w in t.lower().split():
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dims] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out

    def encode_query(self, text):
        return self.encode([text])[0]


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "chrome_live_history", "history", "h")
    return conn, sid


def _visit(conn, sid, key, title, url):
    item_id = upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key=key, title=title, url=url, raw={}), sid)
    conn.commit()
    return item_id


# ── chunking ──────────────────────────────────────────────

def test_chunk_overlap_and_termination():
    text = ("Sentence number %d about growing tomatoes. " * 200) % tuple(range(200))
    chunks = chunk_text(text, max_chars=500, overlap=100)
    assert len(chunks) > 3
    # overlap: consecutive chunks share text
    assert chunks[0][-40:] not in chunks[2]  # far chunks don't
    joined = " ".join(chunks)
    assert "number 0" in joined and "number 199" in joined  # nothing lost


def test_chunk_empty_and_short():
    assert chunk_text("") == []
    assert chunk_text("short bit") == ["short bit"]


def test_content_for_item_includes_body():
    conn, sid = _db()
    item_id = _visit(conn, sid, "url:a", "Gardening Notes", "https://x.com/a")
    row = conn.execute("SELECT *, NULL AS body FROM items WHERE id=?", (item_id,)).fetchone()
    without = content_for_item(row, body=None)
    with_body = content_for_item(row, body="The secret to tomatoes is patience. " * 50)
    assert len(without) == 1  # title only
    assert len(with_body) > len(without)
    assert any("patience" in c for c in with_body)


# ── content table + FTS ───────────────────────────────────

def test_content_fts_triggers_and_search():
    from km.search.keyword import content_keyword_search

    conn, sid = _db()
    item_id = _visit(conn, sid, "url:a", "Some Essay", "https://x.com/a")
    conn.execute(
        "INSERT INTO content(item_id, text, word_count, fetched_at, ok) VALUES (?,?,?,?,1)",
        (item_id, "A very distinctive phrase: the moss remembers the rain.", 9, "2026-01-01"))
    conn.commit()

    hits = content_keyword_search(conn, "moss remembers")
    assert len(hits) == 1
    hit_id, _, snip = hits[0]
    assert hit_id == item_id
    assert "moss" in snip


# ── embed + passage retrieval ─────────────────────────────

@needs_vec
def test_embed_uses_body_and_search_returns_passage():
    from km.embedding.store import embed_pending
    from km.search.hybrid import fetch_results, hybrid_search

    conn, sid = _db()
    item_id = _visit(conn, sid, "url:a", "Fermentation Guide", "https://x.com/a")
    body = (
        "Wild yeast lives on every grape skin. "
        + "Filler sentence about cellars and oak barrels. " * 60
        + "The lambic brewers of Belgium leave their coolships open to the night air."
    )
    conn.execute(
        "INSERT INTO content(item_id, text, word_count, fetched_at, ok) VALUES (?,?,?,?,1)",
        (item_id, body, len(body.split()), "2026-01-01"))
    conn.commit()

    emb = FakeEmbedder()
    count = embed_pending(conn, emb)
    assert count > 1  # body produced multiple chunks

    passages: dict = {}
    scored = hybrid_search(conn, "lambic coolships night air", emb, passages=passages, k=5)
    assert scored and scored[0][0] == item_id
    assert item_id in passages
    assert "coolships" in passages[item_id]

    results = fetch_results(conn, scored, passages=passages)
    assert "coolships" in results[0]["snippet"]
    assert results[0]["passage"]


@needs_vec
def test_reembed_clears_stale_chunks():
    from km.embedding.store import embed_pending

    conn, sid = _db()
    item_id = _visit(conn, sid, "url:a", "T", "https://x.com/a")
    conn.execute(
        "INSERT INTO content(item_id, text, word_count, fetched_at, ok) VALUES (?,?,?,?,1)",
        (item_id, "long body " * 500, 1000, "2026-01-01"))
    conn.commit()
    emb = FakeEmbedder()
    embed_pending(conn, emb)
    many = conn.execute("SELECT count(*) c FROM embedding_chunks WHERE item_id=?", (item_id,)).fetchone()["c"]
    assert many > 1

    # content shrinks; cache invalidated (as fetch-content does)
    conn.execute("UPDATE content SET text='tiny' WHERE item_id=?", (item_id,))
    conn.execute("DELETE FROM embedding_cache WHERE item_id=?", (item_id,))
    conn.commit()
    embed_pending(conn, emb)
    few = conn.execute("SELECT count(*) c FROM embedding_chunks WHERE item_id=?", (item_id,)).fetchone()["c"]
    assert few == 1  # no orphan high-idx chunks


@needs_vec
def test_dims_mismatch_rebuilds_vec_table():
    from km.embedding.store import embed_pending, ensure_vec_tables

    conn, sid = _db()
    _visit(conn, sid, "url:a", "Some Title", "https://x.com/a")

    class Fake4(FakeEmbedder):
        model_name = "fake-4d"
        dims = 4

        def encode(self, texts):
            return [v[:4] for v in super().encode(texts)]

    embed_pending(conn, Fake4())
    assert ensure_vec_tables(conn, 8)  # switch to 8 dims: rebuild, no crash
    assert conn.execute("SELECT count(*) c FROM embedding_chunks").fetchone()["c"] == 0
    embed_pending(conn, FakeEmbedder())  # re-embeds under the new model
    assert conn.execute("SELECT count(*) c FROM embedding_chunks").fetchone()["c"] > 0


# ── fetch-content candidate selection ─────────────────────

def test_fetch_candidates_selection():
    from km.fetch_content import candidates

    conn, sid = _db()
    essay = _visit(conn, sid, "url:a", "Essay", "https://blog.example/a")
    conn.execute("UPDATE items SET is_essay=1 WHERE id=?", (essay,))
    _visit(conn, sid, "url:b", "Random visit", "https://random.example/b")
    yt = _visit(conn, sid, "url:c", "Video", "https://youtube.com/watch?v=1")
    conn.execute("UPDATE items SET is_essay=1 WHERE id=?", (yt,))
    tweet = upsert_item(conn, NormalizedItem(
        kind="like", dedupe_key="tw:1", text="a tweet", url="https://x.com/s/1", raw={}), sid)
    conn.commit()

    ids = {r["id"] for r in candidates(conn)}
    assert essay in ids            # essays are in
    assert yt not in ids           # skip-domain filtered
    assert tweet not in ids        # self-contained kinds excluded
    assert _visit not in ids
    ids_all = {r["id"] for r in candidates(conn, everything=True)}
    assert essay in ids_all and len(ids_all) > len(ids)

    # already fetched -> excluded
    conn.execute(
        "INSERT INTO content(item_id, text, word_count, fetched_at, ok) VALUES (?,?,?,?,1)",
        (essay, "body", 1, "2026-01-01"))
    conn.commit()
    assert essay not in {r["id"] for r in candidates(conn)}


# ── MCP server ────────────────────────────────────────────

def test_mcp_protocol_and_search(tmp_path):
    from km.config import Config
    from km.mcp_server import KmServer

    cfg = Config()
    cfg.project_root = tmp_path
    server = KmServer(cfg)
    server._embedder_failed = True  # keyword-only for the test

    # seed the server's own DB
    sid, _ = add_source(server.conn, "twitter_archive", "a", "h")
    upsert_item(server.conn, NormalizedItem(
        kind="like", dedupe_key="tw:1",
        text="The best essays are written twice, once to think and once to say",
        url="https://x.com/s/1", raw={}), sid)
    server.conn.commit()

    init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18"}})
    assert init["result"]["serverInfo"]["name"] == "km"
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None

    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    from km.search.tools import READ_TOOLS

    names = {t["name"] for t in tools["result"]["tools"]}
    assert names == READ_TOOLS

    call = server.handle({
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "search_archive", "arguments": {"query": "essays written twice"}},
    })
    assert not call["result"].get("isError")
    assert "written twice" in call["result"]["content"][0]["text"]

    import json as _json
    found = _json.loads(call["result"]["content"][0]["text"])
    item = server.handle({
        "jsonrpc": "2.0", "id": 4, "method": "tools/call",
        "params": {"name": "get_item", "arguments": {"id": found[0]["id"]}},
    })
    body = _json.loads(item["result"]["content"][0]["text"])
    assert body["kind"] == "like" and "occurrences" in body

    unknown = server.handle({"jsonrpc": "2.0", "id": 5, "method": "nope"})
    assert unknown["error"]["code"] == -32601
