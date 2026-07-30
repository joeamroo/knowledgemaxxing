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
