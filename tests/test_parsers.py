from datetime import datetime, timezone

from km.parsers import (
    bookmarks,
    chat_chatgpt,
    chat_claude,
    chrome_export_flex,
    chrome_history,
    my_activity,
    reddit_gdpr,
    takeout_browser,
    twitter_archive,
)
from km.parsers.base import NeedsMappingError
from km.parsers.registry import probe_chat_schema
from km.parsers.twitter_archive import strip_ytd_prefix
from tests.conftest import fixture_bytes, make_ctx


# Twitter archive

def test_strip_ytd_prefix():
    assert strip_ytd_prefix("window.YTD.like.part0 = [1]") == "[1]"
    assert strip_ytd_prefix("window.YTD.tweets.part12 = {\"a\":1}") == '{"a":1}'
    assert strip_ytd_prefix("[1]") == "[1]"  # already clean


def test_parse_likes():
    items = list(twitter_archive.parse(fixture_bytes("like.js"), make_ctx("like.js")))
    assert len(items) == 2
    first = items[0]
    assert first.kind == "like"
    assert first.dedupe_key == "tweet:1111111111111111111"
    assert '"work hard"' in first.text  # HTML entities unescaped
    assert "&amp;" not in first.text


def test_parse_tweets_rt_and_own():
    items = list(twitter_archive.parse(fixture_bytes("tweets.js"), make_ctx("tweets.js")))
    assert len(items) == 3
    rt, own1, own2 = items
    assert rt.kind == "retweet" and rt.author == "guzey"
    assert rt.raw["expanded_urls"] == ["https://guzey.com/why-blog/"]
    assert rt.created_at == datetime(2018, 10, 10, 20, 19, 24, tzinfo=timezone.utc)
    assert own1.kind == "own_tweet"
    assert own2.raw["in_reply_to_status_id"] == "4444444444444444444"


# Chrome live history SQLite

def test_chrome_history_sqlite(chrome_history_db):
    ctx = make_ctx("History", path=str(chrome_history_db), note="profile: Default")
    items = list(chrome_history.parse_path(chrome_history_db, ctx))
    urls = [i.url for i in items]
    assert urls.count("https://guzey.com/why-blog/") == 2  # per-visit rows
    assert "chrome://settings/" not in urls  # non-http dropped
    assert items[0].created_at.year == 2019  # WebKit conversion
    assert items[0].occurrence_detail == "profile: Default"


# Takeout BrowserHistory.json

def test_takeout_browser_transition_filter():
    items = list(
        takeout_browser.parse(fixture_bytes("BrowserHistory.json"), make_ctx("BrowserHistory.json"))
    )
    assert len(items) == 2  # AUTO_SUBFRAME dropped
    assert items[0].created_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


# My Activity

def test_my_activity_json():
    items = list(my_activity.parse(fixture_bytes("MyActivity.json"), make_ctx("MyActivity.json")))
    kinds = {i.kind for i in items}
    assert kinds == {"visit", "search_query", "chat_message"}
    visit = next(i for i in items if i.kind == "visit")
    assert visit.url == "https://guzey.com/why-blog/"  # redirect unwrapped
    assert visit.title == "Why you should start a blog"
    gemini = next(i for i in items if i.kind == "chat_message")
    assert "progress studies" in gemini.text


def test_my_activity_html_fallback():
    items = list(
        my_activity.parse_html(fixture_bytes("MyActivity.html"), make_ctx("MyActivity.html"))
    )
    kinds = [i.kind for i in items]
    assert "visit" in kinds and "search_query" in kinds
    visit = next(i for i in items if i.kind == "visit")
    assert visit.url == "https://guzey.com/why-blog/"
    # dates come from the br-separated trailing line, tz abbreviation applied
    assert visit.created_at == datetime(2025, 11, 13, 14, 50, 57, tzinfo=timezone.utc)
    search = next(i for i in items if i.kind == "search_query")
    assert search.created_at == datetime(2024, 7, 2, 6, 15, tzinfo=timezone.utc)


# Chat exports

def test_probe_chat_schema():
    assert probe_chat_schema(fixture_bytes("conversations_chatgpt.json")) == "chatgpt"
    assert probe_chat_schema(fixture_bytes("conversations_claude.json")) == "claude"
    assert probe_chat_schema(b"{}") is None


def test_chatgpt_parse():
    items = list(
        chat_chatgpt.parse(
            fixture_bytes("conversations_chatgpt.json"), make_ctx("conversations_chatgpt.json")
        )
    )
    conv = next(i for i in items if i.kind == "chat_conversation")
    assert conv.title == "Reading recommendations"
    assert "user: What should I read" in conv.text
    assert "assistant: Start with" in conv.text
    urls = {i.url for i in items if i.kind == "chat_message"}
    assert "https://rootsofprogress.org" in urls
    assert "https://guzey.com/why-blog/" in urls


def test_claude_parse():
    items = list(
        chat_claude.parse(
            fixture_bytes("conversations_claude.json"), make_ctx("conversations_claude.json")
        )
    )
    conv = next(i for i in items if i.kind == "chat_conversation")
    assert conv.title == "Essay hunting"
    assert conv.created_at == datetime(2024, 3, 1, 12, 0, tzinfo=timezone.utc)
    msg = next(i for i in items if i.kind == "chat_message")
    assert msg.url.startswith("https://slatestarcodex.com")


# Flexible chrome exports

def test_flex_csv():
    items = list(
        chrome_export_flex.parse(
            fixture_bytes("history_export.csv"), make_ctx("history_export.csv")
        )
    )
    assert len(items) == 2  # ftp:// dropped
    assert items[0].url == "https://guzey.com/why-blog/"
    assert items[0].title == "Why you should start a blog"
    assert items[0].created_at is not None
    assert items[0].raw.get("visitCount") == "3"  # extras preserved


def test_flex_json():
    items = list(
        chrome_export_flex.parse(
            fixture_bytes("history_export.json"), make_ctx("history_export.json")
        )
    )
    assert len(items) == 2
    assert items[0].title == "Do Things that Don't Scale"
    assert items[0].created_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_flex_unmappable_raises():
    import pytest

    data = b"foo,bar\n1,2\n"
    with pytest.raises(NeedsMappingError):
        list(chrome_export_flex.parse(data, make_ctx("weird.csv", path="/tmp/weird.csv")))


# Bookmarks family

def test_chrome_bookmarks_json():
    items = list(bookmarks.parse_chrome_json(fixture_bytes("Bookmarks"), make_ctx("Bookmarks")))
    assert {i.url for i in items} == {
        "http://www.paulgraham.com/articles.html", "https://nintil.com/",
    }
    nintil = next(i for i in items if "nintil" in i.url)
    assert nintil.created_at.year == 2019  # WebKit micros string converted
    assert "Blogs" in nintil.raw["folder"]


def test_netscape_html():
    items = list(
        bookmarks.parse_netscape_html(
            fixture_bytes("bookmarks_export.html"), make_ctx("bookmarks_export.html")
        )
    )
    assert len(items) == 2
    assert items[1].raw.get("tags") == "science"
    assert items[0].created_at == datetime(2023, 11, 14, 22, 15, tzinfo=timezone.utc)


def test_onetab():
    items = list(
        bookmarks.parse_onetab(fixture_bytes("onetab_export.txt"), make_ctx("onetab_export.txt"))
    )
    assert len(items) == 3
    assert items[0].title == "The Diff: Some Issue"


def test_instapaper_csv():
    items = list(
        bookmarks.parse_saves_csv(
            fixture_bytes("instapaper_export.csv"), make_ctx("instapaper_export.csv")
        )
    )
    assert len(items) == 2
    assert items[0].title == "How to Do Great Work"
    assert items[0].raw["folder"] == "Unread"


# Reddit GDPR

def test_reddit_gdpr_posts_and_comments():
    posts = list(
        reddit_gdpr.parse(fixture_bytes("saved_posts.csv"), make_ctx("saved_posts.csv"))
    )
    assert len(posts) == 2
    assert posts[0].kind == "saved_post"
    assert posts[0].dedupe_key == "reddit:abc123"
    assert posts[0].url.startswith("https://old.reddit.com/r/slatestarcodex/")
    assert posts[0].raw["subreddit"] == "slatestarcodex"
    comments = list(
        reddit_gdpr.parse(fixture_bytes("saved_comments.csv"), make_ctx("saved_comments.csv"))
    )
    assert comments[0].kind == "saved_comment"
