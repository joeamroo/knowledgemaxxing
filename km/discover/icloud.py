"""iCloud dataless-file handling.

Evicted files appear as .<name>.icloud placeholders. We list them in the
manifest as needs_download and only materialize with explicit approval,
never in bulk without asking.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from km.models import ManifestEntry


def real_path_for_placeholder(placeholder: Path) -> Path:
    """.<name>.icloud placeholder -> the path the real file will occupy."""
    name = placeholder.name
    if name.startswith(".") and name.endswith(".icloud"):
        return placeholder.with_name(name[1:-len(".icloud")])
    return placeholder


def materialize(placeholder: Path, timeout: int = 300) -> tuple[bool, str]:
    """Ask iCloud to download one evicted file via brctl. Returns (ok, message)."""
    target = real_path_for_placeholder(placeholder)
    try:
        proc = subprocess.run(
            ["brctl", "download", str(target)],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        return False, "brctl not found (is this macOS with iCloud Drive enabled?)"
    except subprocess.TimeoutExpired:
        return False, f"download timed out after {timeout}s"
    if proc.returncode != 0:
        return False, proc.stderr.strip() or "brctl download failed"
    return True, f"requested download of {target.name}; iCloud fetches it in the background"


def pending_downloads(entries: list[ManifestEntry]) -> list[ManifestEntry]:
    return [e for e in entries if e.status == "needs_download"]
