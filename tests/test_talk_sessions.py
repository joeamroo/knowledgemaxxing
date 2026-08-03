"""Tests for multi-tab session addressing in the talk layer."""
import json

from km.classify.talk import (
    list_sessions, load_history, resolve_session, save_session,
)


def _write(data_dir, persona, messages):
    return save_session(data_dir, persona, None, messages)


def test_save_names_include_seconds_no_collision(tmp_path):
    a = _write(tmp_path, "archivist", [{"role": "user", "content": "first tab"}])
    b = _write(tmp_path, "archivist", [{"role": "user", "content": "second tab"}])
    # same minute is fine; same second is the only collision window left
    assert a.name != b.name or load_history(a) == load_history(b)


def test_resolve_session_blocks_traversal(tmp_path):
    path = _write(tmp_path, "archivist", [{"role": "user", "content": "hi"}])
    assert resolve_session(tmp_path, path.name) == path
    assert resolve_session(tmp_path, "../" + path.name) is None
    assert resolve_session(tmp_path, "/etc/passwd") is None
    assert resolve_session(tmp_path, "nope.json") is None
    assert resolve_session(tmp_path, path.name.replace(".json", ".txt")) is None


def test_list_sessions_labels_and_order(tmp_path):
    _write(tmp_path, "therapist", [{"role": "user", "content": "about my sleep"}])
    newer = _write(tmp_path, "archivist", [
        {"role": "user", "content": "find that essay about compounding"},
        {"role": "assistant", "content": "found it"},
    ])
    import os
    import time
    os.utime(newer, (time.time() + 5, time.time() + 5))

    sessions = list_sessions(tmp_path)
    assert len(sessions) == 2
    assert sessions[0]["session"] == newer.name
    assert sessions[0]["persona"] == "archivist"
    assert sessions[0]["label"].startswith("find that essay")
    assert sessions[0]["messages"] == 2


def test_history_endpoint_session_param(tmp_path):
    import pytest

    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from km.config import Config
    from km.web.server import create_app

    cfg = Config()
    cfg.project_root = tmp_path
    path = _write(cfg.data_dir, "archivist", [{"role": "user", "content": "tab one"}])
    client = TestClient(create_app(cfg))

    r = client.get(f"/api/talk/history?session={path.name}")
    assert r.status_code == 200
    assert r.json()["messages"][0]["content"] == "tab one"
    assert client.get("/api/talk/history?session=..%2Fescape.json").status_code == 404

    listed = client.get("/api/talk/sessions").json()["sessions"]
    assert listed and listed[0]["session"] == path.name
