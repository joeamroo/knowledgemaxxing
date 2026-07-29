"""FastAPI app factory: API + pre-built frontend static assets.

Binds 127.0.0.1 only (enforced by km ui), no auth, no external calls.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from km.config import Config
from km.db import get_db
from km.web.api import build_router

STATIC_DIR = Path(__file__).parent / "static"


def create_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="km", docs_url=None, redoc_url=None, openapi_url=None)

    # one connection per app; SQLite in WAL mode handles the UI's read-heavy load
    conn = get_db(cfg.db_path, check_same_thread=False)
    conn.execute("PRAGMA busy_timeout=5000")

    def get_conn() -> sqlite3.Connection:
        return conn

    app.include_router(build_router(cfg, get_conn))

    if (STATIC_DIR / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            candidate = STATIC_DIR / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app
