"""Parser contract.

Every parser is a function `parse(data: bytes, ctx: ParseContext) ->
Iterator[NormalizedItem]`. Ingest handles reading files and zip members,
so parsers stay pure and offline-testable. The one exception is live
Chrome history (SQLite), which needs a real path: see chrome_history.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from km.config import Config
from km.models import ManifestEntry


class NeedsMappingError(Exception):
    """Raised when a flexible CSV/JSON export's columns cannot be mapped."""


@dataclass
class ParseContext:
    entry: ManifestEntry
    config: Config = field(default_factory=Config)

    @property
    def detail(self) -> Optional[str]:
        parts = [p for p in (self.entry.note, self.entry.zip_member) if p]
        return "; ".join(parts) if parts else None

    @property
    def member_name(self) -> str:
        """Basename that identifies the file inside an archive or on disk."""
        name = self.entry.zip_member or self.entry.path
        return name.rsplit("/", 1)[-1]
