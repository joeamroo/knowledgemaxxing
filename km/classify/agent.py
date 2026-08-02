"""The archivist: an agentic chat over the whole archive.

Unlike the companion personas (which carry a sampled evidence pack), the
archivist works like a research assistant with live tools: it runs
hybrid searches, pulls full items, builds filtered link lists, and
checks archive stats, in a tool-use loop, before answering. This is the
"find that passage I read years ago" and "make me a list of every
fermentation link I saved" chat.

Only item text and the conversation go to the API, with the user's own
key. Tools are read-only.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable, Optional

from km.search.tool_schemas import TOOL_SCHEMAS

MAX_TOOL_ROUNDS = 8

ARCHIVIST_SYSTEM = """You are the archivist for this person's complete digital \
history: every browser visit, bookmark, tweet they saved, AI conversation, note, \
and search, going back years, with the full text of many articles they read. You \
answer by actually looking: use the tools, then ground every claim in what came \
back.

How to work:
- Half-remembered passage or idea: search_archive with a natural-language \
description. Try a second phrasing if the first misses; vary the wording, not \
just the keywords. Quote the matching passage back and give the url.
- "Make me a list": list_items with filters (or several searches), then present \
a clean markdown list of titles with urls and dates. Say how many there were in \
total if you truncated.
- Deep dives: get_item for the full text before summarizing or quoting at length.
- Broad questions about their history: archive_stats first to orient.
- Cite specifics: title, url, date, which source saw it. Never invent an item, \
a url, or a quote. If the archive genuinely does not have it, say so plainly \
and suggest a different search they could try.
- Be direct and useful. Markdown lists and short paragraphs; no filler, no \
em dashes ever."""


def _tool_result_block(tool_use_id: str, payload) -> dict:
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": payload[:60_000]}


def run_agent(
    client,
    model: str,
    conn: sqlite3.Connection,
    embedder,
    messages: list[dict],
    on_activity: Optional[Callable[[str], None]] = None,
    max_tokens: int = 2000,
) -> tuple[str, list[str]]:
    """Run one archivist turn: tool-use loop until Claude answers in text.

    messages: prior turns as plain {"role", "content": str} pairs; the tool
    traffic lives only inside this call. Returns (reply_text, tool_trace)
    where tool_trace lists human-readable tool calls made.
    """
    from km.search import tools

    convo: list[dict] = [dict(m) for m in messages]
    trace: list[str] = []

    for _ in range(MAX_TOOL_ROUNDS):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": ARCHIVIST_SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            tools=TOOL_SCHEMAS,
            messages=convo,
        )
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        text = "".join(b.text for b in response.content if b.type == "text")

        if not tool_uses:
            return text.strip(), trace

        # serialize the assistant turn (text + tool_use blocks) verbatim
        convo.append({"role": "assistant", "content": [
            b.model_dump() if hasattr(b, "model_dump") else b for b in response.content
        ]})
        results = []
        for use in tool_uses:
            args = use.input or {}
            label = f"{use.name}({json.dumps(args, ensure_ascii=False)[1:-1][:80]})"
            trace.append(label)
            if on_activity:
                on_activity(label)
            try:
                if use.name == "search_archive":
                    payload = tools.search_archive(conn, embedder, **args)
                elif use.name == "get_item":
                    payload = tools.get_item(conn, **args)
                elif use.name == "list_items":
                    payload = tools.list_items(conn, **args)
                elif use.name == "archive_stats":
                    payload = tools.archive_stats(conn)
                else:
                    payload = {"error": f"unknown tool {use.name}"}
            except TypeError as exc:  # bad/unexpected arguments from the model
                payload = {"error": str(exc)}
            results.append(_tool_result_block(use.id, payload))
        convo.append({"role": "user", "content": results})

    return (
        text.strip()
        or "I ran out of search rounds without a confident answer. "
           "Try narrowing the description or adding a date/site hint.",
        trace,
    )
