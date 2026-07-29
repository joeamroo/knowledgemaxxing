import pytest

fastapi = pytest.importorskip("fastapi")

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from km.config import Config
from km.db import get_db
from km.models import NormalizedItem
from km.store import add_source, upsert_item
from km.web.api import build_router


def make_app(tmp_path):
    cfg = Config()
    cfg.project_root = tmp_path
    # TestClient serves endpoints from a threadpool, same as km ui
    conn = get_db(":memory:", check_same_thread=False)
    sid, _ = add_source(conn, "twitter_archive", "scraper:test", "h")
    for i in range(3):
        upsert_item(
            conn,
            NormalizedItem(
                kind="like", dedupe_key=f"tweet:{i}",
                text=f"tweet number {i} about contradictory advice",
                url=f"https://twitter.com/i/web/status/{i}",
                created_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            ),
            sid,
        )
    app = fastapi.FastAPI()
    app.include_router(build_router(cfg, lambda: conn))
    return app, conn


def test_list_items_pagination(tmp_path):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/items", params={"page_size": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] == 2
    resp2 = client.get("/api/items", params={"page_size": 2, "cursor": 2})
    assert len(resp2.json()["items"]) == 1


def test_keyword_search_endpoint(tmp_path):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    resp = client.get("/api/items", params={"q": "contradictory advice", "mode": "keyword"})
    assert len(resp.json()["items"]) == 3


def test_item_detail_and_patch(tmp_path):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    item = client.get("/api/items").json()["items"][0]
    detail = client.get(f"/api/items/{item['id']}").json()
    assert detail["occurrences"]
    patched = client.patch(
        f"/api/items/{item['id']}",
        json={"starred": True, "note": "great one", "category_override": "anecdote"},
    ).json()
    assert patched["starred"] is True
    assert patched["note"] == "great one"
    assert patched["category"] == "anecdote"
    # patch again without fields: previous edits survive
    patched2 = client.patch(f"/api/items/{item['id']}", json={"archived": True}).json()
    assert patched2["starred"] is True and patched2["archived"] is True


def test_facets_and_stats(tmp_path):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    facets = client.get("/api/facets").json()
    assert facets["kinds"] == {"like": 3}
    assert facets["sources"] == {"twitter_archive": 3}
    stats = client.get("/api/stats").json()
    assert stats["total_items"] == 3
    assert stats["items_per_month"]


def test_random_and_ask_and_export(tmp_path):
    app, _ = make_app(tmp_path)
    client = TestClient(app)
    assert client.get("/api/random").status_code == 200
    ask = client.post("/api/ask", json={"query": "contradictory advice"}).json()
    assert ask["mode"] == "hybrid"
    assert ask["candidates"]
    ids = [c["id"] for c in ask["candidates"]]
    exported = client.post("/api/export", json={"ids": ids, "filename": "sel.md"}).json()
    assert exported["count"] == len(ids)
    assert (tmp_path / "exports" / "sel.md").exists()
