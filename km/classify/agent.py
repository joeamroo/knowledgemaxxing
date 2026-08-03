"""The archivist: an agentic chat over the whole archive.

Unlike the companion personas (which carry a sampled evidence pack), the
archivist works like a research assistant with live tools: it searches,
pulls full items, builds and saves lists, stars and annotates, manages
tasks and the reading feed, and can fetch an article's text on demand.

Cost discipline: every call goes through the spend ledger (tracked_create),
the system prompt is cache_controlled, history is windowed, and tool
results are truncated. When the monthly budget is hit the loop refuses
before spending anything.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Callable, Optional

from km.classify.spend import tracked_create
from km.search.tool_schemas import TOOL_SCHEMAS
from km.search.tools import run_tool

MAX_TOOL_ROUNDS = 12
HISTORY_WINDOW = 24          # prior messages sent per turn
TOOL_RESULT_MAX_CHARS = 24_000

ARCHIVIST_SYSTEM = """You are the archivist for this person's complete digital \
history: every browser visit, bookmark, tweet they saved, AI conversation, note, \
and search, going back years, with the full text of many articles they read. You \
answer by actually looking: use the tools, then ground every claim in what came \
back.

How to work:
- Quick lookups (a title, a name, an exact phrase): search_archive.
- Finding BY MEANING (half-remembered essay or idea, "everything I read \
about X"): go straight to deep_search with a rich description of the meaning; \
it fans out phrasings and reranks locally, so one call beats several retried \
searches. If even deep_search misses, re-describe the MEANING differently \
(the situation, the claim, the feeling) rather than swapping keywords. Quote \
the matching passage back and give the url.
- Orienting in a big corpus ("what do I even have about X"): map_topics first, \
then deep_search into the interesting cluster.
- "Make me a list": list_items with filters (or several searches), then present \
a clean markdown list of titles with urls and dates. Offer to save_collection \
(pins it in their sidebar) or export_list (writes a markdown file) when the \
list seems worth keeping.
- Deep dives: get_item for the full text. If article_body is missing, \
fetch_page pulls it from the web right now.
- Explore: similar_items widens from a good hit to its neighbors.
- Act when asked: star_item, add_note, set_category on items; create_task / \
complete_task / get_tasks for their task list; queue_reading for 'read later'.
- Broad questions about their history: archive_stats first to orient.
- Retrospectives over a period ("why did X keep happening last fall", "what \
did I ask the AIs about Y"): first list_items with kind=chat_conversation and \
the date range, plus a deep_search for the topic, to build the full roster of \
relevant conversations. Then get_chat_messages(role="user") on each to pull \
what they actually asked; batch several of those calls in ONE round. Only \
get_item when you need an assistant answer or article in full (it paginates; \
follow next_offset if chars remain). Synthesize with dates and quote their own \
words back; a plan grounded in what they actually did beats generic advice.
- Cite specifics: title, url, date, which source saw it. Never invent an item, \
a url, or a quote. If the archive genuinely does not have it, say so plainly \
and suggest a different search they could try.
- Be direct and useful. Markdown lists and short paragraphs; no filler, no \
em dashes ever. Tools cost money: be effective, not exhaustive."""


def _tool_result_block(tool_use_id: str, payload) -> dict:
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": payload[:TOOL_RESULT_MAX_CHARS],
    }


def run_agent(
    client,
    model: str,
    conn: sqlite3.Connection,
    embedder,
    messages: list[dict],
    cfg=None,
    on_activity: Optional[Callable[[str], None]] = None,
    max_tokens: int = 2000,
) -> tuple[str, list[str]]:
    """Run one archivist turn: tool-use loop until Claude answers in text.

    messages: prior turns as plain {"role", "content": str} pairs; the tool
    traffic lives only inside this call. Returns (reply_text, tool_trace).
    Raises BudgetExceeded (from spend.tracked_create) when the monthly
    budget is already spent.
    """
    convo: list[dict] = [dict(m) for m in messages[-HISTORY_WINDOW:]]
    trace: list[str] = []
    text = ""

    for _ in range(MAX_TOOL_ROUNDS):
        response = tracked_create(
            conn, cfg, client, "archivist",
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
                payload = run_tool(use.name, conn, cfg, embedder, args)
            except KeyError:
                payload = {"error": f"unknown tool {use.name}"}
            except TypeError as exc:  # bad/unexpected arguments from the model
                payload = {"error": str(exc)}
            except Exception as exc:  # tool bugs surface in-band, loop survives
                payload = {"error": f"{type(exc).__name__}: {exc}"}
            results.append(_tool_result_block(use.id, payload))
        convo.append({"role": "user", "content": results})

    return (
        text.strip()
        or "I ran out of search rounds without a confident answer. "
           "Try narrowing the description or adding a date/site hint.",
        trace,
    )
