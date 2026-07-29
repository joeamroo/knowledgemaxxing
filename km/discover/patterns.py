"""Filename and content patterns that identify source files.

classify_name() works on names alone (fast path, also used for zip members).
sniff_file() inspects contents for ambiguous CSV/JSON/TXT candidates.
"""
from __future__ import annotations

import csv
import fnmatch
import io
import json
import re
from pathlib import Path
from typing import Optional

# Ordered: first match wins. All matching is case-insensitive.
_NAME_RULES: list[tuple[str, str]] = [
    ("twitter-*.zip", "twitter_archive_zip"),
    ("twitter_archive*.zip", "twitter_archive_zip"),
    ("like.js", "twitter_archive"),
    ("like-part*.js", "twitter_archive"),
    ("tweets.js", "twitter_archive"),
    ("tweets-part*.js", "twitter_archive"),
    ("tweet.js", "twitter_archive"),
    ("tweet-part*.js", "twitter_archive"),
    ("account.js", "twitter_archive"),
    ("takeout-*.zip", "takeout_zip"),
    ("browserhistory.json", "takeout_browser"),
    ("myactivity.json", "my_activity"),
    ("myactivity.html", "my_activity_html"),
    ("conversations.json", "chat_export"),
    ("conversations-*.json", "chat_export"),  # ChatGPT split exports
    ("bookmarks_*.html", "bookmarks_html"),
    ("saved_posts.csv", "reddit_gdpr"),
    ("saved_comments.csv", "reddit_gdpr"),
    ("ril_export.html", "pocket"),
    ("pocket*.csv", "pocket_csv"),
    ("instapaper*.csv", "instapaper"),
    ("onetab*.txt", "onetab"),
    # Chrome history exports are a primary source; match liberally
    ("history*.csv", "chrome_export"),
    ("history*.json", "chrome_export"),
    ("chrome*history*.csv", "chrome_export"),
    ("chrome*history*.json", "chrome_export"),
    ("browser*history*.csv", "chrome_export"),
    ("browser*history*.json", "chrome_export"),
    ("*visited*.csv", "chrome_export"),
    # Takeout sometimes names it History.json
    ("history.json", "chrome_export"),
]

_URLISH_COLS = {"url", "href", "link", "uri", "address", "page address"}
_TITLEISH_COLS = {"title", "name", "page", "page title"}
_TIMEISH_HINTS = ("date", "time", "visit", "epoch")

URL_RE = re.compile(r"https?://[^\s\"'<>|)\]]+")


def classify_name(name: str) -> Optional[str]:
    """Map a filename to a source type, or None if not name-identifiable."""
    low = Path(name).name.lower()
    for pattern, source_type in _NAME_RULES:
        if fnmatch.fnmatch(low, pattern):
            return source_type
    return None


def sniff_csv_header(sample: str) -> tuple[Optional[str], Optional[str]]:
    """Inspect a CSV header line.

    Returns (source_type, header) where source_type is chrome_export when
    url-ish columns are present, else None.
    """
    try:
        first_line = sample.splitlines()[0] if sample else ""
        reader = csv.reader(io.StringIO(first_line))
        header = next(reader, [])
    except (csv.Error, StopIteration):
        return None, None
    cols = [c.strip().lower() for c in header]
    if not cols:
        return None, None
    has_url = any(c in _URLISH_COLS for c in cols)
    has_time = any(any(h in c for h in _TIMEISH_HINTS) for c in cols)
    has_title = any(c in _TITLEISH_COLS for c in cols)
    header_str = ",".join(header)
    if has_url and (has_time or has_title):
        return "chrome_export", header_str
    if has_url:
        return "generic", header_str
    return None, header_str


def sniff_file(path: Path, max_bytes: int = 65536) -> Optional[tuple[str, Optional[str]]]:
    """Content-sniff an unclassified CSV/JSON/TXT file.

    Returns (source_type, header_sample) or None if it is not a candidate.
    Reads at most max_bytes, and never touches evicted (dataless) iCloud
    files, since reading those would force a download.
    """
    suffix = path.suffix.lower()
    if suffix not in (".csv", ".json", ".txt"):
        return None
    try:
        st = path.stat()
        if st.st_size > 64 * 1024 * 1024:
            return None  # generic sniffing is not worth reading giant files
        # Evicted iCloud files: reading them forces a network download that
        # can block for minutes. macOS marks them SF_DATALESS (0x40000000).
        if st.st_size > 0 and (
            getattr(st, "st_blocks", 1) == 0
            or getattr(st, "st_flags", 0) & 0x40000000
        ):
            return None
        with open(path, "r", errors="replace") as f:
            sample = f.read(max_bytes)
    except OSError:
        return None
    if not sample.strip():
        return None

    if suffix == ".csv":
        source_type, header = sniff_csv_header(sample)
        return (source_type, header) if source_type else None

    if suffix == ".txt":
        lines = [l for l in sample.splitlines() if l.strip()][:20]
        if lines and sum(1 for l in lines if re.match(r"^https?://\S+ \| ", l)) >= max(
            1, len(lines) // 2
        ):
            return "onetab", None
        return None

    # JSON: high URL density flags it generic; obvious history dumps flag chrome_export
    urls = URL_RE.findall(sample)
    if len(urls) < 5:
        return None
    try:
        head = json.loads(sample) if len(sample) < max_bytes else None
    except json.JSONDecodeError:
        head = None
    if isinstance(head, list) and head and isinstance(head[0], dict):
        keys = {k.lower() for k in head[0]}
        if keys & _URLISH_COLS and any(any(h in k for h in _TIMEISH_HINTS) for k in keys):
            return "chrome_export", ",".join(sorted(head[0].keys()))
    return "generic", None
