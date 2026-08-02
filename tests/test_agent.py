"""Tests for the archive tools module and the archivist agent loop."""
from types import SimpleNamespace

from km.classify.agent import run_agent
from km.db import get_db
from km.models import NormalizedItem
from km.search.tools import archive_stats, get_item, list_items, search_archive
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "a", "h")
    upsert_item(conn, NormalizedItem(
        kind="like", dedupe_key="tw:1",
        text="Fermentation is controlled rot and that is the whole trick",
        url="https://x.com/s/1", raw={}), sid)
    essay = upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key="url:a", title="On Slow Cooking",
        url="https://food.example/slow", raw={}), sid)
    conn.execute("UPDATE items SET is_essay=1, domain='food.example', interest_score=3 WHERE id=?", (essay,))
    conn.commit()
    return conn, essay


# ── tools ─────────────────────────────────────────────────

def test_search_archive_keyword_only():
    conn, _ = _db()
    hits = search_archive(conn, None, "fermentation controlled rot")
    assert hits and hits[0]["url"] == "https://x.com/s/1"


def test_list_items_filters_and_sort():
    conn, essay = _db()
    rows = list_items(conn, is_essay=True)
    assert [r["id"] for r in rows] == [essay]
    assert list_items(conn, domain="nowhere.example") == []
    assert list_items(conn, limit=1000)  # cap does not blow up


def test_get_item_includes_body_when_fetched():
    conn, essay = _db()
    assert get_item(conn, essay)["article_body"] is None
    conn.execute(
        "INSERT INTO content(item_id, text, word_count, fetched_at, ok) VALUES (?,?,?,?,1)",
        (essay, "Full article body here.", 4, "2026-01-01"))
    conn.commit()
    assert get_item(conn, essay)["article_body"] == "Full article body here."
    assert "error" in get_item(conn, 99999)


def test_archive_stats_shape():
    conn, _ = _db()
    s = archive_stats(conn)
    assert s["total_items"] == 2
    assert "twitter_archive" in s["by_source"]


# ── agent loop ────────────────────────────────────────────

def _text_block(text):
    return SimpleNamespace(type="text", text=text)


def _tool_block(name, args, use_id="tu_1"):
    block = SimpleNamespace(type="tool_use", name=name, input=args, id=use_id)
    block.model_dump = lambda: {"type": "tool_use", "name": name, "input": args, "id": use_id}
    return block


class ScriptedClient:
    """Returns scripted responses; records the requests it saw."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(content=self._responses.pop(0))


def test_agent_runs_tools_then_answers():
    conn, essay = _db()
    client = ScriptedClient([
        [_tool_block("search_archive", {"query": "slow cooking"})],
        [_text_block("Found it: On Slow Cooking, https://food.example/slow")],
    ])
    reply, trace = run_agent(client, "m", conn, None,
                             [{"role": "user", "content": "that slow cooking thing?"}])
    assert "food.example/slow" in reply
    assert trace and trace[0].startswith("search_archive(")
    # second request carried the tool result back
    second = client.requests[1]["messages"]
    assert second[-1]["role"] == "user"
    assert second[-1]["content"][0]["type"] == "tool_result"
    assert "On Slow Cooking" in second[-1]["content"][0]["content"]


def test_agent_survives_bad_tool_args_and_unknown_tools():
    conn, _ = _db()
    client = ScriptedClient([
        [_tool_block("search_archive", {"query": "x", "bogus_arg": 1}, "tu_1"),
         _tool_block("made_up_tool", {}, "tu_2")],
        [_text_block("done")],
    ])
    reply, trace = run_agent(client, "m", conn, None,
                             [{"role": "user", "content": "hi"}])
    assert reply == "done"
    results = client.requests[1]["messages"][-1]["content"]
    assert "error" in results[0]["content"]  # bad args reported, not raised
    assert "error" in results[1]["content"]


def test_agent_round_cap():
    conn, _ = _db()
    responses = [[_tool_block("archive_stats", {}, f"tu_{i}")] for i in range(8)]
    client = ScriptedClient(responses)
    reply, trace = run_agent(client, "m", conn, None,
                             [{"role": "user", "content": "loop forever"}])
    assert len(trace) == 8
    assert "ran out of search rounds" in reply
