"""Google Takeout Chrome history (BrowserHistory.json).

Objects carry title, url, time_usec (microseconds since Unix epoch), and
page_transition. We keep meaningful transitions and drop redirect noise.
"""
from __future__ import annotations

import json
from typing import Iterator

from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.timeutil import usec_to_dt
from km.urls import canonicalize

_KEEP_TRANSITIONS = {"LINK", "TYPED", "GENERATED"}


def parse(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    doc = json.loads(data)
    records = doc.get("Browser History", doc if isinstance(doc, list) else [])
    for rec in records:
        url = rec.get("url")
        if not url:
            continue
        transition = (rec.get("page_transition") or "").upper()
        if transition and transition not in _KEEP_TRANSITIONS:
            continue
        ts = rec.get("time_usec")
        yield NormalizedItem(
            kind="visit",
            dedupe_key=f"url:{canonicalize(url)}",
            url=url,
            title=rec.get("title") or None,
            created_at=usec_to_dt(ts) if ts else None,
            raw={"page_transition": transition} if transition else {},
            occurrence_detail=ctx.detail,
        )
