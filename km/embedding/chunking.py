"""Build embeddable content per item.

Tweets embed as single chunks; essays embed title plus extracted text;
chat conversations chunk to roughly 512 tokens with the conversation
title prefixed to each chunk so retrieval keeps context.
"""
from __future__ import annotations

import sqlite3

_CHARS_PER_CHUNK = 2000  # ~512 tokens at ~4 chars/token


def chunk_text(text: str, prefix: str = "", max_chars: int = _CHARS_PER_CHUNK) -> list[str]:
    if not text:
        return []
    budget = max(200, max_chars - len(prefix))
    chunks = []
    remaining = text
    while remaining:
        piece = remaining[:budget]
        if len(remaining) > budget:
            # break at the last paragraph or sentence boundary in the window
            cut = max(piece.rfind("\n\n"), piece.rfind(". "))
            if cut > budget // 2:
                piece = piece[:cut + 1]
        chunks.append(f"{prefix}{piece.strip()}")
        remaining = remaining[len(piece):].lstrip()
    return chunks


def content_for_item(row: sqlite3.Row) -> list[str]:
    """Return the list of chunks to embed for one item (may be empty)."""
    kind = row["kind"]
    title = (row["title"] or "").strip()
    text = (row["text"] or "").strip()

    if kind in ("like", "retweet", "own_tweet", "bookmark_tweet"):
        return [text] if text else []
    if kind == "chat_conversation":
        prefix = f"{title}: " if title else ""
        return chunk_text(text, prefix=prefix)
    if kind == "search_query":
        return [text] if text else []
    # URL items (visits, bookmarks, saves, favorites): title plus any text
    combined = "\n".join(part for part in (title, text) if part)
    if not combined:
        return []
    return chunk_text(combined)
