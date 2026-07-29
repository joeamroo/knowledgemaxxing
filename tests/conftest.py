import sqlite3
from pathlib import Path

import pytest

from km.config import Config
from km.models import ManifestEntry
from km.parsers.base import ParseContext

FIXTURES = Path(__file__).parent / "fixtures"


def make_ctx(name: str, path: str | None = None, note: str | None = None,
             zip_member: str | None = None, config: Config | None = None) -> ParseContext:
    entry = ManifestEntry(
        path=path or str(FIXTURES / name), source_type="test",
        note=note, zip_member=zip_member,
    )
    return ParseContext(entry=entry, config=config or Config())


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


@pytest.fixture
def chrome_history_db(tmp_path):
    """Fabricate a minimal live-Chrome History SQLite file."""
    db_path = tmp_path / "History"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE urls(id INTEGER PRIMARY KEY, url TEXT, title TEXT,
                          visit_count INTEGER, last_visit_time INTEGER);
        CREATE TABLE visits(id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER,
                            transition INTEGER);
        INSERT INTO urls VALUES
          (1, 'https://guzey.com/why-blog/', 'Why you should start a blog', 2, 13217370610000000),
          (2, 'https://gwern.net/', 'Gwern.net', 1, 13217370620000000),
          (3, 'chrome://settings/', 'Settings', 1, 13217370630000000);
        INSERT INTO visits VALUES
          (1, 1, 13217370600000000, 0),
          (2, 1, 13217370610000000, 0),
          (3, 2, 13217370620000000, 0),
          (4, 3, 13217370630000000, 0);
        """
    )
    conn.commit()
    conn.close()
    return db_path
