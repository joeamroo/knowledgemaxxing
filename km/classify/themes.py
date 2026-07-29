"""km themes: Claude labels each month of the timeline with narrative
themes, then names the repeated mistakes visible across months."""
from __future__ import annotations

import json
import sqlite3

from km.classify.client import call_claude, parse_json_response
from km.extract.timeline import compact_timeline_for_ai, recurring_queries

_THEMES_SYSTEM = """You are a biographer working from a person's digital traces. \
For each month you receive (searches, private note titles, AI conversation topics, \
reading domains), name what that month was actually about in their life: not the \
traces themselves but the life behind them (a job hunt, a breakup aftermath, an \
obsession forming, an exam season, a move, a build phase, a spiral, a recovery). \
Base every theme on the evidence given; when evidence is thin, say so rather than \
inventing. Respond with ONLY a JSON array, one object per month:
{"month": "YYYY-MM", "themes": ["2-4 short themes"], "note": "one grounded sentence"}
No prose, no code fences, no em dashes."""

_MISTAKES_SYSTEM = """You are a sharp, honest pattern analyst. From this person's \
month-by-month timeline and their searches that recur across many months, identify \
their REPEATED MISTAKES and loops: problems they keep re-encountering without \
resolving, promises that reappear without follow-through, cycles (same struggle \
resurfacing every few months), and avoidance patterns. Be concrete and cite the \
months and evidence. Distinguish genuine recurring mistakes from benign recurring \
interests. End with the 3 loops most worth breaking and one first step for each. \
Write in Markdown. No em dashes."""


def run_themes(conn: sqlite3.Connection, client, model: str) -> tuple[list[dict], str]:
    """Returns (per-month themes, mistakes report markdown)."""
    timeline = compact_timeline_for_ai(conn)
    themes: list[dict] = []
    # chunk by year batches to keep each call bounded
    chunk: list[dict] = []
    chunks: list[list[dict]] = []
    for entry in timeline:
        chunk.append(entry)
        if len(chunk) >= 18:
            chunks.append(chunk)
            chunk = []
    if chunk:
        chunks.append(chunk)
    for piece in chunks:
        reply = call_claude(
            client, model, _THEMES_SYSTEM,
            json.dumps(piece, ensure_ascii=False), max_tokens=4000,
        )
        parsed = parse_json_response(reply)
        if isinstance(parsed, list):
            themes.extend(e for e in parsed if isinstance(e, dict) and e.get("month"))

    recurring = recurring_queries(conn)[:50]
    mistakes = call_claude(
        client, model, _MISTAKES_SYSTEM,
        json.dumps({"monthly_themes": themes, "recurring_searches": recurring},
                   ensure_ascii=False),
        max_tokens=4000,
    )
    return themes, mistakes


def export_themes(themes: list[dict], mistakes_md: str, out_path) -> None:
    lines = ["# Life themes, month by month", ""]
    current_year = None
    for entry in sorted(themes, key=lambda e: e["month"]):
        year = entry["month"][:4]
        if year != current_year:
            current_year = year
            lines.append(f"## {year}")
            lines.append("")
        theme_list = ", ".join(entry.get("themes") or [])
        lines.append(f"**{entry['month']}**: {theme_list}")
        if entry.get("note"):
            lines.append(f"  {entry['note']}")
        lines.append("")
    lines.append("# Repeated mistakes and loops")
    lines.append("")
    lines.append(mistakes_md)
    out_path.write_text("\n".join(lines) + "\n")
