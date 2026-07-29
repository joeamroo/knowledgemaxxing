"""Twitter/X archive parser: like.js, tweets.js, tweet.js, account.js.

Archive files are JavaScript, not JSON: `window.YTD.<name>.part0 = [...]`.
Split archives ship extra files (tweets-part1.js) with partN prefixes.
like.js fullText is truncated to ~140 chars and HTML-escaped.
"""
from __future__ import annotations

import html
import json
import re
from typing import Iterator, Optional

from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.timeutil import infer_timestamp

_YTD_RE = re.compile(r"^\s*window\.YTD\.\w+\.part\d+\s*=\s*", re.DOTALL)
_RT_RE = re.compile(r"^RT @(\w+):\s*")


def strip_ytd_prefix(text: str) -> str:
    """Remove the `window.YTD.<name>.partN = ` assignment prefix."""
    return _YTD_RE.sub("", text.lstrip("﻿"), count=1)


def load_ytd(data: bytes) -> list:
    return json.loads(strip_ytd_prefix(data.decode("utf-8", errors="replace")))


def _tweet_url(tweet_id: str) -> str:
    return f"https://twitter.com/i/web/status/{tweet_id}"


def _parse_likes(records: list, ctx: ParseContext) -> Iterator[NormalizedItem]:
    for rec in records:
        like = rec.get("like", rec)
        tweet_id = like.get("tweetId")
        if not tweet_id:
            continue
        text = html.unescape(like.get("fullText") or "")
        yield NormalizedItem(
            kind="like",
            dedupe_key=f"tweet:{tweet_id}",
            url=like.get("expandedUrl") or _tweet_url(tweet_id),
            text=text or None,
            raw={"tweetId": tweet_id, "truncated": True},
            occurrence_detail=ctx.detail,
        )


def _expanded_urls(tweet: dict) -> list[str]:
    urls = []
    for u in (tweet.get("entities") or {}).get("urls", []):
        expanded = u.get("expanded_url")
        if expanded:
            urls.append(expanded)
    return urls


def _parse_tweets(records: list, ctx: ParseContext) -> Iterator[NormalizedItem]:
    for rec in records:
        tweet = rec.get("tweet", rec)
        tweet_id = tweet.get("id_str") or tweet.get("id")
        if not tweet_id:
            continue
        full_text = html.unescape(tweet.get("full_text") or tweet.get("text") or "")
        rt_match = _RT_RE.match(full_text)
        kind = "retweet" if rt_match else "own_tweet"
        author = rt_match.group(1) if rt_match else None
        raw = {
            "expanded_urls": _expanded_urls(tweet),
            "in_reply_to_status_id": tweet.get("in_reply_to_status_id_str"),
            "in_reply_to_screen_name": tweet.get("in_reply_to_screen_name"),
            "favorite_count": tweet.get("favorite_count"),
            "retweet_count": tweet.get("retweet_count"),
        }
        yield NormalizedItem(
            kind=kind,
            dedupe_key=f"tweet:{tweet_id}",
            url=_tweet_url(str(tweet_id)),
            text=full_text or None,
            author=author,
            created_at=infer_timestamp(tweet.get("created_at")),
            raw=raw,
            occurrence_detail=ctx.detail,
        )


def extract_username(data: bytes) -> Optional[str]:
    """Pull the account username out of account.js (used for context only)."""
    try:
        records = load_ytd(data)
        account = records[0].get("account", {}) if records else {}
        return account.get("username")
    except (json.JSONDecodeError, IndexError, AttributeError):
        return None


def parse(data: bytes, ctx: ParseContext) -> Iterator[NormalizedItem]:
    name = ctx.member_name.lower()
    if name.startswith("account"):
        return  # metadata only, nothing itemizable
    records = load_ytd(data)
    if name.startswith("like"):
        yield from _parse_likes(records, ctx)
    else:  # tweets.js, tweet.js, tweets-part1.js
        yield from _parse_tweets(records, ctx)
