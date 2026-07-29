"""Optional Google Drive API discovery (no local mount required).

Entirely optional: the core tool works without any Google Cloud setup.
Requires: uv add google-api-python-client google-auth-oauthlib, plus an
OAuth client credentials.json in the project root (see README).
"""
from __future__ import annotations

from pathlib import Path

from km.config import Config
from km.models import ManifestEntry

_QUERIES = [
    "name contains 'twitter-' and name contains '.zip'",
    "name contains 'takeout-'",
    "name = 'conversations.json'",
    "name contains 'history' and (name contains '.csv' or name contains '.json')",
    "name = 'BrowserHistory.json'",
    "name contains 'MyActivity'",
    "name contains 'saved_posts'",
]

SETUP_HELP = """Google Drive API mode needs one-time setup:
1. console.cloud.google.com: create a project, enable the Drive API
2. Create OAuth client ID (Desktop app), download credentials.json to the project root
3. uv add google-api-python-client google-auth-oauthlib
4. Re-run km discover --gdrive-api (a browser window will ask for read-only consent)
Downloads land in data/cache/gdrive/ and appear in the manifest."""


def scan_gdrive_api(cfg: Config) -> tuple[list[ManifestEntry], str | None]:
    """Search Drive by filename patterns and download matches to a local cache.

    Returns (entries, error_message). error_message is set when setup is
    incomplete; the caller prints SETUP_HELP and moves on.
    """
    creds_path = cfg.project_root / "credentials.json"
    if not creds_path.exists():
        return [], "credentials.json not found in project root"
    try:
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        from googleapiclient.http import MediaIoBaseDownload  # type: ignore
    except ImportError:
        return [], "google client libraries not installed"

    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    token_path = cfg.data_dir / "gdrive_token.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    service = build("drive", "v3", credentials=creds)
    cache = cfg.data_dir / "cache" / "gdrive"
    cache.mkdir(parents=True, exist_ok=True)
    entries: list[ManifestEntry] = []
    seen_ids: set[str] = set()
    for q in _QUERIES:
        resp = service.files().list(
            q=f"({q}) and trashed = false",
            fields="files(id, name, size, modifiedTime)", pageSize=50,
        ).execute()
        for f in resp.get("files", []):
            if f["id"] in seen_ids:
                continue
            seen_ids.add(f["id"])
            local = cache / f["name"]
            if not local.exists():
                import io

                request = service.files().get_media(fileId=f["id"])
                with open(local, "wb") as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
            from km.discover.scanner import classify_path, scan_zip

            e = classify_path(local)
            if e:
                e.note = f"{e.note + ', ' if e.note else ''}downloaded from Google Drive API"
                entries.append(e)
                if e.source_type in ("twitter_archive_zip", "takeout_zip"):
                    entries.extend(scan_zip(local))
    return entries, None
