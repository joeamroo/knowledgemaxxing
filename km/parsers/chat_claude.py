"""Claude conversations.json parser.

Different schema from ChatGPT: conversations carry chat_messages with
sender and text. The registry probes the JSON shape at runtime, so this
parser can assume the Claude schema but still reads defensively.
"""
from __future__ import annotations

import json
from typing import Iterator

from km.discover.patterns import URL_RE
from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.timeutil import infer_timestamp
from km.urls import canonicalize


def _message_text(msg: dict) -> str:
    text = msg.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    # some exports carry a content list of {type: "text", text: ...}
    content = msg.get("content")
    if isinstance(content, list):
        return "\n".join(
            c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
        ).strip()
    return ""


def parse(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    conversations = json.loads(data)
    for conv in conversations:
        conv_id = conv.get("uuid") or conv.get("id") or ""
        title = conv.get("name") or conv.get("title") or "Untitled conversation"
        created = infer_timestamp(conv.get("created_at"))
        lines, urls = [], []
        for msg in conv.get("chat_messages") or []:
            sender = msg.get("sender") or "unknown"
            text = _message_text(msg)
            if not text:
                continue
            lines.append(f"{sender}: {text}")
            urls.extend(URL_RE.findall(text))
        full_text = "\n\n".join(lines)
        if not full_text:
            continue
        fallback_id = f"{hash(title + full_text[:100]) & 0xFFFFFFFF:x}"
        yield NormalizedItem(
            kind="chat_conversation",
            dedupe_key=f"chat:claude:{conv_id or fallback_id}",
            title=title, text=full_text, created_at=created,
            raw={"provider": "claude", "urls": sorted(set(urls))},
            occurrence_detail=ctx.detail,
        )
        for url in sorted(set(urls)):
            yield NormalizedItem(
                kind="chat_message",
                dedupe_key=f"url:{canonicalize(url)}",
                url=url, title=title, created_at=created,
                raw={"provider": "claude", "conversation": title},
                occurrence_kind="chat_mention",
                occurrence_detail=f"claude: {title}",
            )
