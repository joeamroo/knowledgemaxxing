"""ChatGPT conversations.json parser.

Each conversation has a `mapping` dict of nodes forming a tree; messages
carry author.role and content.parts. We walk root-to-leaf in order,
emit one chat_conversation item per conversation, and one chat_message
item per URL mentioned so links merge with the rest of the base.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator, Optional

from km.discover.patterns import URL_RE
from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.urls import canonicalize


def _walk_mapping(mapping: dict) -> list[dict]:
    """Return messages in tree order (root down, following children)."""
    children_of: dict[Optional[str], list[str]] = {}
    roots = []
    for node_id, node in mapping.items():
        parent = node.get("parent")
        if parent is None:
            roots.append(node_id)
        else:
            children_of.setdefault(parent, []).append(node_id)
    ordered: list[dict] = []
    stack = list(reversed(roots))
    while stack:
        node_id = stack.pop()
        node = mapping.get(node_id) or {}
        msg = node.get("message")
        if msg:
            ordered.append(msg)
        stack.extend(reversed(node.get("children") or children_of.get(node_id, [])))
    return ordered


def _message_text(msg: dict) -> str:
    content = msg.get("content") or {}
    parts = content.get("parts") or []
    return "\n".join(p for p in parts if isinstance(p, str)).strip()


def _ts(value) -> Optional[datetime]:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, tz=timezone.utc)
    return None


def parse(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    conversations = json.loads(data)
    for conv in conversations:
        conv_id = conv.get("conversation_id") or conv.get("id") or ""
        title = conv.get("title") or "Untitled conversation"
        created = _ts(conv.get("create_time"))
        messages = _walk_mapping(conv.get("mapping") or {})
        lines, urls = [], []
        for msg in messages:
            role = ((msg.get("author") or {}).get("role")) or "unknown"
            text = _message_text(msg)
            if not text or role == "system":
                continue
            lines.append(f"{role}: {text}")
            urls.extend(URL_RE.findall(text))
        full_text = "\n\n".join(lines)
        if not full_text:
            continue
        fallback_id = f"{hash(title + full_text[:100]) & 0xFFFFFFFF:x}"
        yield NormalizedItem(
            kind="chat_conversation",
            dedupe_key=f"chat:chatgpt:{conv_id or fallback_id}",
            title=title, text=full_text, created_at=created,
            raw={"provider": "chatgpt", "urls": sorted(set(urls))},
            occurrence_detail=ctx.detail,
        )
        for url in sorted(set(urls)):
            yield NormalizedItem(
                kind="chat_message",
                dedupe_key=f"url:{canonicalize(url)}",
                url=url, title=title, created_at=created,
                raw={"provider": "chatgpt", "conversation": title},
                occurrence_kind="chat_mention",
                occurrence_detail=f"chatgpt: {title}",
            )
