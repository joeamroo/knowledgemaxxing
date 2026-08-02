"""Tests for the full-archive JSON/JSONL export."""
import json

from km.db import get_db
from km.exporters.jsonl import export_json
from km.models import NormalizedItem
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "twitter_archive", "archive.zip", "hash")
    return conn, sid


def _item(conn, sid, key, title="A title", text="Body", url="https://x.com/a"):
    item_id = upsert_item(conn, NormalizedItem(
        kind="like", dedupe_key=key, title=title, text=text, url=url,
        raw={"secret": "raw-only"}), sid)
    conn.commit()
    return item_id


def test_jsonl_one_parseable_record_per_item(tmp_path):
    conn, sid = _db()
    _item(conn, sid, "tw:1")
    _item(conn, sid, "tw:2", title="Second")

    out = tmp_path / "archive.jsonl"
    summary = export_json(conn, out)

    assert summary["items"] == 2
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 2
    recs = [json.loads(ln) for ln in lines]
    assert {r["title"] for r in recs} == {"A title", "Second"}
    for r in recs:
        assert r["dedupe_key"].startswith("tw:")
        assert isinstance(r["occurrences"], list) and r["occurrences"]
        assert r["occurrences"][0]["source"] == "twitter_archive"


def test_json_format_is_valid_array(tmp_path):
    conn, sid = _db()
    _item(conn, sid, "tw:1")
    _item(conn, sid, "tw:2")

    out = tmp_path / "archive.json"
    export_json(conn, out, fmt="json")
    data = json.loads(out.read_text())
    assert isinstance(data, list) and len(data) == 2


def test_raw_excluded_by_default_included_on_request(tmp_path):
    conn, sid = _db()
    _item(conn, sid, "tw:1")

    out = tmp_path / "a.jsonl"
    export_json(conn, out)
    assert "raw-only" not in out.read_text()

    export_json(conn, out, include_raw=True)
    rec = json.loads(out.read_text().strip())
    assert rec["raw"] == {"secret": "raw-only"}


def test_category_override_wins(tmp_path):
    conn, sid = _db()
    item_id = _item(conn, sid, "tw:1")
    conn.execute(
        "INSERT INTO classifications(item_id, category, prompt_version, classified_at)"
        " VALUES (?, 'joke', 'v1', '2026-01-01')", (item_id,))
    conn.execute(
        "INSERT INTO user_edits(item_id, category_override) VALUES (?, 'aphorism')",
        (item_id,))
    conn.commit()

    out = tmp_path / "a.jsonl"
    export_json(conn, out)
    rec = json.loads(out.read_text().strip())
    assert rec["category"] == "aphorism"
    assert rec["classification"]["category"] == "joke"
    assert rec["user_edits"]["category_override"] == "aphorism"


def test_limit(tmp_path):
    conn, sid = _db()
    for i in range(5):
        _item(conn, sid, f"tw:{i}")

    out = tmp_path / "a.jsonl"
    summary = export_json(conn, out, limit=2)
    assert summary["items"] == 2
    assert len(out.read_text().strip().splitlines()) == 2
