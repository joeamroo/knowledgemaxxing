"""Twitter/X archive parser: the whole archive, not just tweets.

Archive files are JavaScript, not JSON: `window.YTD.<name>.part0 = [...]`.
Split archives ship extra files (tweets-part1.js) with partN prefixes.
like.js fullText is truncated to ~140 chars and HTML-escaped.

Covered: tweets, likes, deleted tweets, long-form note tweets, community
tweets, DMs (1:1 and group), Grok chats, and the social graph (following,
followers, blocks, mutes, lists).

Why relationships become items: every archive is a snapshot, and older
archives hold people you have since unfollowed and tweets you have since
deleted. Storing a relationship as an item with ONE OCCURRENCE PER ARCHIVE
means ingesting every archive dedupes the person to a single row while
keeping the full history of which snapshots saw them. "Followed in 2024,
gone by 2026" is then a query, not a lost fact. Occurrence timestamps are
the archive's own date, taken from its filename.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import datetime, timezone
from typing import Iterator, Optional

from km.models import NormalizedItem
from km.parsers.base import ParseContext
from km.timeutil import infer_timestamp

_YTD_RE = re.compile(r"^\s*window\.YTD\.\w+\.part\d+\s*=\s*", re.DOTALL)
_RT_RE = re.compile(r"^RT @(\w+):\s*")
_ARCHIVE_DATE_RE = re.compile(r"twitter-(\d{4}-\d{2}-\d{2})")


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


def archive_date(ctx: ParseContext) -> Optional[datetime]:
    """The snapshot date of the archive this file came from, from its
    filename (twitter-2026-07-24-<hash>.zip). Used as the occurrence time
    so every relationship records which archives still contained it."""
    match = _ARCHIVE_DATE_RE.search(ctx.entry.path or "")
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    mtime = getattr(ctx.entry, "mtime", None)
    if isinstance(mtime, (int, float)) and mtime > 0:
        return datetime.fromtimestamp(mtime, tz=timezone.utc)
    return None


_RELATIONS = {
    "follower": ("follower", "x_follower", "follows you"),
    "following": ("following", "x_following", "you follow"),
    "block": ("blocking", "x_blocked", "you blocked"),
    "mute": ("muting", "x_muted", "you muted"),
}


def _parse_social(records: list, ctx: ParseContext, name: str) -> Iterator[NormalizedItem]:
    """follower.js / following.js / block.js / mute.js: one item per account,
    stamped with this archive's date so disappearances are detectable."""
    key = next(k for k in _RELATIONS if name.startswith(k))
    field, kind, label = _RELATIONS[key]
    stamp = archive_date(ctx)
    for rec in records:
        node = rec.get(field, rec)
        account_id = node.get("accountId")
        if not account_id:
            continue
        yield NormalizedItem(
            kind=kind,
            dedupe_key=f"x-account:{account_id}:{key}",
            url=node.get("userLink") or f"https://twitter.com/intent/user?user_id={account_id}",
            title=f"X account {account_id} ({label})",
            created_at=stamp,
            raw={"accountId": account_id, "relation": key},
            occurrence_kind=kind,
            occurrence_detail=ctx.detail,
        )


def _parse_deleted_tweets(records: list, ctx: ParseContext) -> Iterator[NormalizedItem]:
    """Tweets you deleted. They exist ONLY in archives taken before the
    deletion, which is exactly why every archive gets ingested."""
    stamp = archive_date(ctx)
    for rec in records:
        tweet = rec.get("tweet", rec)
        tweet_id = tweet.get("id_str") or tweet.get("id")
        if not tweet_id:
            continue
        full_text = html.unescape(tweet.get("full_text") or tweet.get("text") or "")
        yield NormalizedItem(
            kind="own_tweet",
            dedupe_key=f"tweet:{tweet_id}",
            url=_tweet_url(str(tweet_id)),
            text=full_text or None,
            created_at=infer_timestamp(tweet.get("created_at")),
            raw={"deleted": True, "expanded_urls": _expanded_urls(tweet)},
            # the occurrence, not the item, carries "this snapshot saw it deleted"
            occurrence_kind="deleted_tweet",
            occurrence_detail=ctx.detail,
        )


def _parse_note_tweets(records: list, ctx: ParseContext) -> Iterator[NormalizedItem]:
    """Long-form posts. The full text lives here and nowhere else."""
    for rec in records:
        note = rec.get("noteTweet", rec)
        note_id = note.get("noteTweetId")
        core = note.get("core") or {}
        text = html.unescape(core.get("text") or "")
        if not note_id or not text:
            continue
        yield NormalizedItem(
            kind="own_tweet",
            dedupe_key=f"note:{note_id}",
            text=text,
            created_at=infer_timestamp(note.get("createdAt")),
            raw={
                "long_form": True,
                "expanded_urls": [u.get("expandedUrl") for u in core.get("urls") or []
                                  if u.get("expandedUrl")],
            },
            occurrence_kind="own_tweet",
            occurrence_detail=ctx.detail,
        )


def _dm_id(message: dict, conversation_id: str) -> str:
    mid = message.get("id")
    if mid:
        return str(mid)
    seed = f"{conversation_id}|{message.get('createdAt')}|{(message.get('text') or '')[:120]}"
    return hashlib.sha256(seed.encode()).hexdigest()[:20]


def _parse_dms(records: list, ctx: ParseContext, group: bool = False) -> Iterator[NormalizedItem]:
    """Direct messages, 1:1 and group. One item per message, deduped by id
    so overlapping archives merge instead of duplicating."""
    for rec in records:
        conv = rec.get("dmConversation", rec)
        conversation_id = conv.get("conversationId") or ""
        for wrapper in conv.get("messages") or []:
            message = wrapper.get("messageCreate") or wrapper.get("welcomeMessageCreate")
            if not message:
                continue  # joins, leaves, reactions: no text to keep
            text = html.unescape(message.get("text") or "")
            if not text:
                continue
            yield NormalizedItem(
                kind="dm",
                dedupe_key=f"dm:{_dm_id(message, conversation_id)}",
                text=text,
                created_at=infer_timestamp(message.get("createdAt")),
                raw={
                    "conversationId": conversation_id,
                    "senderId": message.get("senderId"),
                    "recipientId": message.get("recipientId"),
                    "group": group,
                    "urls": [u.get("expanded") or u.get("url")
                             for u in message.get("urls") or []],
                },
                occurrence_kind="dm",
                occurrence_detail=ctx.detail,
            )


def _parse_grok(records: list, ctx: ParseContext) -> Iterator[NormalizedItem]:
    """Grok chats arrive as individual messages; stitch them back into
    conversations so they read like the ChatGPT and Claude exports."""
    chats: dict[str, list[dict]] = {}
    for rec in records:
        item = rec.get("grokChatItem", rec)
        chat_id = item.get("chatId")
        if not chat_id or not (item.get("message") or "").strip():
            continue
        chats.setdefault(chat_id, []).append(item)
    for chat_id, messages in chats.items():
        messages.sort(key=lambda m: m.get("createdAt") or "")
        lines = []
        for m in messages:
            who = ((m.get("sender") or {}).get("name") or "user").lower()
            role = "user" if who == "user" else "assistant"
            lines.append(f"{role}: {html.unescape(m.get('message') or '').strip()}")
        first = messages[0]
        text = "\n\n".join(lines)
        yield NormalizedItem(
            kind="chat_conversation",
            dedupe_key=f"chat:grok:{chat_id}",
            title=(messages[0].get("message") or "")[:80] or f"Grok chat {chat_id}",
            text=text,
            created_at=infer_timestamp(first.get("createdAt")),
            raw={"provider": "grok", "chatId": chat_id, "urls": []},
            occurrence_kind="chat_mention",
            occurrence_detail=ctx.detail,
        )


def _parse_lists(records: list, ctx: ParseContext, name: str) -> Iterator[NormalizedItem]:
    stamp = archive_date(ctx)
    field = "userListInfo"
    for rec in records:
        node = rec.get(field) or next(iter(rec.values()), {})
        url = node.get("url") or node.get("listUrl")
        if not url:
            continue
        yield NormalizedItem(
            kind="x_list",
            dedupe_key=f"x-list:{url}",
            url=url,
            title=node.get("listName") or node.get("name") or "X list",
            created_at=stamp,
            raw={"membership": "member" if "member" in name else "subscribed"},
            occurrence_kind="x_list",
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
    if name.startswith(("account", "profile", "manifest")):
        return  # metadata only, nothing itemizable
    records = load_ytd(data)

    if name.startswith("like"):
        yield from _parse_likes(records, ctx)
    elif name.startswith(("follower", "following", "block", "mute")):
        yield from _parse_social(records, ctx, name)
    elif name.startswith("deleted-tweet"):
        yield from _parse_deleted_tweets(records, ctx)
    elif name.startswith("note-tweet"):
        yield from _parse_note_tweets(records, ctx)
    elif name.startswith("direct-message"):
        # headers files carry no text; only the message files are useful
        if "header" in name:
            return
        yield from _parse_dms(records, ctx, group="group" in name)
    elif name.startswith("grok-chat"):
        yield from _parse_grok(records, ctx)
    elif name.startswith("lists-"):
        yield from _parse_lists(records, ctx, name)
    else:  # tweets.js, tweet.js, tweets-part1.js, community-tweet.js
        yield from _parse_tweets(records, ctx)
