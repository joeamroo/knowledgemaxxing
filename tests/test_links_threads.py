import shutil

from km.config import Config
from km.db import get_db
from km.extract.link_expansion import extract_outbound_links
from km.extract.threads import reconstruct_threads
from km.ingest import ingest_manifest
from km.models import Manifest, ManifestEntry, NormalizedItem
from km.store import add_source, upsert_item
from tests.conftest import FIXTURES


def test_reconstruct_own_thread(tmp_path):
    cfg = Config()
    cfg.project_root = tmp_path
    conn = get_db(":memory:")
    dest = tmp_path / "tweets.js"
    shutil.copy(FIXTURES / "tweets.js", dest)
    manifest = Manifest(generated_at="2026-01-01T00:00:00Z",
                        entries=[ManifestEntry(path=str(dest), source_type="twitter_archive")])
    ingest_manifest(conn, manifest, cfg)
    threads = reconstruct_threads(conn)
    own = [t for t in threads if t["kind"] == "own"]
    assert len(own) == 1
    parts = own[0]["parts"]
    assert len(parts) == 2
    assert parts[0]["text"].startswith("1/")
    assert parts[1]["text"].startswith("2/")


def test_reconstruct_bookmarked_conversation():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "x_bookmarks", "scraper:x_bookmarks", None)
    for i, (tid, conv) in enumerate([("100", "100"), ("101", "100"), ("200", "999")]):
        upsert_item(conn, NormalizedItem(
            kind="bookmark_tweet", dedupe_key=f"tweet:{tid}",
            text=f"part {i}", url=f"https://twitter.com/i/web/status/{tid}",
            author="someone", raw={"conversation_id": conv},
        ), sid)
    threads = reconstruct_threads(conn)
    booked = [t for t in threads if t["kind"] == "bookmarked"]
    assert len(booked) == 1  # the lone tweet in conv 999 is not a thread
    assert len(booked[0]["parts"]) == 2


def test_extract_outbound_links():
    html = """
    <html><body>
      <nav><a href="https://elsewhere.com/nav">Nav Link</a></nav>
      <article>
        <a href="https://guzey.com/why-blog/">Why you should start a blog</a>
        <a href="/relative/post">A relative post</a>
        <a href="https://samesite.com/other">Same-site link</a>
        <a href="https://gwern.net/spaced-repetition">Spaced repetition</a>
        <a href="https://twitter.com/foo">tweet</a>
        <a href="https://ok.com/x.png">image</a>
        <a href="https://short.com/a">here</a>
      </article>
    </body></html>
    """
    links = extract_outbound_links(html, "https://samesite.com/blogroll")
    urls = {l["url"] for l in links}
    assert "https://guzey.com/why-blog/" in urls
    assert "https://gwern.net/spaced-repetition" in urls
    assert not any("samesite.com" in u for u in urls)     # internal skipped
    assert not any("twitter.com" in u for u in urls)      # social skipped
    assert not any(u.endswith(".png") for u in urls)      # assets skipped
    assert not any("elsewhere.com/nav" in u for u in urls)  # nav skipped
    assert not any("short.com" in u for u in urls)        # junk anchor text
