"""Bookmark-family parsers: Chrome JSON, Netscape HTML (also Pocket),
OneTab text, Instapaper/Pocket CSV.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Iterator

from bs4 import BeautifulSoup

from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.timeutil import infer_timestamp, webkit_to_dt
from km.urls import canonicalize


def parse_chrome_json(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    doc = json.loads(data)
    roots = doc.get("roots") or {}

    def walk(node: dict, folder: str) -> Iterator[NormalizedItem]:
        if node.get("type") == "url" and node.get("url", "").startswith(("http://", "https://")):
            # date_added is WebKit microseconds serialized as a string
            created = None
            try:
                created = webkit_to_dt(int(node.get("date_added", 0))) if node.get("date_added") else None
            except (TypeError, ValueError):
                pass
            yield NormalizedItem(
                kind="bookmark",
                dedupe_key=f"url:{canonicalize(node['url'])}",
                url=node["url"], title=node.get("name") or None, created_at=created,
                raw={"folder": folder},
                occurrence_detail=f"chrome bookmarks: {folder}" + (f"; {ctx.detail}" if ctx.detail else ""),
            )
        for child in node.get("children") or []:
            yield from walk(child, f"{folder}/{node.get('name', '')}".strip("/"))

    for root in roots.values():
        if isinstance(root, dict):
            yield from walk(root, root.get("name", ""))


def parse_netscape_html(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    """Netscape bookmark format; also covers Pocket's ril_export.html."""
    soup = BeautifulSoup(data, "lxml")
    for a in soup.find_all("a", href=True):
        url = a["href"]
        if not url.startswith(("http://", "https://")):
            continue
        ts = a.get("add_date") or a.get("time_added") or a.get("last_visit")
        yield NormalizedItem(
            kind="bookmark",
            dedupe_key=f"url:{canonicalize(url)}",
            url=url, title=a.get_text(strip=True) or None,
            created_at=infer_timestamp(ts),
            raw={"tags": a.get("tags")} if a.get("tags") else {},
            occurrence_detail=ctx.detail or ctx.member_name,
        )


def parse_onetab(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or not line.startswith(("http://", "https://")):
            continue
        url, _, title = line.partition(" | ")
        yield NormalizedItem(
            kind="bookmark",
            dedupe_key=f"url:{canonicalize(url)}",
            url=url.strip(), title=title.strip() or None,
            occurrence_detail=f"onetab: {ctx.member_name}",
        )


def parse_saves_csv(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    """Instapaper and Pocket CSV exports (URL, Title, ... Timestamp columns)."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8", errors="replace")))
    for row in reader:
        low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        url = low.get("url") or low.get("given_url") or low.get("resolved_url") or ""
        if not url.startswith(("http://", "https://")):
            continue
        yield NormalizedItem(
            kind="bookmark",
            dedupe_key=f"url:{canonicalize(url)}",
            url=url,
            title=low.get("title") or None,
            created_at=infer_timestamp(low.get("timestamp") or low.get("time_added")),
            raw={"folder": low.get("folder")} if low.get("folder") else {},
            occurrence_detail=ctx.detail or ctx.member_name,
        )
