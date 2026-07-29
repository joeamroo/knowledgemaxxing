"""Reading debt: everything you saved and never actually opened.

An item counts as debt when it entered the archive through a save action
(bookmark, saved post, bookmarked tweet, or a link mined out of a saved
page) and no visit for the same item ever appears in the history. Word
counts, where text exists, give a rough time-to-repay at 220 wpm.
"""
from __future__ import annotations

import sqlite3
from collections import Counter

_SAVE_KINDS = ("bookmark", "saved_post", "bookmark_tweet", "linked_from")


def reading_debt(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        f"""SELECT i.id, i.title, i.text, i.url, i.domain, i.is_essay,
                   min(o.occurred_at) first_saved
            FROM items i JOIN occurrences o ON o.item_id = i.id
            WHERE o.kind IN ({",".join("?" * len(_SAVE_KINDS))})
            AND i.url IS NOT NULL AND i.url != ''
            AND i.id NOT IN (SELECT item_id FROM occurrences WHERE kind='visit')
            GROUP BY i.id ORDER BY first_saved""",
        _SAVE_KINDS,
    ).fetchall()
    items = []
    total_words = 0
    domains: Counter = Counter()
    for row in rows:
        words = len((row["text"] or "").split())
        total_words += words
        domains[row["domain"] or "?"] += 1
        items.append({
            "title": row["title"] or (row["text"] or "").split("\n")[0][:120] or row["url"],
            "url": row["url"],
            "domain": row["domain"],
            "saved": (row["first_saved"] or "")[:10],
            "words": words,
            "is_essay": bool(row["is_essay"]),
        })
    return {
        "items": items,
        "count": len(items),
        "essays": sum(1 for it in items if it["is_essay"]),
        "total_words": total_words,
        "hours_to_repay": round(total_words / 220 / 60, 1),
        "top_domains": domains.most_common(20),
    }


def export_reading_debt(conn: sqlite3.Connection, out_path) -> dict:
    debt = reading_debt(conn)
    lines = [
        "# Reading debt",
        "",
        f"{debt['count']:,} things saved and never opened, {debt['essays']:,} of",
        f"them essays. Where full text is known that is {debt['total_words']:,}",
        f"words, roughly {debt['hours_to_repay']} hours of reading at 220 wpm.",
        "Oldest debt first. Complete list.",
        "",
        "## Where the debt piled up",
        "",
    ]
    for domain, count in debt["top_domains"]:
        lines.append(f"- {domain}: {count}")
    lines += ["", "## The full ledger, oldest first", ""]
    for item in debt["items"]:
        title = " ".join(item["title"].split())
        meta = [item["saved"] or "undated"]
        if item["is_essay"]:
            meta.append("essay")
        if item["words"] > 100:
            meta.append(f"~{max(1, round(item['words'] / 220))} min")
        lines.append(f"- [{title}]({item['url']}) · {' · '.join(meta)}")
    out_path.write_text("\n".join(lines) + "\n")
    return {"count": debt["count"], "hours": debt["hours_to_repay"]}
