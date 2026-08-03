"""Tests for the retrospective research tools: get_chat_messages + paginated get_item."""
from km.db import get_db
from km.models import NormalizedItem
from km.search.tools import get_chat_messages, get_item
from km.store import add_source, upsert_item

TRANSCRIPT = """user: How should I answer the system design question I bombed today?

assistant: Walk me through what happened.

It sounds like you jumped to sharding before load estimates.

user: They asked about rate limiting and I froze. What should I have said?

Also, was mentioning Redis a mistake?

assistant: Redis was fine. The freeze came from not having a framework."""


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "chat_export", "conversations.json", "h")
    chat = upsert_item(conn, NormalizedItem(
        kind="chat_conversation", dedupe_key="chat:chatgpt:abc",
        title="Interview post-mortem", text=TRANSCRIPT,
        raw={"provider": "chatgpt", "urls": []}), sid)
    conn.commit()
    return conn, sid, chat


def test_chat_messages_roles_and_continuations():
    conn, _, chat = _db()
    out = get_chat_messages(conn, chat)
    assert out["total_messages"] == 4
    assert out["provider"] == "chatgpt"
    # continuation paragraphs stay attached to their message
    assert "load estimates" in out["messages"][1]["text"]
    assert "Redis a mistake" in out["messages"][2]["text"]


def test_chat_messages_user_role_filter():
    conn, _, chat = _db()
    out = get_chat_messages(conn, chat, role="user")
    assert out["returned"] == 2
    assert all(m["role"] == "user" for m in out["messages"])
    assert "rate limiting" in out["messages"][1]["text"]


def test_chat_messages_claude_human_role():
    conn, sid, _ = _db()
    claude = upsert_item(conn, NormalizedItem(
        kind="chat_conversation", dedupe_key="chat:claude:x",
        title="Another one", text="human: why did I fail?\n\nassistant: let's look.",
        raw={"provider": "claude", "urls": []}), sid)
    conn.commit()
    out = get_chat_messages(conn, claude, role="user")
    assert out["returned"] == 1
    assert out["messages"][0]["role"] == "user"


def test_chat_messages_wrong_kind_and_missing():
    conn, sid, _ = _db()
    visit = upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key="url:a", title="T", url="https://x.com/a", raw={}), sid)
    conn.commit()
    assert "error" in get_chat_messages(conn, visit)
    assert "error" in get_chat_messages(conn, 99999)


def test_get_item_pagination():
    conn, sid, _ = _db()
    long_item = upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key="url:long", title="Long",
        text="x" * 30_000, url="https://x.com/l", raw={}), sid)
    conn.commit()

    first = get_item(conn, long_item, max_chars=12_000)
    assert len(first["text"]) == 12_000
    assert first["pagination"]["text_chars_remaining"] == 18_000
    second = get_item(conn, long_item, offset=first["pagination"]["next_offset"], max_chars=12_000)
    assert second["pagination"]["text_chars_remaining"] == 6_000
    last = get_item(conn, long_item, offset=24_000, max_chars=12_000)
    assert last["pagination"]["text_chars_remaining"] == 0
    assert last["pagination"]["next_offset"] is None
    # short items stay pagination-free
    short = get_item(conn, long_item, max_chars=50_000)
    assert "pagination" not in short
