"""Life timeline: what each month was about, and what keeps recurring.

monthly_signals() distills every month into its salient traces: top
search terms, notes written, AI conversations started, newly-prominent
reading domains. Offline and heuristic; the AI layer (km themes) labels
these months with narrative themes, and recurring_queries() surfaces the
concerns that keep coming back across months, which is where repeated
mistakes live.
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict

from km.extract.reports import _STOPWORDS

_QUERY_NORMALIZE_RE = re.compile(r"[^a-z0-9 ]+")


def _terms(text: str) -> list[str]:
    return [
        w for w in re.findall(r"[a-zA-Z][a-zA-Z'-]{2,}", text.lower())
        if w not in _STOPWORDS
    ]


def monthly_signals(conn: sqlite3.Connection, min_items: int = 5) -> dict[str, dict]:
    """Per YYYY-MM: the traces that show what the month was about."""
    months: dict[str, dict] = defaultdict(lambda: {
        "search_terms": Counter(), "notes": [], "chats": [],
        "domains": Counter(), "counts": Counter(),
    })
    for row in conn.execute(
        """SELECT substr(created_at, 1, 7) m, kind, title, text, domain FROM items
           WHERE created_at IS NOT NULL AND substr(created_at, 1, 4) >= '2015'"""
    ):
        month = row["m"]
        if not month or len(month) != 7:
            continue
        bucket = months[month]
        bucket["counts"][row["kind"]] += 1
        if row["kind"] == "search_query" and row["text"]:
            bucket["search_terms"].update(_terms(row["text"]))
        elif row["kind"] == "note" and row["title"]:
            bucket["notes"].append(row["title"][:90])
        elif row["kind"] == "chat_conversation" and row["title"]:
            bucket["chats"].append(row["title"][:90])
        elif row["kind"] == "visit" and row["domain"]:
            bucket["domains"][row["domain"]] += 1
    out: dict[str, dict] = {}
    for month in sorted(months):
        bucket = months[month]
        if sum(bucket["counts"].values()) < min_items:
            continue
        out[month] = {
            "total": sum(bucket["counts"].values()),
            "search_terms": bucket["search_terms"].most_common(12),
            "notes": bucket["notes"][:10],
            "chats": bucket["chats"][:10],
            "domains": [
                (d, c) for d, c in bucket["domains"].most_common(8)
                if d not in ("google.com", "www.google.com")
            ],
        }
    return out


def recurring_queries(conn: sqlite3.Connection, min_months: int = 3) -> list[dict]:
    """Concerns that keep coming back: near-identical searches spread
    across min_months+ distinct months. Repeated mistakes hide here."""
    by_query: dict[str, set[str]] = defaultdict(set)
    examples: dict[str, str] = {}
    for row in conn.execute(
        """SELECT substr(created_at, 1, 7) m, text FROM items
           WHERE kind='search_query' AND created_at IS NOT NULL AND text != ''"""
    ):
        normalized = _QUERY_NORMALIZE_RE.sub("", row["text"].lower()).strip()
        normalized = " ".join(w for w in normalized.split() if w not in _STOPWORDS)[:60]
        if len(normalized.split()) < 2:
            continue
        by_query[normalized].add(row["m"])
        examples.setdefault(normalized, row["text"])
    out = [
        {"query": examples[q], "months": sorted(months), "span": len(months)}
        for q, months in by_query.items() if len(months) >= min_months
    ]
    out.sort(key=lambda e: -e["span"])
    return out


def recurring_domains(conn: sqlite3.Connection, min_months: int = 6) -> list[dict]:
    """Sites you kept returning to across many months (excluding utilities)."""
    boring = {
        "google.com", "www.google.com", "accounts.google.com", "docs.google.com",
        "mail.google.com", "youtube.com", "twitter.com", "x.com", "localhost",
    }
    # asset/CDN hosts are plumbing, not places you go
    cdn_re = re.compile(
        r"(^i\.|^cdn|\.cdn\.|ytimg|twimg|pinimg|googleusercontent|gstatic|"
        r"fbcdn|akamai|cloudfront|fastly|redditmedia|redd\.it|imgur)"
    )
    by_domain: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        """SELECT substr(created_at, 1, 7) m, domain FROM items
           WHERE kind='visit' AND created_at IS NOT NULL AND domain != ''"""
    ):
        if row["domain"] not in boring and not cdn_re.search(row["domain"]):
            by_domain[row["domain"]].add(row["m"])
    out = [
        {"domain": d, "months": sorted(months), "span": len(months)}
        for d, months in by_domain.items() if len(months) >= min_months
    ]
    out.sort(key=lambda e: -e["span"])
    return out


_GENERIC_CHAT_TERMS = {
    "conversation", "chat", "code", "error", "errors", "guide", "explained",
    "analysis", "application", "request", "question", "help", "issue",
    "discussion", "review", "overview", "summary", "comparison", "creating",
    "building", "writing", "understanding", "troubleshooting", "fixing",
    "setup", "using", "options", "ideas", "advice", "tips", "improving",
}


def recurring_chat_topics(conn: sqlite3.Connection, min_months: int = 2) -> list[dict]:
    """Terms you kept bringing to AI chats across different months."""
    by_term: dict[str, set[str]] = defaultdict(set)
    for row in conn.execute(
        """SELECT substr(created_at, 1, 7) m, title FROM items
           WHERE kind='chat_conversation' AND created_at IS NOT NULL AND title != ''"""
    ):
        for term in _terms(row["title"]):
            if term not in _GENERIC_CHAT_TERMS:
                by_term[term].add(row["m"])
    out = [
        {"term": t, "months": sorted(months), "span": len(months)}
        for t, months in by_term.items() if len(months) >= min_months
    ]
    out.sort(key=lambda e: -e["span"])
    return out


def export_recurring_threads(conn: sqlite3.Connection, out_path) -> dict:
    """The complete recurrence report, nothing held back or truncated."""
    searches = recurring_queries(conn)
    domains = recurring_domains(conn)
    chats = recurring_chat_topics(conn)
    lines = [
        "# Recurring threads, complete",
        "",
        "Everything that keeps coming back, unfiltered. Searches repeated in",
        "3+ distinct months, sites returned to across 6+ months, and topics",
        "brought to AI chats in multiple months.",
        "",
        f"## Searches that keep coming back ({len(searches)})",
        "",
    ]
    for entry in searches:
        months_str = ", ".join(entry["months"])
        lines.append(f"- \"{entry['query']}\" · {entry['span']} months: {months_str}")
    lines += ["", f"## Sites you keep returning to ({len(domains)})", ""]
    for entry in domains:
        first, last = entry["months"][0], entry["months"][-1]
        lines.append(f"- {entry['domain']} · {entry['span']} months, {first} to {last}")
    lines += ["", f"## Topics you keep bringing to AI ({len(chats)})", ""]
    for entry in chats:
        months_str = ", ".join(entry["months"])
        lines.append(f"- {entry['term']} · {entry['span']} months: {months_str}")
    out_path.write_text("\n".join(lines) + "\n")
    return {"searches": len(searches), "domains": len(domains), "chat_topics": len(chats)}


def export_life_timeline(conn: sqlite3.Connection, out_path) -> int:
    months = monthly_signals(conn)
    recurring = recurring_queries(conn)
    lines = [
        "# Life timeline, month by month",
        "",
        "Reconstructed from searches, notes, AI conversations, and reading.",
        "",
    ]
    current_year = None
    for month, signals in months.items():
        year = month[:4]
        if year != current_year:
            current_year = year
            lines.append(f"# {year}")
            lines.append("")
        lines.append(f"## {month} ({signals['total']:,} traces)")
        lines.append("")
        if signals["search_terms"]:
            terms = ", ".join(t for t, _ in signals["search_terms"])
            lines.append(f"**Searching about:** {terms}")
        if signals["notes"]:
            lines.append(f"**Wrote notes:** {'; '.join(signals['notes'])}")
        if signals["chats"]:
            lines.append(f"**Asked AI about:** {'; '.join(signals['chats'])}")
        if signals["domains"]:
            lines.append(
                "**Reading:** " + ", ".join(f"{d} ({c})" for d, c in signals["domains"])
            )
        lines.append("")
    if recurring:
        lines.append("# Recurring threads (possible repeated loops)")
        lines.append("")
        lines.append("Searches that came back in 3 or more different months:")
        lines.append("")
        for entry in recurring[:60]:
            months_str = ", ".join(entry["months"][:8])
            more = "..." if len(entry["months"]) > 8 else ""
            lines.append(f"- \"{entry['query']}\" ({entry['span']} months: {months_str}{more})")
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n")
    return len(months)


def compact_timeline_for_ai(conn: sqlite3.Connection, max_chars_per_month: int = 420) -> list[dict]:
    """Timeline in evidence-pack form for mentor/talk/themes."""
    months = monthly_signals(conn)
    out = []
    for month, signals in months.items():
        entry = {
            "month": month,
            "searching": [t for t, _ in signals["search_terms"][:8]],
            "notes": signals["notes"][:5],
            "chats": signals["chats"][:5],
            "reading": [d for d, _ in signals["domains"][:5]],
        }
        out.append(entry)
    return out
