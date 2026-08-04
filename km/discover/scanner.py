"""Filesystem discovery: local roots, live Chrome, iCloud, Google Drive mount, zips."""
from __future__ import annotations

import os
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from km.config import Config
from km.discover.patterns import classify_name, sniff_file
from km.models import ManifestEntry

# Chrome's Bookmarks file has no extension, matched by exact path segment instead
_CHROME_DIR = Path("~/Library/Application Support/Google/Chrome").expanduser()
_CLOUDSTORAGE = Path("~/Library/CloudStorage").expanduser()

_SKIP_SUFFIXES = {".app", ".photoslibrary", ".musiclibrary", ".framework"}
_MAX_SNIFF_SIZE = 512 * 1024 * 1024  # never sniff files larger than 512 MB
# dependency/build trees that must never be walked, whatever the config says
_ALWAYS_EXCLUDE_DIRS = {
    "node_modules", ".venv", "venv", "env", "site-packages", "__pycache__",
    ".git", "vendor", "Pods", "DerivedData",
}


def _entry(path: Path, source_type: str, **kw) -> ManifestEntry:
    try:
        st = path.stat()
        size, mtime = st.st_size, datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        size, mtime = 0, None
    return ManifestEntry(path=str(path), source_type=source_type, size=size, mtime=mtime, **kw)


def _walk(root: Path, exclude_dirs: list[str]) -> Iterator[Path]:
    excludes = set(exclude_dirs) | _ALWAYS_EXCLUDE_DIRS
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if d not in excludes and not d.startswith(".") and Path(d).suffix not in _SKIP_SUFFIXES
            # a dir containing pyvenv.cfg is a virtualenv whatever it is named
            and not (Path(dirpath) / d / "pyvenv.cfg").exists()
        ]
        for name in filenames:
            yield Path(dirpath) / name


def _mdfind_candidates(root: Path) -> list[Path]:
    """Spotlight-assisted candidate list. Empty on any failure."""
    queries = [
        "kMDItemFSName == '*.zip'c",
        "kMDItemFSName == '*.js'c",
        "kMDItemFSName == '*history*'c",
        "kMDItemFSName == 'conversations.json'c",
        "kMDItemFSName == 'MyActivity*'c",
        "kMDItemFSName == 'bookmarks*'c",
        "kMDItemFSName == 'saved_*.csv'c",
    ]
    found: list[Path] = []
    for q in queries:
        try:
            out = subprocess.run(
                ["mdfind", "-onlyin", str(root), q],
                capture_output=True, text=True, timeout=30,
            )
            if out.returncode == 0:
                found.extend(Path(p) for p in out.stdout.splitlines() if p.strip())
        except (OSError, subprocess.TimeoutExpired):
            return []
    return found


def scan_zip(path: Path) -> list[ManifestEntry]:
    """List interesting members of a zip without extracting it."""
    entries: list[ManifestEntry] = []
    try:
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                member_type = classify_name(info.filename)
                if member_type in (None, "twitter_archive_zip", "takeout_zip"):
                    continue
                entries.append(
                    ManifestEntry(
                        path=str(path), source_type=member_type, size=info.file_size,
                        zip_member=info.filename,
                        mtime=datetime(*info.date_time, tzinfo=timezone.utc).isoformat()
                        if info.date_time[0] >= 1980 else None,
                    )
                )
    except (zipfile.BadZipFile, OSError) as exc:
        entries.append(_entry(path, "zip", status="unsupported", note=f"unreadable zip: {exc}"))
    return entries


def classify_path(path: Path) -> Optional[ManifestEntry]:
    """Classify one regular file into a manifest entry, or None."""
    name = path.name
    if name == "Bookmarks" and path.parent.parent == _CHROME_DIR:
        return _entry(path, "chrome_bookmarks", note=f"profile: {path.parent.name}")
    source_type = classify_name(str(path))
    if source_type in ("twitter_archive_zip", "takeout_zip"):
        return _entry(path, source_type)
    if source_type:
        return _entry(path, source_type)
    if path.suffix.lower() in (".csv", ".json", ".txt"):
        try:
            if path.stat().st_size > _MAX_SNIFF_SIZE:
                return None
        except OSError:
            return None
        sniffed = sniff_file(path)
        if sniffed:
            stype, header = sniffed
            return _entry(
                path, stype, header_sample=header,
                note="flagged by content sniffing, approve before ingest"
                if stype == "generic" else None,
            )
    return None


def scan_roots(cfg: Config, extra_roots: Optional[list[Path]] = None) -> list[ManifestEntry]:
    """Scan configured local roots (walk + Spotlight supplement) and zips."""
    entries: dict[str, ManifestEntry] = {}
    roots = cfg.search.all_roots() + (extra_roots or [])
    # never treat km's own tree (test fixtures!) as source data
    project_root = str(cfg.project_root.resolve())
    for root in roots:
        if not root.exists():
            continue
        candidates: set[Path] = set(_mdfind_candidates(root))
        candidates.update(_walk(root, cfg.search.exclude_dirs))
        for path in sorted(candidates):
            if str(path.resolve()).startswith(project_root):
                continue
            if not path.is_file():
                continue
            e = classify_path(path)
            if e is None:
                continue
            key = e.display_path
            if key not in entries:
                entries[key] = e
                if e.source_type in ("twitter_archive_zip", "takeout_zip"):
                    for ze in scan_zip(path):
                        entries.setdefault(ze.display_path, ze)
    return list(entries.values())


def scan_chrome_live() -> list[ManifestEntry]:
    """Find live Chrome History SQLite files across every profile."""
    entries = []
    if not _CHROME_DIR.exists():
        return entries
    for profile_dir in sorted(_CHROME_DIR.iterdir()):
        history = profile_dir / "History"
        if history.is_file():
            entries.append(
                _entry(history, "chrome_live_history", note=f"profile: {profile_dir.name}")
            )
        bookmarks = profile_dir / "Bookmarks"
        if bookmarks.is_file():
            entries.append(
                _entry(bookmarks, "chrome_bookmarks", note=f"profile: {profile_dir.name}")
            )
    return entries


def scan_safari_live() -> list[ManifestEntry]:
    """Safari's History.db, which iCloud fills with iPhone/iPad visits too.

    Needs Full Disk Access for the terminal; unreadable means not granted.
    """
    history = Path.home() / "Library/Safari/History.db"
    if not history.is_file():
        return []
    try:
        with open(history, "rb") as fh:
            fh.read(16)
    except (PermissionError, OSError):
        return [_entry(history, "safari_history", status="unsupported",
                       note="grant Full Disk Access to your terminal to read Safari history")]
    return [_entry(history, "safari_history", note="includes iPhone visits synced via iCloud")]


def scan_icloud(cfg: Config) -> list[ManifestEntry]:
    """Scan iCloud Drive, flagging evicted .<name>.icloud placeholders."""
    root = Path(cfg.search.icloud_root).expanduser()
    if not cfg.search.scan_icloud or not root.exists():
        return []
    entries: list[ManifestEntry] = []
    for path in _walk(root, cfg.search.exclude_dirs):
        name = path.name
        if name.endswith(".icloud") and name.startswith("."):
            real_name = name[1:-len(".icloud")]
            stype = classify_name(real_name)
            if stype is None and Path(real_name).suffix.lower() in (".csv", ".json", ".txt"):
                stype = "generic"
            if stype:
                e = _entry(path, stype, status="needs_download",
                           note=f"evicted iCloud file, real name: {real_name}")
                entries.append(e)
            continue
        e = classify_path(path)
        if e:
            entries.append(e)
            if e.source_type in ("twitter_archive_zip", "takeout_zip"):
                entries.extend(scan_zip(path))
    return entries


def scan_gdrive_mount(cfg: Config) -> list[ManifestEntry]:
    """Scan Google Drive for Desktop mounts under ~/Library/CloudStorage."""
    if not cfg.search.scan_gdrive_mount or not _CLOUDSTORAGE.exists():
        return []
    entries: list[ManifestEntry] = []
    for mount in sorted(_CLOUDSTORAGE.glob("GoogleDrive-*")):
        for sub in ("My Drive", "Other computers"):
            base = mount / sub
            if not base.exists():
                continue
            for path in _walk(base, cfg.search.exclude_dirs):
                e = classify_path(path)
                if e:
                    e.note = f"{e.note + ', ' if e.note else ''}google drive: {sub}"
                    entries.append(e)
                    if e.source_type in ("twitter_archive_zip", "takeout_zip"):
                        entries.extend(scan_zip(path))
        # Shared drives live alongside My Drive
        shared = mount / "Shared drives"
        if shared.exists():
            for path in _walk(shared, cfg.search.exclude_dirs):
                e = classify_path(path)
                if e:
                    e.note = f"{e.note + ', ' if e.note else ''}google shared drive"
                    entries.append(e)
    return entries


def discover_all(cfg: Config) -> list[ManifestEntry]:
    """Full discovery pass in spec order: local, live Chrome, iCloud, Drive."""
    seen: dict[str, ManifestEntry] = {}
    entries = scan_roots(cfg) + scan_chrome_live() + scan_icloud(cfg) + scan_gdrive_mount(cfg)
    if cfg.grok_export_path:
        grok = Path(cfg.grok_export_path).expanduser()
        if grok.is_file():
            entries.append(_entry(grok, "grok_export", note="from config grok_export_path"))
    for entry in entries:
        seen.setdefault(entry.display_path, entry)
    return sorted(seen.values(), key=lambda e: (e.source_type, e.path))
