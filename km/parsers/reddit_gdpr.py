"""Reddit GDPR export: saved_posts.csv and saved_comments.csv.

Columns are (id, permalink) with occasional extras. These complement the
live scraper, which only sees the most recent ~1000 saves; dedupe keys
match the scraper's so the two merge cleanly.
"""
from __future__ import annotations

import csv
import io
from typing import Iterator

from km.models import NormalizedItem
from km.parsers.base import ParseContext


def parse(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    is_comments = "comment" in ctx.member_name.lower()
    kind = "saved_comment" if is_comments else "saved_post"
    reader = csv.DictReader(io.StringIO(data.decode("utf-8", errors="replace")))
    for row in reader:
        low = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
        rid = low.get("id")
        permalink = low.get("permalink")
        if not rid and not permalink:
            continue
        # normalize t3_/t1_ fullname prefixes so scraper and GDPR keys match
        if rid and "_" in rid:
            rid = rid.split("_", 1)[1]
        if permalink and permalink.startswith("/"):
            permalink = f"https://old.reddit.com{permalink}"
        subreddit = ""
        if permalink and "/r/" in permalink:
            subreddit = permalink.split("/r/", 1)[1].split("/", 1)[0]
        yield NormalizedItem(
            kind=kind,
            dedupe_key=f"reddit:{rid or permalink}",
            url=permalink or None,
            title=None,
            raw={"subreddit": subreddit} if subreddit else {},
            occurrence_detail=f"reddit gdpr: {ctx.member_name}",
        )
