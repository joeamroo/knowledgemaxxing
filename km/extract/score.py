"""Interest score: how deliberately did I engage with this item?

Weights (documented in ASSUMPTIONS.md): bookmarks are the highest-intent
save, then likes/saves, then visits. Repeat occurrences of the same kind
add a small diminishing bonus, so visited-many-times still counts but
never outranks a deliberate save.
"""
from __future__ import annotations

import sqlite3

WEIGHTS = {
    "bookmark_tweet": 3.0,
    "bookmark": 2.5,
    "like": 2.0,
    "retweet": 2.0,
    "saved_post": 2.0,
    "saved_comment": 2.0,
    "favorite": 2.0,
    "upvote": 1.5,
    "note": 2.5,
    "chat_mention": 1.5,
    "own_tweet": 1.0,
    "chat_conversation": 1.0,
    "chat_message": 1.0,
    "visit": 1.0,
    "search_query": 0.5,
    "linked_from": 0.8,  # second-order discovery, not yet touched by me
}
_REPEAT_BONUS = 0.2
_REPEAT_CAP = 1.0


def compute_scores(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """SELECT item_id, kind, count(*) AS n
           FROM occurrences GROUP BY item_id, kind"""
    ).fetchall()
    per_item: dict[int, float] = {}
    for row in rows:
        weight = WEIGHTS.get(row["kind"], 1.0)
        bonus = min(_REPEAT_BONUS * (row["n"] - 1), _REPEAT_CAP)
        per_item[row["item_id"]] = per_item.get(row["item_id"], 0.0) + weight + bonus
    conn.executemany(
        "UPDATE items SET interest_score=? WHERE id=?",
        [(score, item_id) for item_id, score in per_item.items()],
    )
    conn.commit()
    return len(per_item)
