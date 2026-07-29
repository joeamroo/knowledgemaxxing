"""Flexible parser for Chrome history CSV/JSON exports.

Different exporter extensions use different column names and date
formats, so we map columns fuzzily and infer timestamp units by
magnitude. Files whose columns cannot be mapped confidently raise
NeedsMappingError; config.yaml column_mappings can override per path.
"""
from __future__ import annotations

import csv
import io
import json
from typing import Iterator, Optional

from km.models import NormalizedItem
from km.parsers.base import NeedsMappingError, ParseContext
from km.timeutil import infer_timestamp, webkit_to_dt
from km.urls import canonicalize

_URL_COLS = ("url", "href", "link", "uri", "address", "page address")
_TITLE_COLS = ("title", "name", "page", "page title")
_TIME_COLS = (
    "visit_time", "visittime", "lastvisittime", "last_visit_time", "visited on",
    "datetime", "date", "time", "timestamp", "epoch", "visit date",
)


def _pick(columns: list[str], candidates: tuple[str, ...], fuzzy_hints: tuple[str, ...] = ()) -> Optional[str]:
    low = {c.strip().lower(): c for c in columns}
    for cand in candidates:
        if cand in low:
            return low[cand]
    for col_low, col in low.items():
        if any(h in col_low for h in fuzzy_hints):
            return col
    return None


def map_columns(columns: list[str], override: Optional[dict] = None) -> dict:
    """Return {url, title, time} -> actual column names. Raises NeedsMappingError."""
    if override:
        return {
            "url": override.get("url"),
            "title": override.get("title"),
            "time": override.get("time"),
            "time_format": override.get("time_format"),
        }
    url_col = _pick(columns, _URL_COLS, ("url", "href", "link"))
    if not url_col:
        raise NeedsMappingError(f"no url column among: {columns}")
    return {
        "url": url_col,
        "title": _pick(columns, _TITLE_COLS, ("title", "name")),
        "time": _pick(columns, _TIME_COLS, ("date", "time", "visit", "epoch")),
        "time_format": None,
    }


def _parse_time(value, time_format: Optional[str]):
    if value in (None, ""):
        return None
    if time_format == "webkit":
        try:
            return webkit_to_dt(float(value))
        except (TypeError, ValueError):
            return None
    if time_format in ("seconds", "millis"):
        try:
            n = float(value)
            return infer_timestamp(n if time_format == "seconds" else n / 1000)
        except (TypeError, ValueError):
            return None
    return infer_timestamp(value)


def _emit(rows: Iterator[dict], mapping: dict, ctx: ParseContext) -> Iterator[NormalizedItem]:
    url_col, title_col = mapping["url"], mapping.get("title")
    time_col, time_format = mapping.get("time"), mapping.get("time_format")
    for row in rows:
        url = (row.get(url_col) or "").strip() if url_col else ""
        if not url.startswith(("http://", "https://")):
            continue
        extra = {
            k: v for k, v in row.items()
            if k not in (url_col, title_col, time_col) and v not in (None, "")
        }
        yield NormalizedItem(
            kind="visit",
            dedupe_key=f"url:{canonicalize(url)}",
            url=url,
            title=(row.get(title_col) or "").strip() or None if title_col else None,
            created_at=_parse_time(row.get(time_col), time_format) if time_col else None,
            raw=extra,
            occurrence_detail=ctx.detail or ctx.member_name,
        )


def parse(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    override = ctx.config.column_mappings.get(ctx.entry.path)
    name = ctx.member_name.lower()
    text = data.decode("utf-8", errors="replace")
    if name.endswith(".json"):
        doc = json.loads(text)
        if isinstance(doc, dict):
            for key in ("Browser History", "history", "items", "records", "data"):
                if isinstance(doc.get(key), list):
                    doc = doc[key]
                    break
        if not isinstance(doc, list) or not doc or not isinstance(doc[0], dict):
            raise NeedsMappingError("JSON is not a list of objects")
        mapping = map_columns(list(doc[0].keys()), override)
        yield from _emit(iter(doc), mapping, ctx)
    else:
        reader = csv.DictReader(io.StringIO(text))
        columns = reader.fieldnames or []
        mapping = map_columns(list(columns), override)
        yield from _emit(reader, mapping, ctx)
