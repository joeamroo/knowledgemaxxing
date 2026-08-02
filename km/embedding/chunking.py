"""Build embeddable content per item.

Tweets embed as single chunks; chat conversations chunk with the title
prefixed so retrieval keeps context; URL items chunk title + source text
+ fetched article body (content table) into passage-sized overlapping
windows. Overlap matters: the sentence you half-remember should never
be lost to a chunk boundary.
"""
from __future__ import annotations

import sqlite3
from typing import Optional

_CHARS_PER_CHUNK = 1000  # ~250 tokens: a passage, not a page
_OVERLAP = 180           # neighboring chunks share an edge


def chunk_text(
    text: str,
    prefix: str = "",
    max_chars: int = _CHARS_PER_CHUNK,
    overlap: int = _OVERLAP,
) -> list[str]:
    if not text:
        return []
    budget = max(200, max_chars - len(prefix))
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        window = text[start:start + budget]
        if start + budget < n:
            # break at the last paragraph or sentence boundary in the window
            cut = max(window.rfind("\n\n"), window.rfind(". "))
            if cut > budget // 2:
                window = window[:cut + 1]
        piece = window.strip()
        if piece:
            chunks.append(f"{prefix}{piece}")
        if start + len(window) >= n:
            break
        # min step guards against pathological short windows looping forever
        start += max(len(window) - overlap, budget // 4)
    return chunks


def content_for_item(row: sqlite3.Row, body: Optional[str] = None) -> list[str]:
    """Return the list of chunks to embed for one item (may be empty).

    body is the fetched article text from the content table, when present.
    """
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
    # URL items (visits, bookmarks, saves, favorites): title, source text,
    # and the full article body when km fetch-content has pulled it
    combined = "\n\n".join(part for part in (title, text, (body or "").strip()) if part)
    if not combined:
        return []
    return chunk_text(combined)
