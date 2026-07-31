"""Tests for web discovery ranking and feed enrichment plumbing."""
from datetime import datetime, timezone

from km.db import get_db
from km.discover_web import ingest_discoveries, rank_by_similarity
from km.models import NormalizedItem
from km.store import add_source, upsert_item


class FakeEmbedder:
    """Deterministic 3-dim embedder keyed on words."""

    def encode(self, texts):
        out = []
        for t in texts:
            t = t.lower()
            vec = [float("fence" in t), float("emu" in t), float("sourdough" in t)]
            norm = sum(v * v for v in vec) ** 0.5 or 1.0
            out.append([v / norm for v in vec])
        return out


def test_rank_by_similarity_orders_by_topic():
    cands = [
        {"url": "https://a.com/1", "title": "all about emus", "text": "emu emu"},
        {"url": "https://a.com/2", "title": "fences and tradition", "text": "fence wisdom"},
        {"url": "https://a.com/3", "title": "bread", "text": "sourdough starter"},
    ]
    ranked = rank_by_similarity(FakeEmbedder(), "why old fences matter",
                                cands, k=2, fetch_texts=False)
    assert ranked[0]["url"] == "https://a.com/2"
    assert len(ranked) == 2


def test_ingest_discoveries_records_occurrence():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "t", "t", "h")
    target = upsert_item(conn, NormalizedItem(
        kind="bookmark", dedupe_key="url:https://x.com/t", url="https://x.com/t",
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)), sid)
    n = ingest_discoveries(conn, target, [
        {"url": "https://blog.com/essay", "title": "An essay", "why": "same territory"},
    ], "local")
    assert n == 1
    row = conn.execute(
        """SELECT o.kind, o.detail FROM occurrences o JOIN items i ON i.id=o.item_id
           WHERE i.url='https://blog.com/essay'""").fetchone()
    assert row["kind"] == "web_discovery"
    assert f"similar_to:{target}" in row["detail"]


def test_seed_blogs_join_candidates():
    from km.feed import SEED_BLOGS, candidate_domains

    conn = get_db(":memory:")
    domains = candidate_domains(conn)
    assert "gwern.net" in domains and "nabeelqu.co" in domains
    assert len(domains) >= len(SEED_BLOGS) - 5


def test_safari_history_parse(tmp_path):
    import sqlite3 as s

    from km.parsers.safari_history import parse_path

    db = tmp_path / "History.db"
    conn = s.connect(db)
    conn.execute("CREATE TABLE history_items(id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute("""CREATE TABLE history_visits(id INTEGER PRIMARY KEY,
        history_item INTEGER, visit_time REAL, title TEXT, origin INTEGER)""")
    conn.execute("INSERT INTO history_items VALUES (1, 'https://gwern.net/spaced-repetition')")
    # 2025-01-01 12:00 UTC in Cocoa seconds
    conn.execute("INSERT INTO history_visits VALUES (1, 1, 757425600.0, 'Spaced Repetition', 1)")
    conn.commit(); conn.close()
    items = list(parse_path(db))
    assert len(items) == 1
    assert items[0].kind == "visit"
    assert items[0].created_at.year == 2025
    assert "iphone" in items[0].occurrence_detail


def test_custom_category_zero_shot(monkeypatch):
    from km.classify import custom
    from km.embedding.store import ensure_vec_tables, serialize_f32

    conn = get_db(":memory:")
    if not ensure_vec_tables(conn, 3):
        return
    sid, _ = add_source(conn, "t", "t", "h")
    vecs = {"fence post": [1.0, 0, 0], "emu warfare": [0, 1.0, 0]}
    for title, vec in vecs.items():
        item_id = upsert_item(conn, NormalizedItem(
            kind="note", dedupe_key=f"apple-note:{title}", title=title, text=title,
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc)), sid)
        cur = conn.execute(
            "INSERT INTO embedding_chunks(item_id, chunk_idx) VALUES (?, 0)", (item_id,))
        conn.execute("INSERT INTO vec_items(rowid, embedding) VALUES (?,?)",
                     (cur.lastrowid, serialize_f32(vec)))

    cat = custom.create_category(conn, "Fence Lore", "posts about fences and boundaries")

    class FakeEmb:
        dims = 3
        def encode_query(self, text):
            return [1.0, 0, 0]

    import km.embedding.embedder as emb_mod
    monkeypatch.setattr(emb_mod, "get_embedder", lambda cfg: FakeEmb())

    class Cfg:
        pass
    assigned = custom.assign_local(conn, Cfg(), cat["slug"], max_distance=0.5)
    assert assigned == 1
    row = conn.execute("SELECT category, model FROM classifications").fetchone()
    assert row["category"] == "fence_lore" and row["model"] == "local-zero-shot"
