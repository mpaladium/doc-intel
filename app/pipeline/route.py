"""route.plan — page-class x content-type -> extractor, via the OWNERSHIP map.

Deterministic priority-table lookup (AGENTS.md §1.11): disagreement about which
extractor owns a region is never resolved ad hoc at the call site, it's resolved
by extending `app/config/ownership.yaml`. This iteration's table always resolves
to Docling (the only extractor wired in), but the lookup path is real so adding
MinerU/Surya later is a config change.
"""

from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_OWNERSHIP_PATH = Path(__file__).parents[1] / "config" / "ownership.yaml"

ContentType = str  # "layout" | "table" | "equation" | "text"


class Ownership:
    def __init__(self, owners: dict[str, dict[str, list[str]]], version: str):
        self.owners = owners
        self.version = version

    @classmethod
    def load(cls, path: str | Path = DEFAULT_OWNERSHIP_PATH) -> "Ownership":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(owners=data["owners"], version=data["version"])

    def engine_for(self, content_type: ContentType, page_class: str) -> str:
        """Return the top-priority engine for this (content_type, page_class) pair."""
        try:
            candidates = self.owners[content_type][page_class]
        except KeyError as exc:
            raise ValueError(
                f"no OWNERSHIP entry for content_type={content_type!r} page_class={page_class!r}"
            ) from exc
        if not candidates:
            raise ValueError(f"empty OWNERSHIP priority list for {content_type}/{page_class}")
        return candidates[0]
