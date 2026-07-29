import json

from km.classify.client import estimate_cost, parse_json_response, strip_code_fences
from km.classify.passes import pending_items, run_pass
from km.classify.tweet_categories import TWEET_PASS
from km.db import get_db
from km.models import NormalizedItem
from km.store import add_source, upsert_item


def _db_with_tweets():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "/tmp/like.js", "h")
    tweets = [
        ("1", "like", "Everyone says diversify but concentration built every great fortune"),
        ("2", "like", "🧵 1/ A thread on Roman concrete"),
        ("3", "bookmark_tweet", "TIL the Eiffel Tower grows 15cm in summer"),
    ]
    for tid, kind, text in tweets:
        upsert_item(
            conn,
            NormalizedItem(kind=kind, dedupe_key=f"tweet:{tid}", text=text,
                           url=f"https://twitter.com/i/web/status/{tid}"),
            sid,
        )
    return conn


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    def __init__(self, text):
        self.content = [FakeBlock(text)]


class FakeMessages:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse(self.reply)


class FakeClient:
    def __init__(self, reply):
        self.messages = FakeMessages(reply)


def test_strip_code_fences():
    assert strip_code_fences('```json\n[{"a":1}]\n```') == '[{"a":1}]'
    assert strip_code_fences('[{"a":1}]') == '[{"a":1}]'


def test_parse_json_defensive():
    assert parse_json_response('Sure! Here you go: [{"id": 1}]') == [{"id": 1}]
    assert parse_json_response("total garbage") is None


def test_estimate_cost_shape():
    est = estimate_cost(["x" * 400] * 80, 2000, "claude-sonnet-4-6", 40)
    assert est.item_count == 80 and est.batch_count == 2
    assert est.est_dollars > 0


def test_run_pass_with_fake_client():
    conn = _db_with_tweets()
    pending = pending_items(conn, TWEET_PASS)
    assert len(pending) == 3
    ids = [r["id"] for r in pending]
    reply = json.dumps([
        {"id": ids[0], "category": "contrarian", "confidence": 0.9},
        {"id": ids[1], "category": "thread", "secondary": ["interesting_fact"], "confidence": 0.8},
        {"id": ids[2], "category": "not_a_real_category"},
    ])
    client = FakeClient(reply)
    result = run_pass(conn, client, TWEET_PASS, "claude-sonnet-4-6", batch_size=40)
    assert result.classified == 2
    assert result.failed == 1  # invalid category fell back to other

    # cached: nothing pending on a second run
    assert pending_items(conn, TWEET_PASS) == []

    row = conn.execute(
        "SELECT category, subcategories FROM classifications WHERE item_id=?", (ids[1],)
    ).fetchone()
    assert row["category"] == "thread"
    assert json.loads(row["subcategories"]) == ["interesting_fact"]

    # privacy: only text goes to the API
    sent = client.messages.calls[0]["messages"][0]["content"]
    assert "/tmp/like.js" not in sent


def test_prompt_version_bump_reclassifies():
    conn = _db_with_tweets()
    client = FakeClient("[]")
    run_pass(conn, client, TWEET_PASS, "claude-sonnet-4-6")
    assert pending_items(conn, TWEET_PASS) == []
    from dataclasses import replace

    bumped = replace(TWEET_PASS, version=TWEET_PASS.version + "-next")
    assert len(pending_items(conn, bumped)) == 3
