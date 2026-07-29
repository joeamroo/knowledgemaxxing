import shutil

from km.config import Config
from km.db import get_db
from km.extract.essays import EssayRules, mark_essays
from km.extract.reading_lists import mark_reading_lists
from km.extract.score import compute_scores
from km.extract.threads import mark_threads
from km.ingest import ingest_manifest
from km.models import Manifest, ManifestEntry
from tests.conftest import FIXTURES

DOMAINS = {
    "essay_domains": ["guzey.com", "gwern.net", "paulgraham.com", "lesswrong.com",
                      "astralcodexten.com", "nintil.com", "slimemoldtimemold.com",
                      "fantasticanachronism.com", "slatestarcodex.com"],
    "essay_patterns": ["*.substack.com", "*.wordpress.com", "medium.com"],
    "exclude_domains": ["amazon.com"],
}


def _rules():
    return EssayRules(DOMAINS)


def test_bucket_allowlist_and_patterns():
    r = _rules()
    assert r.bucket("https://guzey.com/why-blog", "guzey.com") == "essay"
    assert r.bucket("https://foo.substack.com/p/thing", "foo.substack.com") == "essay"
    assert r.bucket("https://medium.com/@x/post", "medium.com") == "essay"


def test_bucket_path_signals():
    r = _rules()
    assert r.bucket("https://example.com/blog/my-post", "example.com") == "essay"
    assert r.bucket("https://example.com/2024/05/a-post", "example.com") == "essay"
    assert r.bucket("https://example.com/pricing", "example.com") == "other"


def test_bucket_excludes():
    r = _rules()
    assert r.bucket("https://www.google.com/search?q=x", "www.google.com") == "excluded"
    assert r.bucket("https://mail.google.com/mail/u/0", "mail.google.com") == "excluded"
    assert r.bucket("https://www.youtube.com/watch?v=abc", "www.youtube.com") == "youtube"
    assert r.bucket("https://github.com/org/repo/pull/42", "github.com") == "github_pr"
    assert r.bucket("https://amazon.com/dp/B00X", "amazon.com") == "excluded"
    assert r.bucket("https://twitter.com", "twitter.com") == "excluded"
    assert r.bucket("https://news.ycombinator.com", "news.ycombinator.com") == "excluded"


def _ingested_db(tmp_path):
    cfg = Config()
    cfg.project_root = tmp_path
    conn = get_db(":memory:")
    entries = []
    for name, stype in [
        ("like.js", "twitter_archive"),
        ("tweets.js", "twitter_archive"),
        ("BrowserHistory.json", "takeout_browser"),
        ("conversations_chatgpt.json", "chat_export"),
        ("instapaper_export.csv", "instapaper"),
    ]:
        dest = tmp_path / name
        shutil.copy(FIXTURES / name, dest)
        entries.append(ManifestEntry(path=str(dest), source_type=stype))
    manifest = Manifest(generated_at="2026-01-01T00:00:00Z", entries=entries)
    ingest_manifest(conn, manifest, cfg)
    return conn


def test_mark_essays_on_ingested(tmp_path):
    conn = _ingested_db(tmp_path)
    counts = mark_essays(conn, DOMAINS)
    assert counts.get("essay", 0) >= 2  # guzey why-blog, greatwork etc
    row = conn.execute(
        "SELECT is_essay FROM items WHERE canonical_url='https://guzey.com/why-blog'"
    ).fetchone()
    assert row["is_essay"] == 1


def test_mark_threads(tmp_path):
    conn = _ingested_db(tmp_path)
    marked = mark_threads(conn)
    assert marked >= 1
    row = conn.execute(
        "SELECT is_thread FROM items WHERE dedupe_key='tweet:4444444444444444444'"
    ).fetchone()
    assert row["is_thread"] == 1  # "1/ A thread..." marker + self-reply chain root


def test_reading_lists_chat_ask(tmp_path):
    conn = _ingested_db(tmp_path)
    marked = mark_reading_lists(conn)
    assert marked >= 1
    row = conn.execute(
        "SELECT in_reading_list FROM items WHERE kind='chat_conversation'"
    ).fetchone()
    assert row["in_reading_list"] == 1  # "What should I read... reading list?"


def test_interest_score_ordering(tmp_path):
    conn = _ingested_db(tmp_path)
    compute_scores(conn)
    # a bookmarked-and-visited item outranks a visited-only item
    visited_only = conn.execute(
        "SELECT interest_score FROM items WHERE canonical_url='https://gwern.net'"
    ).fetchone()
    saved = conn.execute(
        "SELECT interest_score FROM items WHERE canonical_url LIKE '%greatwork%'"
    ).fetchone()
    assert saved["interest_score"] > visited_only["interest_score"]


def test_exports_no_em_dashes(tmp_path):
    from km.exporters.markdown import export_all

    conn = _ingested_db(tmp_path)
    mark_essays(conn, DOMAINS)
    mark_threads(conn)
    mark_reading_lists(conn)
    compute_scores(conn)
    out = tmp_path / "exports"
    written = export_all(conn, out)
    assert (out / "essays.md").exists()
    assert (out / "reading-lists.md").exists()
    assert (out / "stats.md").exists()
    for path in written:
        assert "—" not in path.read_text(), f"em dash in {path}"
    essays = (out / "essays.md").read_text()
    assert "guzey.com" in essays
