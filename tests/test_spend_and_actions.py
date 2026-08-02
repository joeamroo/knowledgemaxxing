"""Tests for the AI spend ledger/budget guard and the archivist's action tools."""
from types import SimpleNamespace

import pytest

from km.classify.spend import (
    BudgetExceeded, estimate_usd, month_spend, record_usage, tracked_create,
)
from km.db import get_db
from km.models import NormalizedItem
from km.search import tools
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "a", "h")
    item = upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key="url:a", title="An Essay",
        url="https://blog.example/a", raw={}), sid)
    conn.commit()
    return conn, item


def _usage(inp=1000, out=500, cache_w=0, cache_r=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_creation_input_tokens=cache_w,
                           cache_read_input_tokens=cache_r)


# ── spend ledger ──────────────────────────────────────────

def test_estimate_and_record():
    conn, _ = _db()
    cost = estimate_usd("claude-sonnet-4-6", _usage(1_000_000, 100_000))
    assert cost == pytest.approx(3.0 + 1.5)
    record_usage(conn, "claude-sonnet-4-6", "archivist", _usage())
    assert month_spend(conn) > 0


def test_cache_reads_cost_less_than_fresh_input():
    fresh = estimate_usd("claude-sonnet-4-6", _usage(inp=100_000, out=0))
    cached = estimate_usd("claude-sonnet-4-6", _usage(inp=0, out=0, cache_r=100_000))
    assert cached < fresh * 0.2


def test_budget_guard_blocks_before_spending():
    conn, _ = _db()
    cfg = SimpleNamespace(ai_monthly_budget_usd=0.001)
    record_usage(conn, "claude-sonnet-4-6", "archivist", _usage(10_000, 10_000))

    calls = []
    client = SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: calls.append(kw)))
    with pytest.raises(BudgetExceeded):
        tracked_create(conn, cfg, client, "archivist", model="claude-sonnet-4-6")
    assert calls == []  # refused BEFORE the API call


def test_zero_budget_disables_guard():
    conn, _ = _db()
    cfg = SimpleNamespace(ai_monthly_budget_usd=0)
    response = SimpleNamespace(usage=_usage())
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
    assert tracked_create(conn, cfg, client, "x", model="m") is response


# ── action tools ──────────────────────────────────────────

def test_star_note_category_roundtrip():
    conn, item = _db()
    assert tools.star_item(conn, item)["ok"]
    assert tools.add_note(conn, item, "great read")["ok"]
    assert tools.set_category(conn, item, "tool_or_resource")["ok"]
    row = conn.execute("SELECT * FROM user_edits WHERE item_id=?", (item,)).fetchone()
    assert row["starred"] == 1 and row["note"] == "great read"
    assert row["category_override"] == "tool_or_resource"
    # unstar preserves the note
    tools.star_item(conn, item, starred=False)
    row = conn.execute("SELECT * FROM user_edits WHERE item_id=?", (item,)).fetchone()
    assert row["starred"] == 0 and row["note"] == "great read"
    assert "error" in tools.star_item(conn, 99999)


def test_task_lifecycle():
    conn, _ = _db()
    created = tools.create_task(conn, "Read: An Essay", due="2026-08-10")
    assert created["ok"]
    assert any(t["text"] == "Read: An Essay" for t in tools.get_tasks(conn))
    tools.complete_task(conn, created["task_id"])
    assert not any(t["id"] == created["task_id"] for t in tools.get_tasks(conn, "open"))


def test_queue_reading_and_feed():
    conn, item = _db()
    assert tools.queue_reading(conn, item)["ok"]
    feed = tools.get_reading_feed(conn)
    assert any(f["id"] == item for f in feed)
    assert "error" in tools.queue_reading(conn, 99999)


def test_save_collection_spec_matches_ui_shape():
    import json

    conn, _ = _db()
    out = tools.save_collection(conn, "Fermentation", query="fermentation",
                                is_essay=True, domain="blog.example")
    assert out["ok"]
    row = conn.execute("SELECT * FROM smart_collections WHERE id=?",
                       (out["collection_id"],)).fetchone()
    spec = json.loads(row["spec"])
    assert spec["query"] == "fermentation"
    assert spec["filters"] == {"domain": "blog.example", "is_essay": True}


def test_export_list_writes_markdown(tmp_path):
    from km.config import Config

    conn, item = _db()
    cfg = Config()
    cfg.project_root = tmp_path
    out = tools.export_list(cfg, conn, "My Reading", [item, 99999])
    assert out["ok"] and out["items"] == 1
    content = (tmp_path / "exports" / "lists" / "my-reading.md").read_text()
    assert "[An Essay](https://blog.example/a)" in content
    assert "error" in tools.export_list(cfg, conn, "Empty", [99999])


def test_run_tool_dispatch_and_read_only_set():
    conn, item = _db()
    assert tools.run_tool("archive_stats", conn, None, None, {})["total_items"] == 1
    assert tools.run_tool("star_item", conn, None, None, {"id": item})["ok"]
    with pytest.raises(KeyError):
        tools.run_tool("nope", conn, None, None, {})
    # every write tool is excluded from the MCP read-only set
    for name in ("star_item", "add_note", "set_category", "create_task",
                 "complete_task", "queue_reading", "save_collection",
                 "export_list", "fetch_page"):
        assert name not in tools.READ_TOOLS


def test_mcp_refuses_write_tools(tmp_path):
    from km.config import Config
    from km.mcp_server import TOOLS, KmServer

    assert {t["name"] for t in TOOLS} == tools.READ_TOOLS
    cfg = Config()
    cfg.project_root = tmp_path
    server = KmServer(cfg)
    server._embedder_failed = True
    resp = server.handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "star_item", "arguments": {"id": 1}},
    })
    assert resp["error"]["code"] == -32602
