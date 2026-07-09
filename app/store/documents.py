"""documents -- read-only listing of the input PDFs directory (DOCS_DIR).

Like the artifact store, this is a dumb, re-derivable source (ARCHITECTURE.md
§0): nothing here is state the service owns. Any replica, or the batch CLI
(`app/cli/evaluate_dir.py`), gets the same listing by re-reading the same two
directories -- DOCS_DIR for what exists, the artifact store for what's ready.
Content hashes are computed by streaming the file rather than loading it
fully into memory, since a listing request shouldn't need to hold every PDF
in DOCS_DIR in RAM at once to answer "is X ready?".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from app.store.artifact_store import ArtifactStore, compute_key_from_digest
from app.version import PIPELINE_VERSION

_HASH_CHUNK_SIZE = 1 << 20  # 1 MiB


def discover_pdf_paths(docs_dir: Path) -> list[Path]:
    return sorted(p for p in docs_dir.rglob("*.pdf") if p.is_file())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class DocumentEntry:
    filename: str
    relative_path: str
    size_bytes: int
    key: str
    ready: bool


def list_documents(docs_dir: Path, store: ArtifactStore) -> list[DocumentEntry]:
    entries: list[DocumentEntry] = []
    for path in discover_pdf_paths(docs_dir):
        key = compute_key_from_digest(sha256_file(path), PIPELINE_VERSION)
        entries.append(DocumentEntry(
            filename=path.name,
            relative_path=str(path.relative_to(docs_dir)),
            size_bytes=path.stat().st_size,
            key=key,
            ready=store.exists(key),
        ))
    return entries
