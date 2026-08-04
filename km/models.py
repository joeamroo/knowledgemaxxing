"""Shared pydantic models."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

ItemKind = Literal[
    "visit",
    "like",
    "retweet",
    "own_tweet",
    "bookmark_tweet",
    "chat_message",
    "chat_conversation",
    "bookmark",
    "saved_post",
    "saved_comment",
    "favorite",
    "upvote",
    "search_query",
    "note",
    "linked",
    "feed_post",
    # direct messages (X archive; local-only, never leaves the machine)
    "dm",
    # social graph snapshots: one item per account per relation, with an
    # occurrence per archive that still listed them
    "x_follower",
    "x_following",
    "x_blocked",
    "x_muted",
    "x_list",
]


class NormalizedItem(BaseModel):
    """The one record every parser and scraper emits."""

    kind: ItemKind
    dedupe_key: str
    url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    author: Optional[str] = None
    created_at: Optional[datetime] = None
    raw: dict = Field(default_factory=dict)
    # Which event this represents in this source (may differ from kind for merged items)
    occurrence_kind: str = ""
    occurrence_detail: Optional[str] = None  # e.g. Chrome profile name, zip member

    def model_post_init(self, __context: object) -> None:
        if not self.occurrence_kind:
            self.occurrence_kind = self.kind


class ManifestEntry(BaseModel):
    path: str
    source_type: str
    size: int = 0
    mtime: Optional[str] = None
    zip_member: Optional[str] = None
    status: Literal["ready", "needs_download", "needs_mapping", "unsupported"] = "ready"
    note: Optional[str] = None
    header_sample: Optional[str] = None

    @property
    def display_path(self) -> str:
        return f"{self.path}!{self.zip_member}" if self.zip_member else self.path


class Manifest(BaseModel):
    generated_at: str
    entries: list[ManifestEntry] = Field(default_factory=list)

    def by_type(self) -> dict[str, list[ManifestEntry]]:
        out: dict[str, list[ManifestEntry]] = {}
        for e in self.entries:
            out.setdefault(e.source_type, []).append(e)
        return out

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        return cls.model_validate_json(path.read_text())
