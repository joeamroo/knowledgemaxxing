"""Tests for the Obsidian vault exporter."""
import re

from km.db import get_db
from km.exporters.vault import export_vault
from km.models import NormalizedItem
from km.store import add_source, upsert_item


def _db():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "test", "test", "hash")
    return conn, sid


def _essay(conn, sid, key, title, url, domain):
    item_id = upsert_item(conn, NormalizedItem(
        kind="visit", dedupe_key=key, title=title, url=url, raw={}), sid)
    conn.execute("UPDATE items SET is_essay=1, domain=? WHERE id=?", (domain, item_id))
    conn.commit()
    return item_id


def test_export_vault_writes_notes_and_mocs(tmp_path):
    conn, sid = _db()
    _essay(conn, sid, "url:a", "How to Do Great Work", "https://paulgraham.com/gw.html", "paulgraham.com")
    _essay(conn, sid, "url:b", "In Praise of Idleness", "https://harpers.org/idle", "harpers.org")

    summary = export_vault(conn, tmp_path)

    assert summary["notes"] == 2
    root = tmp_path / "km"
    assert (root / "km-index.md").exists()
    assert (root / "notes" / "How to Do Great Work.md").exists()
    note = (root / "notes" / "How to Do Great Work.md").read_text()
    assert 'title: "How to Do Great Work"' in note
    assert "url: https://paulgraham.com/gw.html" in note
    assert "[[km-domain-paulgraham-com]]" not in note  # dots kept, not hyphenated
    assert "[[km-domain-paulgraham.com]]" in note


def test_export_vault_has_no_dangling_wikilinks(tmp_path):
    conn, sid = _db()
    for i in range(5):
        _essay(conn, sid, f"url:{i}", f"Essay {i}", f"https://blog{i}.com/p", f"blog{i}.com")

    export_vault(conn, tmp_path)
    root = tmp_path / "km"
    stems = {p.stem for p in root.rglob("*.md")}
    for p in root.rglob("*.md"):
        for target in re.findall(r"\[\[([^\]]+)\]\]", p.read_text()):
            assert target in stems, f"dangling link: {target}"


def test_export_vault_no_em_dashes(tmp_path):
    conn, sid = _db()
    _essay(conn, sid, "url:a", "Some Essay", "https://x.com/a", "x.com")
    export_vault(conn, tmp_path)
    em_dash = "\u2014"
    for path in (tmp_path / "km").rglob("*.md"):
        assert em_dash not in path.read_text()


def test_export_vault_essays_only_excludes_saves(tmp_path):
    conn, sid = _db()
    _essay(conn, sid, "url:a", "An Essay", "https://x.com/a", "x.com")
    # a saved bookmark that is not an essay
    upsert_item(conn, NormalizedItem(
        kind="bookmark_tweet", dedupe_key="tw:1", text="a saved tweet",
        url="https://x.com/s/1", raw={}), sid)
    conn.commit()

    both = export_vault(conn, tmp_path / "both")
    essays = export_vault(conn, tmp_path / "essays", include_saved=False)
    assert both["notes"] == 2
    assert essays["notes"] == 1
