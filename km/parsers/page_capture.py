"""Browser page-capture exports (fttf-*.json and friends).

These are history exports that carry the readability-extracted text of
each page, not just its title, so they are the single richest source km
can ingest: article bodies captured at the moment you actually read the
page, including sites that have since gone behind a paywall or died.

Shape: {"document": [[id, title, url, siteName, markdown, hash, ?, domain,
visited_ms, date, extractor, start_ms, end_ms], ...]}. Content fields are
null for pages the extractor skipped (search result pages, apps).

Items merge into existing browser-history visits by canonical URL, so a
title-only visit gets upgraded with real text.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Iterator, Optional

from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.urls import canonicalize

# field positions in the record tuple
_TITLE, _URL, _SITE, _TEXT = 1, 2, 3, 4
_DOMAIN, _VISITED_MS, _EXTRACTOR = 7, 8, 10
_MIN_FIELDS = 9


def _looks_like_capture(doc) -> bool:
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("document"), list)
        and bool(doc["document"])
        and isinstance(doc["document"][0], list)
        and len(doc["document"][0]) >= _MIN_FIELDS
    )


def probe(data: bytes) -> bool:
    """Cheap structural check without decoding the whole file."""
    head = data[:4096].decode("utf-8", errors="replace").lstrip()
    return head.startswith('{"document"') or head.startswith('{ "document"')


def _ts(value) -> Optional[datetime]:
    if isinstance(value, (int, float)) and value > 0:
        seconds = value / 1000.0 if value > 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def parse(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    doc = json.loads(data)
    if not _looks_like_capture(doc):
        return
    for rec in doc["document"]:
        if not isinstance(rec, list) or len(rec) < _MIN_FIELDS:
            continue
        url = rec[_URL]
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        text = rec[_TEXT] if len(rec) > _TEXT and isinstance(rec[_TEXT], str) else None
        extractor = rec[_EXTRACTOR] if len(rec) > _EXTRACTOR else None
        yield NormalizedItem(
            kind="visit",
            dedupe_key=f"url:{canonicalize(url)}",
            url=url,
            title=(rec[_TITLE] or None) if isinstance(rec[_TITLE], str) else None,
            text=text or None,
            created_at=_ts(rec[_VISITED_MS] if len(rec) > _VISITED_MS else None),
            raw={
                "site": rec[_SITE] if isinstance(rec[_SITE], str) else None,
                "domain": rec[_DOMAIN] if len(rec) > _DOMAIN else None,
                "extractor": extractor,
                "captured_text": bool(text),
            },
            occurrence_kind="visit",
            occurrence_detail=ctx.detail,
        )
