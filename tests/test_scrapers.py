import json

from km.scrapers.hn import parse_listing
from km.scrapers.reddit_saved import parse_saved_page
from km.scrapers.substack_saved import items_from_api_json
from km.scrapers.x_bookmarks import item_from_tweet, parse_bookmarks_response
from tests.conftest import fixture_bytes


def test_x_bookmarks_graphql_parse():
    payload = json.loads(fixture_bytes("x_bookmarks_graphql.json"))
    tweets, cursor = parse_bookmarks_response(payload)
    assert len(tweets) == 2
    assert cursor == "HBaAwLDN pretend-cursor-token"

    first = tweets[0]
    assert first["id"] == "1750000000000000001"
    assert first["screen_name"] == "visakanv"
    assert "contradictory advice" in first["text"]
    assert first["conversation_id"] == "1750000000000000001"

    second = tweets[1]
    # note_tweet text wins over the truncated legacy full_text
    assert second["text"].endswith("thanks to the GraphQL payload.")
    assert second["expanded_urls"] == [
        "https://www.kalzumeus.com/2012/01/23/salary-negotiation/"
    ]
    assert second["has_media"] is True
    # part of a thread: conversation id differs from tweet id
    assert second["conversation_id"] != second["id"]


def test_x_bookmark_to_item():
    payload = json.loads(fixture_bytes("x_bookmarks_graphql.json"))
    tweets, _ = parse_bookmarks_response(payload)
    item = item_from_tweet(tweets[0])
    assert item.kind == "bookmark_tweet"
    assert item.dedupe_key == "tweet:1750000000000000001"
    assert item.author == "visakanv"
    assert item.created_at.year == 2024


def test_hn_favorites_parse():
    items, next_path = parse_listing(
        fixture_bytes("hn_favorites.html").decode(), "favorite"
    )
    assert len(items) == 2
    assert next_path == "favorites?id=testuser&p=2"
    story = items[0]
    assert story.dedupe_key == "hn:38900001"
    assert story.url == "https://www.paulgraham.com/greatwork.html"
    assert story.raw["points"] == 3212
    assert story.raw["discussion"] == "https://news.ycombinator.com/item?id=38900001"
    ask = items[1]
    assert ask.url == "https://news.ycombinator.com/item?id=38900002"


def test_reddit_saved_parse():
    items, next_url = parse_saved_page(fixture_bytes("reddit_saved.html").decode())
    assert len(items) == 2
    assert next_url and "after=t1_com555" in next_url
    post, comment = items
    assert post.kind == "saved_post"
    assert post.dedupe_key == "reddit:abc999"  # merges with GDPR csv keys
    assert post.raw["subreddit"] == "slatestarcodex"
    assert post.raw["external_url"] == "https://www.astralcodexten.com/p/book-review-contest"
    assert comment.kind == "saved_comment"
    assert "celestial navigation" in comment.text


def test_substack_api_parse():
    payload = json.loads(fixture_bytes("substack_saved_api.json"))
    items = items_from_api_json(payload)
    assert len(items) == 2
    diff = items[0]
    assert diff.kind == "saved_post"
    assert diff.title == "The Rise and Fall of the Third Normal Form"
    assert diff.author == "Byrne Hobart"
    assert diff.raw["publication"] == "The Diff"
    assert diff.created_at is not None
