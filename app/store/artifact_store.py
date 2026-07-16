"""artifact.put / artifact.exists -- the ONLY state ingestion-engine touches,
and it doesn't own it (ARCHITECTURE.md §0). Pure `PUT(key, bytes)` / `GET(key,
bytes)` over a filesystem-backed, content-addressed volume. Key =
sha256(pdf) + pipeline_version, so replaying the same PDF through the same
pipeline version always converges on the same key -- no coordination needed
between replicas, no job table, "done" is answered by existence, not status.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from canonical_schema import CanonicalEdition


def compute_key_from_digest(digest_hex: str, pipeline_version: str) -> str:
    return f"{digest_hex}+{pipeline_version}"


def compute_key(pdf_bytes: bytes, pipeline_version: str) -> str:
    return compute_key_from_digest(hashlib.sha256(pdf_bytes).hexdigest(), pipeline_version)


class ArtifactStore:
    """Filesystem-backed content-addressed store (TECHSTACK.md: single-node
    default; swap for an S3-compatible client later without changing this
    interface). Every write is a rename-from-temp so a killed-mid-write
    replica never leaves a partial file behind for a concurrent reader to see
    (ARCHITECTURE.md §0's "kill any replica mid-request, retry is safe")."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _edition_dir(self, key: str) -> Path:
        return self.root / key

    def _edition_path(self, key: str) -> Path:
        return self._edition_dir(key) / "edition.json"

    def _pages_dir(self, key: str) -> Path:
        return self._edition_dir(key) / "pages"

    def exists(self, key: str) -> bool:
        return self._edition_path(key).exists()

    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
        tmp_path.write_bytes(data)
        os.replace(tmp_path, path)  # atomic on the same filesystem

    def put_edition(self, key: str, edition: CanonicalEdition) -> None:
        self._atomic_write(self._edition_path(key), edition.model_dump_json(indent=2).encode())

    def get_edition(self, key: str) -> CanonicalEdition | None:
        path = self._edition_path(key)
        if not path.exists():
            return None
        return CanonicalEdition.model_validate_json(path.read_text())

    def edition_path(self, key: str) -> Path | None:
        """The on-disk path of a cached edition.json, for callers (e.g.
        scripts/verify_extraction.py) that need to point a subprocess at the
        file rather than the in-memory object. None if not cached."""
        path = self._edition_path(key)
        return path if path.exists() else None

    def put_page_image(self, key: str, page_no: int, png_bytes: bytes) -> None:
        self._atomic_write(self._pages_dir(key) / f"{page_no}.png", png_bytes)

    def page_image_path(self, key: str, page_no: int) -> Path | None:
        path = self._pages_dir(key) / f"{page_no}.png"
        return path if path.exists() else None

    def list_page_numbers(self, key: str) -> list[int]:
        pages_dir = self._pages_dir(key)
        if not pages_dir.exists():
            return []
        return sorted(int(p.stem) for p in pages_dir.glob("*.png"))
