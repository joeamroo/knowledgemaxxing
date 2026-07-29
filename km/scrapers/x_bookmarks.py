"""X bookmarks scraper: the most cautious scraper in km.

Bookmarks are not in the Twitter archive; the browser is the only way.
We open x.com/i/bookmarks and intercept the GraphQL Bookmarks API
responses while scrolling slowly (one viewport every 3-5 seconds,
jittered). The JSON gives untruncated text, author, created_at, expanded
URLs, media indicators, and conversation ids.

Defaults to headed. Stops immediately on any challenge or rate limit,
saving all progress. Resumable via last-seen tweet id.
"""
from __future__ import annotations

from typing import Optional

from km.models import NormalizedItem
from km.scrapers.base import BaseScraper
from km.timeutil import infer_timestamp


def _dig(d: dict, *keys, default=None):
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key)
    return d if d is not None else default


def _tweet_from_result(result: dict) -> Optional[dict]:
    """Unwrap tweet_results.result into a flat dict of the fields we keep."""
    if not isinstance(result, dict):
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet") or {}
    legacy = result.get("legacy") or {}
    tweet_id = result.get("rest_id") or legacy.get("id_str")
    if not tweet_id:
        return None
    user_legacy = _dig(result, "core", "user_results", "result", "legacy", default={})
    if not user_legacy:  # newer payloads move user info under "core"->"core"
        user_legacy = _dig(result, "core", "user_results", "result", "core", default={})
    note_text = _dig(result, "note_tweet", "note_tweet_results", "result", "text")
    entities = legacy.get("entities") or {}
    expanded = [
        u.get("expanded_url") for u in entities.get("urls", []) if u.get("expanded_url")
    ]
    media = entities.get("media") or []
    return {
        "id": str(tweet_id),
        "text": note_text or legacy.get("full_text") or "",
        "created_at": legacy.get("created_at"),
        "conversation_id": legacy.get("conversation_id_str"),
        "screen_name": user_legacy.get("screen_name"),
        "display_name": user_legacy.get("name"),
        "expanded_urls": expanded,
        "has_media": bool(media),
        "media_types": sorted({m.get("type", "") for m in media if m.get("type")}),
    }


def parse_bookmarks_response(payload: dict) -> tuple[list[dict], Optional[str]]:
    """Parse one GraphQL Bookmarks response.

    Returns (tweets, bottom_cursor). Defensive about instruction shapes.
    """
    timeline = (
        _dig(payload, "data", "bookmark_timeline_v2", "timeline")
        or _dig(payload, "data", "bookmark_timeline", "timeline")
        or {}
    )
    tweets: list[dict] = []
    cursor = None
    for instruction in timeline.get("instructions") or []:
        for entry in instruction.get("entries") or []:
            content = entry.get("content") or {}
            entry_type = content.get("entryType") or content.get("__typename") or ""
            if "Cursor" in entry_type:
                if content.get("cursorType") == "Bottom":
                    cursor = content.get("value")
                continue
            item_content = content.get("itemContent") or {}
            result = _dig(item_content, "tweet_results", "result")
            tweet = _tweet_from_result(result or {})
            if tweet:
                tweets.append(tweet)
    return tweets, cursor


def item_from_tweet(tweet: dict) -> NormalizedItem:
    return NormalizedItem(
        kind="bookmark_tweet",
        dedupe_key=f"tweet:{tweet['id']}",
        url=f"https://twitter.com/i/web/status/{tweet['id']}",
        text=tweet["text"] or None,
        author=tweet.get("screen_name"),
        title=None,
        created_at=infer_timestamp(tweet.get("created_at")),
        raw={
            "display_name": tweet.get("display_name"),
            "conversation_id": tweet.get("conversation_id"),
            "expanded_urls": tweet.get("expanded_urls") or [],
            "has_media": tweet.get("has_media", False),
            "media_types": tweet.get("media_types") or [],
        },
        occurrence_detail="x bookmarks",
    )


class XBookmarksScraper(BaseScraper):
    name = "x_bookmarks"
    source_kind = "x_bookmarks"

    MAX_IDLE_SCROLLS = 8

    def run(self) -> int:
        from km.scrapers.session import session_valid

        if not session_valid(self.context, "x"):
            self.stop("no valid X session; run km login")

        # Incremental boundary: newest bookmarked tweet id from previous runs
        newest_seen = self.cursor
        first_id_this_run: Optional[str] = None
        challenged: list[str] = []
        response_count = 0
        collected: dict[str, dict] = {}

        def on_response(response) -> None:
            nonlocal response_count, first_id_this_run
            url = response.url
            if "/i/api/graphql/" not in url or "Bookmark" not in url:
                return
            if response.status == 429:
                challenged.append("rate limited (HTTP 429) on the Bookmarks API")
                return
            if response.status in (401, 403):
                challenged.append(f"auth challenge (HTTP {response.status}) on the Bookmarks API")
                return
            try:
                payload = response.json()
            except Exception:
                return
            response_count += 1
            self.save_raw(f"bookmarks-{response_count}.json", payload)
            tweets, _ = parse_bookmarks_response(payload)
            for tweet in tweets:
                if first_id_this_run is None:
                    first_id_this_run = tweet["id"]
                collected.setdefault(tweet["id"], tweet)

        page = self.context.new_page()
        page.on("response", on_response)
        saved_ids: set[str] = set()

        def drain() -> None:
            """Persist everything collected so far; interruption loses nothing."""
            for tweet_id, tweet in list(collected.items()):
                if tweet_id not in saved_ids:
                    self.save_item(item_from_tweet(tweet))
                    saved_ids.add(tweet_id)

        try:
            page.goto("https://x.com/i/bookmarks", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            if any(marker in page.url for marker in ("login", "flow", "account/access")):
                self.stop("X redirected to a login/challenge page; run km login --headed")

            # mouse.wheel scrolls the element UNDER the pointer, so park the
            # pointer over the timeline first or nothing ever paginates
            viewport = page.viewport_size or {"width": 1280, "height": 900}
            center_x = viewport["width"] * 0.5
            center_y = viewport["height"] * 0.55
            page.mouse.move(center_x, center_y)

            idle = 0
            reached_old = False
            while idle < self.MAX_IDLE_SCROLLS and not challenged and not reached_old:
                if newest_seen and newest_seen in collected and len(collected) > 25:
                    reached_old = True
                    break
                before = len(collected)
                page.mouse.move(center_x, center_y)
                page.mouse.wheel(0, 900)
                if idle >= 2:  # wheel is not biting: scroll the document directly
                    page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
                # deliberately slow: one viewport every 3-5 seconds, jittered
                self.pause(3.0, 5.0)
                drain()
                if len(collected) == before:
                    idle += 1
                else:
                    idle = 0

            drain()
            if first_id_this_run:
                self.cursor = first_id_this_run

            if challenged:
                self.stop(challenged[0])
        finally:
            page.close()
        return self.items_saved
