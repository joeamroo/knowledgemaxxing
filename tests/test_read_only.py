"""Tests for km ui --read-only (server-enforced demo/share mode)."""
import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from km.config import Config
from km.web.server import create_app


def _client(tmp_path, read_only, monkeypatch=None):
    cfg = Config()
    cfg.project_root = tmp_path  # fresh empty DB under tmp_path/data
    return TestClient(create_app(cfg, read_only=read_only))


def test_meta_reports_mode(tmp_path):
    assert _client(tmp_path, True).get("/api/meta").json() == {"read_only": True}
    assert _client(tmp_path, False).get("/api/meta").json() == {"read_only": False}


def test_read_only_blocks_all_mutations(tmp_path):
    client = _client(tmp_path, True)
    for method, url in [
        ("post", "/api/tasks"),
        ("post", "/api/ask"),
        ("post", "/api/sync"),
        ("patch", "/api/items/1"),
        ("delete", "/api/collections/1"),
        ("post", "/api/talk/message"),
    ]:
        resp = client.request(method.upper(), url, json={})
        assert resp.status_code == 403, f"{method} {url} not blocked"
        assert "read-only" in resp.json()["detail"]


def test_read_only_still_serves_reads(tmp_path):
    client = _client(tmp_path, True)
    assert client.get("/api/items").status_code == 200
    assert client.get("/api/stats").status_code == 200


def test_writable_mode_allows_mutations(tmp_path):
    client = _client(tmp_path, False)
    resp = client.post("/api/tasks", json={"text": "a task"})
    assert resp.status_code == 200
