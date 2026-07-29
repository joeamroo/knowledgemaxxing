"""Heuristic wisdom detection, no API required.

A cheap, high-precision first pass over tweets (likes, RTs, bookmarks)
that finds three things Yousef asked for by name:
- natural_law: eponymous laws, razors, effects, principles
- aphorism: compact standalone maxims
- contrarian: explicit inversion-of-received-wisdom framings

Results are stored as classifications under prompt_version heuristic:v1,
so the Claude pass (tweet_categories:v2) supersedes them when it runs
(category display always prefers the newest classification). Precision
over recall: this pass only tags what it is confident about.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone

PROMPT_VERSION = "wisdom_heuristic:v1"

# Eponymous laws, effects, razors, principles: high precision by name.
_LAW_NAMES = r"""goodhart|chesterton|hanlon|occam|ockham|murphy'?s law|parkinson'?s law|pareto|
gell-?mann|dunning[- ]?kruger|peter principle|lindy|streisand|cunningham|postel|
conway'?s law|brandolini|hofstadter|sturgeon|amara|gresham|campbell'?s law|
hyrum|wirth|zawinski|kerckhoff|shirky|metcalfe|moloch|molochian|baumol|
jevons|braess|simpson'?s paradox|berkson|survivorship bias|regression to the mean|
principal[- ]agent|moral hazard|tragedy of the commons|overton window|
chesterton'?s fence|second[- ]order effects?|revealed preference|goodharting"""
_LAW_RE = re.compile(
    r"\b(" + _LAW_NAMES.replace("\n", "") + r")\b", re.IGNORECASE
)
_LAW_SUFFIX_RE = re.compile(
    r"\b[A-Z][a-z]{3,15}('s)? (law|razor|effect|principle|paradox|fence|rule|fallacy)\b"
)

# Contrarian framings: "everyone says X but", inversion markers.
_CONTRARIAN_RES = [
    re.compile(r"\beveryone (says|thinks|believes|tells you)\b.{0,120}\b(but|actually|wrong)\b", re.I | re.S),
    re.compile(r"\b(unpopular opinion|hot take aside|contrary to popular)\b", re.I),
    re.compile(r"\b(conventional wisdom|common advice|standard advice|they tell you)\b.{0,120}\b(wrong|backwards|opposite|lie|myth)\b", re.I | re.S),
    re.compile(r"\bthe opposite is (true|the case)\b", re.I),
    re.compile(r"\bmost people (think|believe|assume)\b.{0,120}\b(but|in reality|actually)\b", re.I | re.S),
    re.compile(r"\bstop (doing|following|listening to)\b.{0,80}\badvice\b", re.I | re.S),
]

# Aphorisms: short, self-contained, no links/mentions/questions, and
# either a known aphoristic shape or a balanced contrast.
_APHORISM_SHAPES = [
    re.compile(r"^(the|a|an|your|every|all|no|never|always|if|what|he who|she who|those who|people who|you)\b", re.I),
    re.compile(r"\bis (the|a|an|not|just|only|always|never)\b", re.I),
]
_CONTRAST_RE = re.compile(
    r"\b(but|not|never|until|unless|;|:)\b|,", re.I
)
_DISQUALIFY_RE = re.compile(
    r"https?://|@\w|\?|\d{3,}|\bRT\b|\bthread\b|🧵|\bI('m| am| was| have)\b|\bmy\b|\bme\b",
    re.I,
)


def is_natural_law(text: str) -> bool:
    return bool(_LAW_RE.search(text) or _LAW_SUFFIX_RE.search(text))


def is_contrarian(text: str) -> bool:
    return any(r.search(text) for r in _CONTRARIAN_RES)


def is_aphorism(text: str) -> bool:
    stripped = text.strip().strip('"“”')
    if not (30 <= len(stripped) <= 220):
        return False
    if stripped.count("\n") > 1:
        return False
    if _DISQUALIFY_RE.search(stripped):
        return False
    words = stripped.split()
    if not (6 <= len(words) <= 34):
        return False
    shape = any(r.search(stripped) for r in _APHORISM_SHAPES)
    contrast = bool(_CONTRAST_RE.search(stripped))
    return shape and contrast


def run_wisdom_pass(conn: sqlite3.Connection) -> dict[str, int]:
    """Tag confident wisdom categories on tweets lacking an AI classification."""
    now = datetime.now(timezone.utc).isoformat()
    counts = {"natural_law": 0, "contrarian": 0, "aphorism": 0}
    rows = conn.execute(
        """SELECT i.id, i.text FROM items i
           WHERE i.kind IN ('like','retweet','bookmark_tweet','own_tweet')
           AND i.text IS NOT NULL AND length(i.text) > 20
           AND i.id NOT IN (
             SELECT item_id FROM classifications WHERE prompt_version LIKE 'tweet_categories:%'
           )"""
    ).fetchall()
    for row in rows:
        text = row["text"]
        if len(text) > 420:
            continue  # wall-of-text tweets are never crisp wisdom
        category = None
        if is_natural_law(text):
            category = "natural_law"
        elif is_contrarian(text):
            category = "contrarian"
        elif is_aphorism(text):
            category = "aphorism"
        if category:
            conn.execute(
                """INSERT OR REPLACE INTO classifications
                   (item_id, category, subcategories, confidence, model,
                    prompt_version, classified_at)
                   VALUES (?,?,NULL,0.6,'heuristic',?,?)""",
                (row["id"], category, PROMPT_VERSION, now),
            )
            counts[category] += 1
    conn.commit()
    return counts


def export_wisdom(conn: sqlite3.Connection, out_path) -> int:
    """exports/wisdom.md: laws, aphorisms, and contrarian takes compiled."""
    sections = [
        ("Natural laws, razors, and effects", "natural_law"),
        ("Aphorisms", "aphorism"),
        ("Contrarian takes", "contrarian"),
    ]
    lines = [
        "# Wisdom",
        "",
        "Aphorisms, natural laws, and contrarian takes mined from tweets you",
        "liked, retweeted, or bookmarked. Heuristic pass; the Claude pass",
        "refines these categories when it runs.",
        "",
    ]
    total = 0
    for heading, category in sections:
        rows = conn.execute(
            """SELECT i.text, i.url, i.author, i.created_at,
                      coalesce(u.category_override, c.category) AS cat
               FROM items i
               JOIN classifications c ON c.item_id = i.id
               LEFT JOIN user_edits u ON u.item_id = i.id
               WHERE coalesce(u.category_override, c.category) = ?
               AND i.kind IN ('like','retweet','bookmark_tweet','own_tweet')
               ORDER BY i.interest_score DESC, i.created_at DESC""",
            (category,),
        ).fetchall()
        if not rows:
            continue
        lines.append(f"## {heading} ({len(rows)})")
        lines.append("")
        seen_texts: set[str] = set()
        for row in rows:
            text = " ".join((row["text"] or "").split())
            key = text.lower()[:120]
            if key in seen_texts:
                continue
            seen_texts.add(key)
            author = f" (@{row['author']})" if row["author"] else ""
            date = (row["created_at"] or "")[:10]
            if len(text) > 300:
                text = text[:297] + "..."
            lines.append(f"- {text}{author}")
            lines.append(f"  [{date or 'undated'}]({row['url']})")
            total += 1
        lines.append("")
    out_path.write_text("\n".join(lines) + "\n")
    return total
