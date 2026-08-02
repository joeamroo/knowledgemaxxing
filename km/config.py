"""Configuration loading: config.yaml + .env."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field


class SearchConfig(BaseModel):
    roots: list[str] = Field(default_factory=lambda: ["~/Downloads", "~/Documents", "~/Desktop"])
    extra_roots: list[str] = Field(default_factory=list)
    exclude_dirs: list[str] = Field(
        default_factory=lambda: [
            "node_modules", ".git", ".venv", "venv", "env", "site-packages",
            "__pycache__", "Library", "Applications", "vendor", "dist", "build",
        ]
    )
    icloud_root: str = "~/Library/Mobile Documents/com~apple~CloudDocs"
    scan_icloud: bool = True
    scan_gdrive_mount: bool = True

    def all_roots(self) -> list[Path]:
        return [Path(r).expanduser() for r in self.roots + self.extra_roots]


class Usernames(BaseModel):
    x: str = ""
    reddit: str = ""
    hn: str = ""


class ClassificationConfig(BaseModel):
    model: str = "claude-sonnet-4-6"
    batch_size: int = 40
    prompt_version: str = "v1"
    categories: list[str] = Field(
        default_factory=lambda: [
            "anecdote", "interesting_fact", "thread", "joke", "link_to_essay",
            "quote", "tool_or_resource", "hot_take", "contrarian", "list",
            "personal", "other",
        ]
    )


class EmbeddingConfig(BaseModel):
    backend: str = "local"
    # bge-base: 768-dim, meaningfully better passage recall than bge-small.
    # Changing this triggers an automatic vector-table rebuild + re-embed.
    model: str = "BAAI/bge-base-en-v1.5"
    api_provider: str = ""


class NetworkConfig(BaseModel):
    resolve_links: bool = False


class Config(BaseModel):
    search: SearchConfig = Field(default_factory=SearchConfig)
    usernames: Usernames = Field(default_factory=Usernames)
    column_mappings: dict[str, dict] = Field(default_factory=dict)
    domains_file: str = "domains.yaml"
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    grok_export_path: str = ""
    network: NetworkConfig = Field(default_factory=NetworkConfig)

    project_root: Path = Field(default_factory=Path.cwd, exclude=True)

    @property
    def data_dir(self) -> Path:
        d = self.project_root / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        override = os.environ.get("KM_DB")
        if override:
            return Path(override).expanduser()
        return self.data_dir / "knowledge.db"

    @property
    def exports_dir(self) -> Path:
        d = self.project_root / "exports"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def anthropic_api_key(self) -> Optional[str]:
        return os.environ.get("ANTHROPIC_API_KEY")

    def load_domains(self) -> dict:
        p = self.project_root / self.domains_file
        if p.exists():
            return yaml.safe_load(p.read_text()) or {}
        return {}


def load_config(root: Optional[Path] = None) -> Config:
    root = root or Path.cwd()
    load_dotenv(root / ".env")
    cfg_path = root / "config.yaml"
    data = {}
    if cfg_path.exists():
        data = yaml.safe_load(cfg_path.read_text()) or {}
    cfg = Config.model_validate(data)
    cfg.project_root = root
    return cfg
