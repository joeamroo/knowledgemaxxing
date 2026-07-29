from km.classify.mentor import PERSONAS, build_evidence_pack
from km.db import get_db
from km.models import NormalizedItem
from km.sources.apple_notes import html_to_text
from km.store import add_source, upsert_item


def test_html_to_text():
    html = "<div><h1>Title</h1><ul><li>one</li><li>two</li></ul><br><div>done</div></div>"
    text = html_to_text(html)
    assert "Title" in text and "one" in text and "done" in text
    assert "<" not in text


def test_note_kind_accepted():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "apple_notes", "app:Notes", None)
    item = NormalizedItem(kind="note", dedupe_key="apple-note:x1",
                          title="Ideas", text="write more aphorisms")
    upsert_item(conn, item, sid)
    row = conn.execute("SELECT kind, title FROM items").fetchone()
    assert row["kind"] == "note" and row["title"] == "Ideas"


def test_evidence_pack_builds_and_personas_exist():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "t", "h")
    for i in range(5):
        upsert_item(conn, NormalizedItem(kind="like", dedupe_key=f"tweet:{i}",
                                         text=f"liked thing {i}"), sid)
    pack = build_evidence_pack(conn)
    assert pack["scale"]["total_items"] == 5
    assert len(pack["liked_tweets_random"]) == 5
    assert set(PERSONAS) == {"analyst", "harsh"}
