"""Tests for OPML import (the export's inverse)."""
from km.db import get_db
from km.feed import export_opml, import_opml

OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>my reader</title></head>
  <body>
    <outline text="Tech" title="Tech">
      <outline type="rss" text="Paul Graham" xmlUrl="http://paulgraham.com/rss.html" htmlUrl="http://www.paulgraham.com/"/>
      <outline type="rss" text="Gwern" xmlUrl="https://gwern.net/atom.xml"/>
    </outline>
    <outline text="folder without feeds" title="empty"/>
  </body>
</opml>
"""


def test_import_walks_nested_outlines(tmp_path):
    conn = get_db(":memory:")
    opml = tmp_path / "reader.opml"
    opml.write_text(OPML)

    result = import_opml(conn, opml)

    assert result == {"added": 2, "skipped": 0}
    rows = {r["domain"]: r["feed_url"] for r in conn.execute("SELECT * FROM feeds")}
    # htmlUrl host wins, www. stripped
    assert rows["paulgraham.com"] == "http://paulgraham.com/rss.html"
    # falls back to the feed URL host
    assert rows["gwern.net"] == "https://gwern.net/atom.xml"
    assert all(r["ok"] == 1 for r in conn.execute("SELECT ok FROM feeds"))


def test_reimport_never_clobbers_existing(tmp_path):
    conn = get_db(":memory:")
    conn.execute(
        "INSERT INTO feeds(domain, feed_url, ok) VALUES ('gwern.net', 'https://gwern.net/original.xml', 1)")
    conn.commit()
    opml = tmp_path / "reader.opml"
    opml.write_text(OPML)

    result = import_opml(conn, opml)

    assert result == {"added": 1, "skipped": 1}
    row = conn.execute("SELECT feed_url FROM feeds WHERE domain='gwern.net'").fetchone()
    assert row["feed_url"] == "https://gwern.net/original.xml"


def test_round_trip_with_export(tmp_path):
    conn = get_db(":memory:")
    opml_in = tmp_path / "reader.opml"
    opml_in.write_text(OPML)
    import_opml(conn, opml_in)

    opml_out = tmp_path / "out.opml"
    n = export_opml(conn, opml_out)
    assert n == 2

    conn2 = get_db(":memory:")
    result = import_opml(conn2, opml_out)
    assert result["added"] == 2
