import json
import zipfile
from pathlib import Path

from km.config import Config, SearchConfig
from km.discover.icloud import real_path_for_placeholder
from km.discover.patterns import classify_name, sniff_csv_header, sniff_file
from km.discover.scanner import scan_roots, scan_zip
from km.discover.datasources import missing_report, write_data_sources_md
from km.discover.manifest import build_manifest


def test_classify_name_patterns():
    assert classify_name("twitter-2024-05-01.zip") == "twitter_archive_zip"
    assert classify_name("like.js") == "twitter_archive"
    assert classify_name("Tweets.js") == "twitter_archive"
    assert classify_name("takeout-20240501T000000Z-001.zip") == "takeout_zip"
    assert classify_name("BrowserHistory.json") == "takeout_browser"
    assert classify_name("MyActivity.json") == "my_activity"
    assert classify_name("MyActivity.html") == "my_activity_html"
    assert classify_name("conversations.json") == "chat_export"
    assert classify_name("bookmarks_5_1_24.html") == "bookmarks_html"
    assert classify_name("saved_posts.csv") == "reddit_gdpr"
    assert classify_name("history_export.csv") == "chrome_export"
    assert classify_name("chrome_history_2023.json") == "chrome_export"
    assert classify_name("sites_visited_2022.csv") == "chrome_export"
    assert classify_name("random.pdf") is None


def test_classify_my_activity_with_a_space():
    """Takeout ships this file both ways. Missing the spaced form silently
    skipped two real exports, so both spellings are pinned here."""
    assert classify_name("Takeout/My Activity/Search/My Activity.json") == "my_activity"
    assert classify_name("Takeout/My Activity/Search/My Activity.html") == "my_activity_html"
    assert classify_name("Takeout/My Activity/Search/MyActivity.json") == "my_activity"
    assert classify_name("Takeout/My Activity/Search/MyActivity.html") == "my_activity_html"


def test_sniff_csv_header_urlish():
    stype, header = sniff_csv_header("url,title,visit_time\nhttps://a.com,A,123\n")
    assert stype == "chrome_export"
    stype2, _ = sniff_csv_header("name,age\nbob,4\n")
    assert stype2 is None


def test_sniff_onetab(tmp_path):
    f = tmp_path / "mytabs.txt"
    f.write_text("https://a.com/x | Page A\nhttps://b.com/y | Page B\n")
    assert sniff_file(f) == ("onetab", None)


def test_sniff_generic_json(tmp_path):
    f = tmp_path / "random_links.json"
    f.write_text(json.dumps([{"link": f"https://site{i}.com/post"} for i in range(10)]))
    stype, _ = sniff_file(f)
    assert stype == "generic"


def test_scan_roots_finds_everything(tmp_path):
    (tmp_path / "like.js").write_text("window.YTD.like.part0 = []")
    (tmp_path / "conversations.json").write_text("[]")
    (tmp_path / "history_2023.csv").write_text("url,title,date\nhttps://a.com,A,2023-01-01\n")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "MyActivity.json").write_text("[]")
    with zipfile.ZipFile(tmp_path / "twitter-2024.zip", "w") as zf:
        zf.writestr("data/like.js", "window.YTD.like.part0 = []")
        zf.writestr("data/tweets.js", "window.YTD.tweets.part0 = []")
        zf.writestr("assets/logo.png", "x")

    cfg = Config(search=SearchConfig(roots=[str(tmp_path)], exclude_dirs=[]))
    entries = scan_roots(cfg)
    types = {(e.source_type, e.zip_member) for e in entries}
    assert ("twitter_archive", None) in types
    assert ("chat_export", None) in types
    assert ("chrome_export", None) in types
    assert ("my_activity", None) in types
    assert ("twitter_archive_zip", None) in types
    assert ("twitter_archive", "data/like.js") in types
    assert ("twitter_archive", "data/tweets.js") in types
    assert not any(m and m.endswith(".png") for _, m in types)


def test_scan_zip_bad_zip(tmp_path):
    bad = tmp_path / "twitter-corrupt.zip"
    bad.write_text("this is not a zip")
    entries = scan_zip(bad)
    assert entries and entries[0].status == "unsupported"


def test_icloud_placeholder_name():
    p = Path("/x/.history_2022.csv.icloud")
    assert real_path_for_placeholder(p) == Path("/x/history_2022.csv")


def test_missing_report_and_datasources(tmp_path):
    (tmp_path / "like.js").write_text("window.YTD.like.part0 = []")
    cfg = Config(search=SearchConfig(roots=[str(tmp_path)], exclude_dirs=[]))
    manifest = build_manifest(scan_roots(cfg))
    report = missing_report(manifest)
    assert report["Twitter/X archive"] is True
    assert report["Reddit GDPR export"] is False
    out = tmp_path / "DATA_SOURCES.md"
    write_data_sources_md(manifest, out)
    text = out.read_text()
    assert "[FOUND]" in text and "[MISSING]" in text
    assert "—" not in text  # no em dashes in generated docs
