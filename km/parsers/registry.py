"""Dispatch manifest source types to parsers.

conversations.json is ambiguous (ChatGPT and Claude both use the name),
so we probe the JSON schema, never the filename.
"""
from __future__ import annotations

import json
import logging
from typing import Callable, Iterator, Optional

from km.models import NormalizedItem
from km.parsers import (
    bookmarks,
    chat_chatgpt,
    chat_claude,
    chrome_export_flex,
    my_activity,
    page_capture,
    reddit_gdpr,
    takeout_browser,
    twitter_archive,
)
from km.parsers.base import ParseContext

log = logging.getLogger(__name__)

ParserFn = Callable[[bytes, ParseContext], Iterator[NormalizedItem]]


def probe_chat_schema(data: bytes) -> Optional[str]:
    """Return 'chatgpt' or 'claude' by inspecting conversations.json shape."""
    try:
        doc = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(doc, list) or not doc:
        return None
    first = doc[0]
    if not isinstance(first, dict):
        return None
    if "mapping" in first:
        return "chatgpt"
    if "chat_messages" in first:
        return "claude"
    return None


def _parse_chat(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    schema = probe_chat_schema(data)
    if schema == "chatgpt":
        yield from chat_chatgpt.parse(data, ctx)
    elif schema == "claude":
        yield from chat_claude.parse(data, ctx)
    else:
        raise ValueError("conversations.json matches neither ChatGPT nor Claude schema")


def parse_grok(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    """Grok export parser skeleton. Format varies; unsupported formats log
    and yield nothing so the pipeline never blocks on Grok."""
    schema = probe_chat_schema(data)
    if schema:  # some Grok exports mirror the ChatGPT/Claude shapes
        yield from _parse_chat(data, ctx)
        return
    log.warning("grok export format unsupported yet: %s", ctx.entry.path)
    return


PARSERS: dict[str, ParserFn] = {
    "twitter_archive": twitter_archive.parse,
    "takeout_browser": takeout_browser.parse,
    "my_activity": my_activity.parse,
    "my_activity_html": my_activity.parse_html,
    "chat_export": _parse_chat,
    "chrome_export": chrome_export_flex.parse,
    "generic": chrome_export_flex.parse,
    "chrome_bookmarks": bookmarks.parse_chrome_json,
    "bookmarks_html": bookmarks.parse_netscape_html,
    "pocket": bookmarks.parse_netscape_html,
    "pocket_csv": bookmarks.parse_saves_csv,
    "instapaper": bookmarks.parse_saves_csv,
    "onetab": bookmarks.parse_onetab,
    "reddit_gdpr": reddit_gdpr.parse,
    "grok_export": parse_grok,
    "page_capture": page_capture.parse,
}
# chrome_live_history is path-based (SQLite): handled specially in ingest.
