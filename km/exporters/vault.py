"""Export the archive into an Obsidian vault: one Markdown note per item,
with YAML frontmatter, tags, and backlinks into category and domain maps of
content (MOCs) so the vault graph actually connects.

This makes km an on-ramp for Obsidian users: the essays you actually read,
with source, date, and provenance, become linked notes instead of a pile of
bookmarks. Nothing here touches the network; it reads the local DB and writes
plain Markdown into a folder you choose inside your vault.

Style rule (shared with markdown.py): no em dashes in generated output.
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from km.exporters.markdown import _category_of, _first_seen, _sources_for

# items worth turning into notes even when they are not flagged as essays:
# the things a reader saves on purpose.
_SAVED_KINDS = (
    "bookmark_tweet", "favorite", "upvote", "note",
)

_INVALID = re.compile(r'[\\/:*?"<>|#^\[\]]+')
_WS = re.compile(r"\s+")


def _slug(text: str, fallback: str) -> str:
    text = _INVALID.sub(" ", text or "")
    text = _WS.sub(" ", text).strip()
    return (text or fallback)[:80].strip()


def _tag(prefix: str, value: str) -> str:
    v = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return f"{prefix}/{v}" if v else ""


def _yaml_escape(text: str) -> str:
    return (text or "").replace('"', "'").replace("\n", " ").strip()


def _select_items(conn: sqlite3.Connection, include_saved: bool, limit: int | None) -> list:
    where = "i.is_essay=1"
    if include_saved:
        placeholders = ",".join("?" for _ in _SAVED_KINDS)
        where = f"(i.is_essay=1 OR i.kind IN ({placeholders}))"
        params: tuple = _SAVED_KINDS
    else:
        params = ()
    sql = (
        f"SELECT DISTINCT i.* FROM items i WHERE {where} "
        "ORDER BY i.interest_score DESC, i.created_at DESC"
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, params).fetchall()


def export_vault(
    conn: sqlite3.Connection,
    vault_dir: Path,
    folder: str = "km",
    include_saved: bool = True,
    limit: int | None = None,
) -> dict:
    """Write one note per item into <vault_dir>/<folder>/ plus MOC index notes.

    Returns a summary dict: notes written, categories, domains.
    """
    root = vault_dir / folder
    notes_dir = root / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)

    rows = _select_items(conn, include_saved, limit)
    used_names: set[str] = set()
    by_category: dict[str, list[str]] = defaultdict(list)
    by_domain: dict[str, list[str]] = defaultdict(list)

    for row in rows:
        item_id = row["id"]
        title = row["title"] or (row["text"] or "").strip().split("\n")[0][:80] or row["canonical_url"] or f"item {item_id}"
        category = _category_of(conn, item_id) or "uncategorized"
        domain = row["domain"] or "unknown"
        seen = _first_seen(conn, item_id) or (row["created_at"] or "")[:10]
        sources = _sources_for(conn, item_id)
        url = row["url"] or row["canonical_url"] or ""
        score = row["interest_score"] or 0.0

        stem = _slug(title, f"item-{item_id}")
        # keep note names unique without leaking ids into every clean title
        if stem.lower() in used_names:
            stem = f"{stem} ({item_id})"
        used_names.add(stem.lower())

        cat_moc = f"km-cat-{_slug(category, category).lower().replace(' ', '-')}"
        dom_moc = f"km-domain-{_slug(domain, domain).lower().replace(' ', '-')}"

        tags = ["km"]
        tags.append("essay" if row["is_essay"] else "saved")
        for t in (_tag("category", category), _tag("domain", domain)):
            if t:
                tags.append(t)
        for s in sources:
            t = _tag("source", s)
            if t:
                tags.append(t)

        fm = ["---"]
        fm.append(f'title: "{_yaml_escape(title)}"')
        if url:
            fm.append(f"url: {url}")
        fm.append(f"category: {category}")
        fm.append(f"domain: {domain}")
        if seen:
            fm.append(f"first_seen: {seen}")
        fm.append(f"interest: {score:.1f}")
        if row["author"]:
            fm.append(f'author: "{_yaml_escape(row["author"])}"')
        fm.append("tags: [" + ", ".join(tags) + "]")
        fm.append("---")

        body = ["", f"# {title}", ""]
        if url:
            body.append(f"[Open original]({url})")
            body.append("")
        provenance = ", ".join(sources) or "unknown"
        body.append(f"Source: {provenance}. First seen: {seen or 'unknown'}.")
        body.append("")
        text = (row["text"] or "").strip()
        if text and text != title:
            body.append("> " + text.replace("\n", "\n> "))
            body.append("")
        body.append(f"Part of [[{cat_moc}]] . [[{dom_moc}]]")
        body.append("")

        (notes_dir / f"{stem}.md").write_text("\n".join(fm + body) + "\n")
        by_category[category].append(stem)
        by_domain[domain].append(stem)

    # per-category MOCs
    for category, stems in by_category.items():
        moc = f"km-cat-{_slug(category, category).lower().replace(' ', '-')}"
        lines = ["---", "tags: [km, moc]", "---", "", f"# {category} ({len(stems)})", ""]
        for stem in sorted(stems, key=str.lower):
            lines.append(f"- [[{stem}]]")
        (root / f"{moc}.md").write_text("\n".join(lines) + "\n")

    # per-domain MOCs (mirror the [[km-domain-...]] backlinks in each note)
    for domain, stems in by_domain.items():
        moc = f"km-domain-{_slug(domain, domain).lower().replace(' ', '-')}"
        lines = ["---", "tags: [km, moc]", "---", "", f"# {domain} ({len(stems)})", ""]
        for stem in sorted(stems, key=str.lower):
            lines.append(f"- [[{stem}]]")
        (root / f"{moc}.md").write_text("\n".join(lines) + "\n")

    # top-level index MOC
    idx = ["---", "tags: [km, moc]", "---", "",
           "# km archive", "",
           f"{len(rows)} notes across {len(by_category)} categories "
           f"and {len(by_domain)} domains, exported from your local km database.", "",
           "## Categories", ""]
    for category in sorted(by_category, key=str.lower):
        moc = f"km-cat-{_slug(category, category).lower().replace(' ', '-')}"
        idx.append(f"- [[{moc}]] ({len(by_category[category])})")
    idx += ["", "## Top domains", ""]
    for domain, stems in sorted(by_domain.items(), key=lambda kv: -len(kv[1]))[:30]:
        moc = f"km-domain-{_slug(domain, domain).lower().replace(' ', '-')}"
        idx.append(f"- [[{moc}]] ({len(stems)})")
    (root / "km-index.md").write_text("\n".join(idx) + "\n")

    return {
        "notes": len(rows),
        "categories": len(by_category),
        "domains": len(by_domain),
        "root": str(root),
    }
