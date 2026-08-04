"""Full Twitter/X archive parsing: social graph, deleted tweets, DMs, Grok.

The load-bearing behavior: ingesting several archive snapshots dedupes an
account or tweet to ONE item while recording one occurrence per archive,
so things that vanished from later exports are still findable.
"""
import json

from km.db import get_db
from km.models import ManifestEntry
from km.parsers.base import ParseContext
from km.parsers.twitter_archive import archive_date, parse
from km.store import add_source, upsert_item


def _ctx(member: str, archive: str = "twitter-2026-07-24-abc.zip"):
    return ParseContext(entry=ManifestEntry(
        path=f"/Users/x/Downloads/{archive}",
        source_type="twitter_archive",
        zip_member=f"data/{member}",
    ))


def _ytd(name: str, payload) -> bytes:
    return f"window.YTD.{name}.part0 = {json.dumps(payload)}".encode()


def test_archive_date_from_filename():
    assert archive_date(_ctx("follower.js")).date().isoformat() == "2026-07-24"
    assert archive_date(_ctx("follower.js", "twitter-2024-04-29-x.zip")).year == 2024


def test_social_graph_kinds_and_keys():
    data = _ytd("follower", [
        {"follower": {"accountId": "123", "userLink": "https://twitter.com/intent/user?user_id=123"}},
        {"follower": {"accountId": "456"}},
    ])
    items = list(parse(data, _ctx("follower.js")))
    assert [i.kind for i in items] == ["x_follower", "x_follower"]
    assert items[0].dedupe_key == "x-account:123:follower"
    assert items[1].url.endswith("user_id=456")  # synthesized when missing
    assert items[0].created_at.date().isoformat() == "2026-07-24"

    following = list(parse(_ytd("following", [{"following": {"accountId": "789"}}]),
                           _ctx("following.js")))
    assert following[0].kind == "x_following"
    assert following[0].dedupe_key == "x-account:789:following"

    blocked = list(parse(_ytd("block", [{"blocking": {"accountId": "9"}}]), _ctx("block.js")))
    muted = list(parse(_ytd("mute", [{"muting": {"accountId": "8"}}]), _ctx("mute.js")))
    assert blocked[0].kind == "x_blocked" and muted[0].kind == "x_muted"


def test_unfollowed_accounts_survive_newer_archives():
    """The whole point: ingest 2024 then 2026; someone dropped from the
    2026 export is still an item, with occurrences proving when."""
    conn = get_db(":memory:")
    old = _ytd("following", [{"following": {"accountId": "gone"}},
                             {"following": {"accountId": "stayed"}}])
    new = _ytd("following", [{"following": {"accountId": "stayed"}}])

    sid_old, _ = add_source(conn, "twitter_archive", "2024.zip!following.js", "h1")
    for item in parse(old, _ctx("following.js", "twitter-2024-04-29-x.zip")):
        upsert_item(conn, item, sid_old)
    sid_new, _ = add_source(conn, "twitter_archive", "2026.zip!following.js", "h2")
    for item in parse(new, _ctx("following.js", "twitter-2026-07-24-x.zip")):
        upsert_item(conn, item, sid_new)
    conn.commit()

    rows = {r["dedupe_key"]: r["id"] for r in conn.execute(
        "SELECT id, dedupe_key FROM items WHERE kind='x_following'")}
    assert len(rows) == 2  # deduped, and the departed account was NOT lost

    def archives_seen(key):
        return [o["occurred_at"][:10] for o in conn.execute(
            "SELECT occurred_at FROM occurrences WHERE item_id=? ORDER BY occurred_at",
            (rows[key],))]

    assert archives_seen("x-account:gone:following") == ["2024-04-29"]
    assert archives_seen("x-account:stayed:following") == ["2024-04-29", "2026-07-24"]


def test_deleted_tweets_merge_with_the_original_tweet():
    conn = get_db(":memory:")
    tweet = _ytd("tweets", [{"tweet": {"id_str": "555", "full_text": "a post",
                                       "created_at": "Wed Apr 10 03:55:54 +0000 2024"}}])
    deleted = _ytd("deleted_tweets", [{"tweet": {"id_str": "555", "full_text": "a post",
                                                 "created_at": "Wed Apr 10 03:55:54 +0000 2024"}}])
    sid1, _ = add_source(conn, "twitter_archive", "a!tweets.js", "h1")
    for item in parse(tweet, _ctx("tweets.js", "twitter-2024-04-29-x.zip")):
        upsert_item(conn, item, sid1)
    sid2, _ = add_source(conn, "twitter_archive", "b!deleted-tweets.js", "h2")
    items = list(parse(deleted, _ctx("deleted-tweets.js")))
    for item in items:
        upsert_item(conn, item, sid2)
    conn.commit()

    assert items[0].occurrence_kind == "deleted_tweet"
    assert items[0].raw["deleted"] is True
    assert conn.execute("SELECT count(*) FROM items WHERE dedupe_key='tweet:555'").fetchone()[0] == 1
    kinds = {o["kind"] for o in conn.execute("SELECT kind FROM occurrences")}
    assert kinds == {"own_tweet", "deleted_tweet"}


def test_note_tweets_carry_full_long_form_text():
    long_text = "A long form post. " * 60
    data = _ytd("note_tweet", [{"noteTweet": {
        "noteTweetId": "42", "createdAt": "2026-04-10T03:55:54.000Z",
        "core": {"text": long_text,
                 "urls": [{"expandedUrl": "https://youssefaa.com/notes/x"}]},
    }}])
    items = list(parse(data, _ctx("note-tweet.js")))
    assert items[0].dedupe_key == "note:42"
    assert items[0].raw["long_form"] is True
    assert len(items[0].text) > 500
    assert items[0].raw["expanded_urls"] == ["https://youssefaa.com/notes/x"]


def test_dms_one_item_per_message_deduped():
    convo = {"dmConversation": {"conversationId": "c1", "messages": [
        {"messageCreate": {"id": "m1", "text": "first", "senderId": "s",
                           "recipientId": "r", "createdAt": "2025-01-01T00:00:00.000Z"}},
        {"messageCreate": {"id": "m2", "text": "second", "senderId": "r",
                           "recipientId": "s", "createdAt": "2025-01-02T00:00:00.000Z"}},
        {"reactionCreate": {"emoji": "x"}},  # no text: skipped
    ]}}
    items = list(parse(_ytd("direct_messages", [convo]), _ctx("direct-messages.js")))
    assert [i.dedupe_key for i in items] == ["dm:m1", "dm:m2"]
    assert items[0].kind == "dm" and items[0].text == "first"
    assert items[0].raw["conversationId"] == "c1"
    assert items[0].raw["group"] is False

    grouped = list(parse(_ytd("direct_messages_group", [convo]),
                         _ctx("direct-messages-group.js")))
    assert grouped[0].raw["group"] is True
    # headers carry no text and must be skipped entirely
    assert list(parse(_ytd("direct_message_headers", [convo]),
                      _ctx("direct-message-headers.js"))) == []


def test_grok_messages_stitch_into_conversations():
    data = _ytd("grok_chat_item", [
        {"grokChatItem": {"chatId": "c9", "createdAt": "2024-12-08T19:57:41.589Z",
                          "sender": {"name": "User"}, "message": "first question"}},
        {"grokChatItem": {"chatId": "c9", "createdAt": "2024-12-08T19:58:00.000Z",
                          "sender": {"name": "Agent"}, "message": "an answer"}},
        {"grokChatItem": {"chatId": "c7", "createdAt": "2025-02-01T10:00:00.000Z",
                          "sender": {"name": "User"}, "message": "other chat"}},
    ])
    items = sorted(parse(data, _ctx("grok-chat-item.js")), key=lambda i: i.dedupe_key)
    assert [i.dedupe_key for i in items] == ["chat:grok:c7", "chat:grok:c9"]
    c9 = items[1]
    assert c9.kind == "chat_conversation"
    assert c9.raw["provider"] == "grok"
    assert c9.text == "user: first question\n\nassistant: an answer"


def test_grok_conversations_work_with_the_chat_messages_tool():
    """Grok chats must be readable by the same retrospective tooling as
    the ChatGPT and Claude exports."""
    from km.search.tools import get_chat_messages

    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "a!grok.js", "h")
    data = _ytd("grok_chat_item", [
        {"grokChatItem": {"chatId": "c1", "createdAt": "2024-12-08T19:57:41.589Z",
                          "sender": {"name": "User"}, "message": "why did I fail"}},
        {"grokChatItem": {"chatId": "c1", "createdAt": "2024-12-08T19:58:00.000Z",
                          "sender": {"name": "Agent"}, "message": "here is why"}},
    ])
    item_id = None
    for item in parse(data, _ctx("grok-chat-item.js")):
        item_id = upsert_item(conn, item, sid)
    conn.commit()

    out = get_chat_messages(conn, item_id, role="user")
    assert out["provider"] == "grok"
    assert out["returned"] == 1
    assert out["messages"][0]["text"] == "why did I fail"


def test_metadata_files_yield_nothing():
    for member in ("account.js", "profile.js", "manifest.js"):
        assert list(parse(_ytd("account", [{"account": {"username": "x"}}]),
                          _ctx(member))) == []


# ── page-capture exports (fttf-*.json) ────────────────────

def test_page_capture_parses_and_merges_with_visits():
    from km.parsers.page_capture import parse as parse_capture, probe

    payload = json.dumps({"document": [
        [1, "An Essay", "https://blog.example/post", "Blog",
         "The full article text, captured while reading.", "hash", None,
         "blog.example", 1742539201142, "2025-03-21", "readability",
         1742539177508, 1742539201142],
        [2, "search - Google Search", "https://www.google.com/search?q=x", None,
         None, None, None, "www.google.com", 1742539778998, "2025-03-21",
         None, 1742539778999, 1742539778999],
        [3, "not a url", "chrome://newtab", None, None, None, None,
         None, 0, "", None, 0, 0],
    ]}).encode()
    assert probe(payload)

    items = list(parse_capture(payload, _ctx("fttf-1.json")))
    assert len(items) == 2  # chrome:// dropped
    assert items[0].kind == "visit"
    assert items[0].text.startswith("The full article text")
    assert items[0].raw["captured_text"] is True
    assert items[0].created_at.date().isoformat() == "2025-03-21"
    assert items[1].text is None and items[1].raw["captured_text"] is False

    # merges into an existing title-only visit and upgrades its text
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "chrome_live_history", "hist", "h")
    from km.models import NormalizedItem
    upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key=items[0].dedupe_key, title="An Essay",
        url="https://blog.example/post"), sid)
    sid2, _ = add_source(conn, "page_capture", "fttf", "h2")
    upsert_item(conn, items[0], sid2)
    conn.commit()
    rows = conn.execute("SELECT text FROM items WHERE dedupe_key=?",
                        (items[0].dedupe_key,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["text"].startswith("The full article text")


def test_page_capture_ignores_foreign_json():
    from km.parsers.page_capture import parse as parse_capture

    assert list(parse_capture(b'{"other": []}', _ctx("fttf-1.json"))) == []
    assert list(parse_capture(b'{"document": []}', _ctx("fttf-1.json"))) == []
