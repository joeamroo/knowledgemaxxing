"""Markdown exports, regenerated from the DB, never hand-edited.

Style rule: no em dashes anywhere in generated output, use commas or
parentheses instead.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

_SOURCE_LABELS = {
    "chrome_live_history": "chrome",
    "chrome_export": "chrome-export",
    "takeout_browser": "chrome-takeout",
    "my_activity": "my-activity",
    "my_activity_html": "my-activity",
    "twitter_archive": "twitter",
    "chat_export": "chat",
    "chrome_bookmarks": "bookmark",
    "bookmarks_html": "bookmark",
    "onetab": "bookmark",
    "pocket": "bookmark",
    "pocket_csv": "bookmark",
    "instapaper": "bookmark",
    "reddit_gdpr": "reddit",
    "x_bookmarks": "x-bookmark",
    "reddit_saved": "reddit",
    "substack_saved": "substack",
    "hn": "hn",
}
_OCC_LABELS = {
    "like": "twitter-like",
    "retweet": "twitter-rt",
    "own_tweet": "twitter-own",
    "bookmark_tweet": "x-bookmark",
    "chat_mention": "chat",
}


def _sources_for(conn: sqlite3.Connection, item_id: int) -> list[str]:
    labels = []
    for row in conn.execute(
        """SELECT o.kind AS occ_kind, s.kind AS source_kind
           FROM occurrences o JOIN sources s ON s.id=o.source_id WHERE o.item_id=?""",
        (item_id,),
    ).fetchall():
        label = _OCC_LABELS.get(row["occ_kind"]) or _SOURCE_LABELS.get(
            row["source_kind"], row["source_kind"]
        )
        labels.append(label)
    return sorted(set(labels))


def _first_seen(conn: sqlite3.Connection, item_id: int) -> str:
    row = conn.execute(
        "SELECT min(occurred_at) lo FROM occurrences WHERE item_id=? AND occurred_at IS NOT NULL",
        (item_id,),
    ).fetchone()
    return (row["lo"] or "")[:10]


def _category_of(conn: sqlite3.Connection, item_id: int) -> str | None:
    row = conn.execute(
        """SELECT coalesce(u.category_override, c.category) cat
           FROM items i
           LEFT JOIN classifications c ON c.item_id=i.id
           LEFT JOIN user_edits u ON u.item_id=i.id
           WHERE i.id=? ORDER BY c.classified_at DESC LIMIT 1""",
        (item_id,),
    ).fetchone()
    return row["cat"] if row else None


def export_essays(conn: sqlite3.Connection, out_dir: Path, twitter_only: bool = False) -> Path:
    """essays.md grouped by domain; essays-from-twitter.md for the tweet subset."""
    if twitter_only:
        rows = conn.execute(
            """SELECT DISTINCT i.* FROM items i
               JOIN occurrences o ON o.item_id=i.id
               WHERE i.is_essay=1
               AND o.kind IN ('like','retweet','bookmark_tweet')
               ORDER BY i.domain, i.interest_score DESC"""
        ).fetchall()
        path = out_dir / "essays-from-twitter.md"
        title = "# Essays from Twitter likes, retweets, and bookmarks"
    else:
        rows = conn.execute(
            "SELECT * FROM items WHERE is_essay=1 ORDER BY domain, interest_score DESC"
        ).fetchall()
        path = out_dir / "essays.md"
        title = "# Essays and blog posts"

    by_domain: dict[str, list] = defaultdict(list)
    for row in rows:
        by_domain[row["domain"] or "unknown"].append(row)

    lines = [title, "", f"{len(rows)} essays across {len(by_domain)} domains.", ""]
    for domain in sorted(by_domain):
        lines.append(f"## {domain}")
        lines.append("")
        for row in by_domain[domain]:
            name = row["title"] or row["canonical_url"]
            seen = _first_seen(conn, row["id"])
            sources = ", ".join(_sources_for(conn, row["id"]))
            score = row["interest_score"] or 0
            lines.append(
                f"- [{name}]({row['url'] or row['canonical_url']})"
                f" (first seen {seen or 'unknown'}; sources: {sources}; score {score:.1f})"
            )
        lines.append("")
    path.write_text("\n".join(lines) + "\n")
    return path


def export_tweet_categories(conn: sqlite3.Connection, out_dir: Path) -> list[Path]:
    """tweets/<category>.md for every category with data."""
    tweets_dir = out_dir / "tweets"
    tweets_dir.mkdir(exist_ok=True)
    file_names = {
        "anecdote": "anecdotes.md", "interesting_fact": "facts.md", "thread": "threads.md",
        "joke": "jokes.md", "link_to_essay": "links.md", "quote": "quotes.md",
        "tool_or_resource": "tools.md", "hot_take": "hot-takes.md",
        "contrarian": "contrarian.md", "list": "lists.md", "aphorism": "aphorisms.md",
        "natural_law": "laws.md", "personal": "personal.md", "other": "other.md",
    }
    rows = conn.execute(
        """SELECT i.*, coalesce(u.category_override, c.category) AS category
           FROM items i
           JOIN classifications c ON c.item_id=i.id
           LEFT JOIN user_edits u ON u.item_id=i.id
           WHERE i.kind IN ('like','retweet','bookmark_tweet','own_tweet')
           ORDER BY i.created_at DESC"""
    ).fetchall()
    by_cat: dict[str, list] = defaultdict(list)
    for row in rows:
        by_cat[row["category"] or "other"].append(row)

    written = []
    for category, tweet_rows in by_cat.items():
        path = tweets_dir / file_names.get(category, f"{category}.md")
        lines = [f"# {category.replace('_', ' ').title()} ({len(tweet_rows)} tweets)", ""]
        for row in tweet_rows:
            text = (row["text"] or "").replace("\n", " ").strip()
            author = f"@{row['author']} " if row["author"] else ""
            date = (row["created_at"] or "")[:10]
            lines.append(f"- {author}{text}")
            lines.append(f"  [{date or 'undated'}]({row['url']})")
        path.write_text("\n".join(lines) + "\n")
        written.append(path)
    return written


def export_reading_lists(conn: sqlite3.Connection, out_dir: Path) -> Path:
    from km.extract.reading_lists import aggregate_reading_lists

    entries = aggregate_reading_lists(conn)
    path = out_dir / "reading-lists.md"
    lines = [
        "# Reading lists",
        "",
        f"{len(entries)} reading-list items aggregated from every source.",
        "",
    ]
    tweets = [e for e in entries if e["kind"] in ("like", "retweet", "own_tweet", "bookmark_tweet")]
    pages = [e for e in entries if e not in tweets and e["kind"] != "chat_conversation"]
    chats = [e for e in entries if e["kind"] == "chat_conversation"]

    if pages:
        lines += ["## List pages (blogrolls, best-of pages, digests)", ""]
        for e in pages:
            lines.append(f"- [{e['title']}]({e['url']}) (via {', '.join(e['sources'])})")
        lines.append("")
    if tweets:
        lines += ["## Multi-link tweets", ""]
        for e in tweets:
            lines.append(f"- [{e['title']}]({e['url']}) (via {', '.join(e['sources'])})")
            for link in e["links"]:
                lines.append(f"  - {link}")
        lines.append("")
    if chats:
        lines += ["## Recommendation conversations (AI chats)", ""]
        for e in chats:
            lines.append(f"- {e['title']} (via {', '.join(e['sources'])})")
        lines.append("")
    path.write_text("\n".join(lines) + "\n")
    return path


def export_saved(conn: sqlite3.Connection, out_dir: Path) -> list[Path]:
    saved_dir = out_dir / "saved"
    saved_dir.mkdir(exist_ok=True)
    # select by originating source, not domain: Substack saves usually live on
    # custom domains, and Reddit saves come from both scraper and GDPR export
    specs = [
        ("x-bookmarks.md", "X bookmarks", "i.kind='bookmark_tweet'"),
        ("reddit.md", "Reddit saved",
         "s.kind IN ('reddit_saved','reddit_gdpr')"),
        ("substack.md", "Substack saved", "s.kind='substack_saved'"),
        ("hn.md", "Hacker News favorites and upvotes", "i.kind IN ('favorite','upvote')"),
        ("notes.md", "Apple Notes", "i.kind='note'"),
    ]
    written = []
    for filename, heading, where in specs:
        rows = conn.execute(
            f"""SELECT DISTINCT i.* FROM items i
                JOIN occurrences o ON o.item_id=i.id
                JOIN sources s ON s.id=o.source_id
                WHERE {where} ORDER BY i.created_at DESC"""
        ).fetchall()
        path = saved_dir / filename
        lines = [f"# {heading} ({len(rows)})", ""]
        for row in rows:
            name = row["title"] or (row["text"] or "")[:120] or row["canonical_url"]
            date = (row["created_at"] or "")[:10]
            author = f" (@{row['author']})" if row["author"] else ""
            lines.append(f"- [{name}]({row['url']}){author} {date}")
        path.write_text("\n".join(lines) + "\n")
        written.append(path)
    return written


def export_chats(conn: sqlite3.Connection, out_dir: Path) -> Path:
    path = out_dir / "chats.md"
    rows = conn.execute(
        """SELECT * FROM items WHERE kind='chat_conversation' ORDER BY created_at DESC"""
    ).fetchall()
    lines = ["# Links and recommendations mined from AI chats", ""]
    import json as _json

    for row in rows:
        raw = _json.loads(row["raw_json"]) if row["raw_json"] else {}
        urls = raw.get("urls") or []
        provider = raw.get("provider", "chat")
        date = (row["created_at"] or "")[:10]
        lines.append(f"## {row['title']} ({provider}, {date or 'undated'})")
        lines.append("")
        if urls:
            for url in urls:
                lines.append(f"- {url}")
        else:
            lines.append("(no links in this conversation)")
        lines.append("")
    path.write_text("\n".join(lines) + "\n")
    return path


def export_stats(conn: sqlite3.Connection, out_dir: Path) -> Path:
    from km.store import stats as get_stats

    s = get_stats(conn)
    path = out_dir / "stats.md"
    lines = ["# Knowledge base stats", "", f"Total items: {s['total_items']}", ""]
    lines += ["## Items per source", ""]
    for kind, count in s["by_source_kind"].items():
        coverage = s["date_coverage"].get(kind)
        cov = f" ({coverage[0][:10]} to {coverage[1][:10]})" if coverage and coverage[0] else ""
        lines.append(f"- {kind}: {count}{cov}")
    lines += ["", "## Items per kind", ""]
    for kind, count in s["by_kind"].items():
        lines.append(f"- {kind}: {count}")
    lines += ["", "## Top 30 domains", ""]
    for domain, count in s["top_domains"]:
        lines.append(f"- {domain}: {count}")
    if s["categories"]:
        lines += ["", "## Tweet categories", ""]
        for cat, count in s["categories"].items():
            lines.append(f"- {cat}: {count}")
    path.write_text("\n".join(lines) + "\n")
    return path


def export_all(conn: sqlite3.Connection, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    from km.extract.threads import export_threads

    written = [
        export_essays(conn, out_dir),
        export_essays(conn, out_dir, twitter_only=True),
        export_reading_lists(conn, out_dir),
        export_chats(conn, out_dir),
        export_stats(conn, out_dir),
    ]
    export_threads(conn, out_dir / "threads-reconstructed.md")
    written.append(out_dir / "threads-reconstructed.md")
    written += export_tweet_categories(conn, out_dir)
    written += export_saved(conn, out_dir)
    return written
