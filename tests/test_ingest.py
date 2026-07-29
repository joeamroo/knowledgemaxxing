import shutil
from pathlib import Path

from km.config import Config
from km.db import get_db
from km.ingest import ingest_manifest
from km.models import Manifest, ManifestEntry
from tests.conftest import FIXTURES


def _manifest_for(tmp_path: Path) -> Manifest:
    entries = []
    for name, stype in [
        ("like.js", "twitter_archive"),
        ("tweets.js", "twitter_archive"),
        ("BrowserHistory.json", "takeout_browser"),
        ("MyActivity.json", "my_activity"),
        ("conversations_chatgpt.json", "chat_export"),
        ("conversations_claude.json", "chat_export"),
        ("history_export.csv", "chrome_export"),
        ("Bookmarks", "chrome_bookmarks"),
        ("bookmarks_export.html", "bookmarks_html"),
        ("onetab_export.txt", "onetab"),
        ("instapaper_export.csv", "instapaper"),
        ("saved_posts.csv", "reddit_gdpr"),
        ("saved_comments.csv", "reddit_gdpr"),
    ]:
        dest = tmp_path / name
        shutil.copy(FIXTURES / name, dest)
        entries.append(ManifestEntry(path=str(dest), source_type=stype))
    # a malformed file that must be skipped, not crash
    bad = tmp_path / "history_broken.json"
    bad.write_text("{not json")
    entries.append(ManifestEntry(path=str(bad), source_type="chrome_export"))
    return Manifest(generated_at="2026-01-01T00:00:00Z", entries=entries)


def test_full_ingest_idempotent(tmp_path):
    cfg = Config()
    cfg.project_root = tmp_path
    conn = get_db(":memory:")
    manifest = _manifest_for(tmp_path)

    report1 = ingest_manifest(conn, manifest, cfg)
    assert report1.total_items > 0
    assert len(report1.ingested) == 13
    assert len(report1.skipped) == 1  # the malformed json
    assert (tmp_path / "skipped.log").exists()

    counts1 = {
        "items": conn.execute("SELECT count(*) c FROM items").fetchone()["c"],
        "occ": conn.execute("SELECT count(*) c FROM occurrences").fetchone()["c"],
    }

    report2 = ingest_manifest(conn, manifest, cfg)
    assert report2.total_items == 0
    assert len(report2.already) == 13
    counts2 = {
        "items": conn.execute("SELECT count(*) c FROM items").fetchone()["c"],
        "occ": conn.execute("SELECT count(*) c FROM occurrences").fetchone()["c"],
    }
    assert counts1 == counts2


def test_cross_source_merge(tmp_path):
    """guzey.com/why-blog appears in Takeout, MyActivity, a RT, chatgpt, and CSV:
    one item, many occurrences."""
    cfg = Config()
    cfg.project_root = tmp_path
    conn = get_db(":memory:")
    ingest_manifest(conn, _manifest_for(tmp_path), cfg)
    row = conn.execute(
        "SELECT id, kind FROM items WHERE canonical_url='https://guzey.com/why-blog'"
    ).fetchone()
    assert row is not None
    occ = conn.execute(
        "SELECT count(*) c FROM occurrences WHERE item_id=?", (row["id"],)
    ).fetchone()["c"]
    assert occ >= 4
