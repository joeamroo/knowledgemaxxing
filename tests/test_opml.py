"""Tests for OPML export of discovered feeds."""
import xml.etree.ElementTree as ET

from km.db import get_db
from km.feed import export_opml


def _db_with_feeds():
    conn = get_db(":memory:")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS feeds(domain TEXT, feed_url TEXT, last_fetched TEXT, ok INTEGER)"
    )
    conn.executemany(
        "INSERT INTO feeds(domain, feed_url, ok) VALUES (?,?,?)",
        [
            ("gwern.net", "https://gwern.net/index.xml", 1),
            ("guzey.com", "https://guzey.com/feed.xml", 1),
            ("broken.example", None, 1),      # no feed url, skipped
            ("dead.example", "https://dead.example/rss", 0),  # not ok, skipped
            ("a&b.com", "https://a&b.com/feed", 1),  # needs xml escaping
        ],
    )
    conn.commit()
    return conn


def test_export_opml_writes_valid_xml(tmp_path):
    conn = _db_with_feeds()
    out = tmp_path / "feeds.opml"
    n = export_opml(conn, out)

    assert n == 3  # only ok feeds with a url
    tree = ET.parse(out)  # raises if the XML (incl. escaping) is malformed
    outlines = tree.findall(".//outline")
    assert len(outlines) == 3
    urls = {o.get("xmlUrl") for o in outlines}
    assert "https://gwern.net/index.xml" in urls
    # escaped ampersand round-trips back to a literal & when parsed
    assert "https://a&b.com/feed" in urls


def test_export_opml_empty(tmp_path):
    conn = get_db(":memory:")  # schema already includes an empty feeds table
    out = tmp_path / "feeds.opml"
    assert export_opml(conn, out) == 0
    ET.parse(out)  # still valid, just empty body
