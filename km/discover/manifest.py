"""Manifest building and terminal rendering."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

from km.models import Manifest, ManifestEntry

_TYPE_LABELS = {
    "twitter_archive_zip": "Twitter archive (zip)",
    "twitter_archive": "Twitter archive file",
    "takeout_zip": "Google Takeout (zip)",
    "takeout_browser": "Takeout Chrome history",
    "my_activity": "Google My Activity (JSON)",
    "my_activity_html": "Google My Activity (HTML)",
    "chat_export": "AI chat export (ChatGPT/Claude)",
    "chrome_export": "Chrome history export (CSV/JSON)",
    "chrome_live_history": "Live Chrome history (SQLite)",
    "chrome_bookmarks": "Chrome bookmarks",
    "bookmarks_html": "Bookmarks HTML export",
    "reddit_gdpr": "Reddit GDPR export",
    "pocket": "Pocket export",
    "pocket_csv": "Pocket CSV",
    "instapaper": "Instapaper CSV",
    "onetab": "OneTab export",
    "generic": "Generic URL file (needs approval)",
}


def build_manifest(entries: list[ManifestEntry]) -> Manifest:
    return Manifest(
        generated_at=datetime.now(timezone.utc).isoformat(), entries=entries
    )


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def render_manifest(manifest: Manifest, console: Console) -> None:
    table = Table(title="km discover: candidate source files", show_lines=False)
    table.add_column("Type", style="cyan", no_wrap=True)
    table.add_column("Path", overflow="fold")
    table.add_column("Size", justify="right", style="green")
    table.add_column("Modified", no_wrap=True)
    table.add_column("Status", style="yellow")
    for e in sorted(manifest.entries, key=lambda e: (e.source_type, e.path)):
        label = _TYPE_LABELS.get(e.source_type, e.source_type)
        mtime = (e.mtime or "")[:10]
        status = e.status if e.status != "ready" else ""
        note = f" ({e.note})" if e.note else ""
        table.add_row(label, e.display_path + note, _fmt_size(e.size), mtime, status)
    console.print(table)
    console.print(
        f"[bold]{len(manifest.entries)}[/bold] candidate files. "
        "Review manifest.json, then run [bold]km ingest[/bold]."
    )


def save_manifest(manifest: Manifest, path: Path) -> None:
    manifest.save(path)
