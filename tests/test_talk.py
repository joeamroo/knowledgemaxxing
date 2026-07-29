import json

from km.classify.talk import TALK_PERSONAS, build_system, load_history, save_session
from km.db import get_db
from km.models import NormalizedItem
from km.store import add_source, upsert_item


def test_personas_and_system_blocks():
    conn = get_db(":memory:")
    sid, _ = add_source(conn, "apple_notes", "app:Notes", None)
    upsert_item(conn, NormalizedItem(kind="note", dedupe_key="apple-note:1",
                                     title="2025", text="this year broke me a little"), sid)
    for persona in ("companion", "analyst", "harsh"):
        system = build_system(conn, persona)
        assert system[0]["text"] == TALK_PERSONAS[persona]
        assert system[1]["cache_control"] == {"type": "ephemeral"}
        assert "this year broke me" in system[1]["text"]
        assert "—" not in system[0]["text"]


def test_session_roundtrip(tmp_path):
    messages = [
        {"role": "user", "content": "2025 was brutal"},
        {"role": "assistant", "content": "Tell me where it started."},
    ]
    path = save_session(tmp_path, "companion", None, messages)
    assert load_history(path) == messages
    transcript = path.with_suffix(".md").read_text()
    assert "You:" in transcript and "brutal" in transcript
    # resume appends to the same file
    messages.append({"role": "user", "content": "with her"})
    path2 = save_session(tmp_path, "companion", path, messages)
    assert path2 == path and len(load_history(path)) == 3
