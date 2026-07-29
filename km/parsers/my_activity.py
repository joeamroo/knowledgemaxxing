"""Google My Activity parser (Takeout), JSON preferred, HTML fallback.

Entries: {header, title ("Visited ..." / "Searched for ..."), titleUrl,
time}. titleUrl is wrapped in a Google redirect that we unwrap. Gemini
Apps activity ("Prompted ...") becomes chat_message items.
"""
from __future__ import annotations

import json
import re
from typing import Iterator, Optional

from bs4 import BeautifulSoup

from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.timeutil import infer_timestamp
from km.urls import canonicalize, unwrap_google_redirect


def _item_from_activity(
    title: str, title_url: Optional[str], time_str: Optional[str],
    header: str, ctx: ParseContext,
) -> Optional[NormalizedItem]:
    created = infer_timestamp(time_str)
    detail = f"my-activity: {header}" if header else "my-activity"
    if ctx.detail:
        detail = f"{detail}; {ctx.detail}"

    if title.startswith("Searched for "):
        query = title[len("Searched for "):]
        return NormalizedItem(
            kind="search_query",
            dedupe_key=f"search:{query.lower()}",
            title=title, text=query, created_at=created,
            url=unwrap_google_redirect(title_url) if title_url else None,
            occurrence_detail=detail,
        )
    if title.startswith("Prompted ") or header.lower().startswith("gemini"):
        prompt = title[len("Prompted "):] if title.startswith("Prompted ") else title
        return NormalizedItem(
            kind="chat_message",
            dedupe_key=f"gemini:{(time_str or '')}:{hash(prompt) & 0xFFFFFFFF:x}",
            title="Gemini prompt", text=prompt, created_at=created,
            occurrence_detail=detail,
        )
    url = unwrap_google_redirect(title_url) if title_url else None
    if not url:
        return None
    clean_title = title[len("Visited "):] if title.startswith("Visited ") else title
    return NormalizedItem(
        kind="visit",
        dedupe_key=f"url:{canonicalize(url)}",
        url=url, title=clean_title or None, created_at=created,
        occurrence_detail=detail,
    )


def parse(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    records = json.loads(data)
    for rec in records:
        item = _item_from_activity(
            rec.get("title") or "", rec.get("titleUrl"),
            rec.get("time"), rec.get("header") or "", ctx,
        )
        if item:
            yield item


# "Nov 13, 2025, 9:50:57 AM CDT": strptime cannot handle US tz abbreviations,
# so parse naive and apply the offset ourselves
_HTML_DATE_RE = re.compile(
    r"^([A-Z][a-z]{2} \d{1,2}, \d{4}, \d{1,2}:\d{2}:\d{2}\s?[AP]M)\s*([A-Z]{2,4})?$"
)
_TZ_OFFSETS_HOURS = {
    "CDT": -5, "CST": -6, "PDT": -7, "PST": -8, "EDT": -4, "EST": -5,
    "MDT": -6, "MST": -7, "AKDT": -8, "HST": -10, "UTC": 0, "GMT": 0,
}


def parse_activity_date(line: str):
    """Parse a My Activity HTML date line to an aware UTC datetime, or None."""
    from datetime import datetime, timedelta, timezone as tz

    match = _HTML_DATE_RE.match(line.strip().replace(" ", " ").replace(" ", " "))
    if not match:
        return None
    stamp, abbrev = match.groups()
    try:
        naive = datetime.strptime(stamp, "%b %d, %Y, %I:%M:%S %p")
    except ValueError:
        return None
    offset = _TZ_OFFSETS_HOURS.get(abbrev or "UTC", 0)
    return (naive - timedelta(hours=offset)).replace(tzinfo=tz.utc)


def parse_html(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    """BeautifulSoup parser for MyActivity.html.

    Cell layout: outer-cell > header-cell (product) + content-cell whose
    br-separated lines are [action line, ...extras, date line].
    """
    soup = BeautifulSoup(data, "lxml")
    for outer in soup.select("div.outer-cell"):
        header_el = outer.find("p", class_="mdl-typography--title")
        header = header_el.get_text(strip=True) if header_el else ""
        body = outer.find("div", class_="mdl-typography--body-1")
        if body is None:
            continue
        lines = [l.strip() for l in body.get_text("\n").splitlines() if l.strip()]
        if not lines:
            continue
        when = None
        for line in reversed(lines[-2:]):
            when = parse_activity_date(line)
            if when:
                break
        link = body.find("a", href=True)
        first = lines[0]
        if link:
            action = "Searched for" if first.startswith("Searched for") else (
                "Visited" if first.startswith("Visited") else ""
            )
            title = f"{action} {link.get_text(strip=True)}".strip() if action else first[:300]
        else:
            title = first[:300]
        item = _item_from_activity(
            title, link["href"] if link else None, None, header, ctx
        )
        if item:
            if when and item.created_at is None:
                item.created_at = when
            yield item
