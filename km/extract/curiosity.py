"""Curiosity report: every question you ever typed into a search box.

Question-shaped searches are the purest trace of what you wanted to
understand at a moment in time. This groups them by year, deduplicates
near-identical phrasings, and keeps everything (per the house rule:
complete, unfiltered).
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

_QUESTION_START = re.compile(
    r"^(how|why|what|when|where|who|which|whose|can|could|should|would|will|"
    r"does|do|did|is|are|was|were|has|have|am)\b",
    re.IGNORECASE,
)
_NORMALIZE = re.compile(r"[^a-z0-9 ]+")


def is_question(text: str) -> bool:
    text = text.strip()
    if not text or len(text.split()) < 3:
        return False
    return text.endswith("?") or bool(_QUESTION_START.match(text))


def questions_by_year(conn: sqlite3.Connection) -> dict[str, list[dict]]:
    """Per year: unique questions with how many times each was asked."""
    seen: dict[str, dict] = {}
    for row in conn.execute(
        """SELECT substr(created_at, 1, 4) y, substr(created_at, 1, 7) m, text
           FROM items WHERE kind='search_query' AND created_at IS NOT NULL
           AND text != '' ORDER BY created_at"""
    ):
        if not (row["y"] or "").startswith("20") or not is_question(row["text"]):
            continue
        key = _NORMALIZE.sub("", row["text"].lower()).strip()
        entry = seen.setdefault(key, {
            "question": row["text"].strip(), "first_year": row["y"],
            "count": 0, "months": set(),
        })
        entry["count"] += 1
        entry["months"].add(row["m"])
    years: dict[str, list[dict]] = defaultdict(list)
    for entry in seen.values():
        entry["months"] = sorted(entry["months"])
        years[entry["first_year"]].append(entry)
    for year in years:
        years[year].sort(key=lambda e: (-e["count"], e["question"].lower()))
    return dict(sorted(years.items()))


def export_questions(conn: sqlite3.Connection, out_path) -> int:
    years = questions_by_year(conn)
    total = sum(len(qs) for qs in years.values())
    lines = [
        "# Everything you ever asked a search box",
        "",
        f"{total:,} distinct questions, grouped by the year you first asked.",
        "Repeat counts shown when you asked more than once. Complete list,",
        "nothing filtered.",
        "",
    ]
    for year, questions in years.items():
        lines.append(f"## {year} ({len(questions):,} questions)")
        lines.append("")
        for entry in questions:
            suffix = ""
            if entry["count"] > 1:
                span = f", across {len(entry['months'])} months" if len(entry["months"]) > 1 else ""
                suffix = f" · asked {entry['count']}x{span}"
            lines.append(f"- {entry['question']}{suffix}")
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n")
    return total
