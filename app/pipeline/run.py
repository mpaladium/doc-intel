"""process_pdf -- the single place that turns PDF bytes into a stored
CanonicalEdition + rasterized page images. Shared by the HTTP API
(app/api.py) and the batch CLI (app/cli/evaluate_dir.py) so there is exactly
one pipeline entrypoint, never two call sites that could drift apart.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from app.pipeline import quarantine
from app.pipeline.assemble import assemble
from app.store.artifact_store import ArtifactStore, compute_key
from app.store.rasterize import rasterize
from app.version import PIPELINE_VERSION

Status = Literal["quarantined", "already_processed", "processed"]


@dataclass(frozen=True)
class ProcessResult:
    key: str
    status: Status
    cause: str | None = None  # set only for status == "quarantined"


def process_pdf(pdf_bytes: bytes, store: ArtifactStore) -> ProcessResult:
    """Idempotent: safe to call twice for the same bytes (ARCHITECTURE.md §0)
    -- a second call is a fast existence check, not a reprocess."""
    q = quarantine.check(pdf_bytes)
    if not q.ok:
        return ProcessResult(key="", status="quarantined", cause=q.cause)

    key = compute_key(pdf_bytes, PIPELINE_VERSION)
    if store.exists(key):
        return ProcessResult(key=key, status="already_processed")

    source_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    edition = assemble(pdf_bytes, source_sha256=source_sha256, ocr_enabled=False)

    raster = rasterize(pdf_bytes)
    edition.pipeline_provenance["page_sizes"] = {
        str(p): list(size) for p, size in raster.page_sizes.items()
    }
    edition.pipeline_provenance["raster_dpi"] = raster.dpi

    store.put_edition(key, edition)
    for page_no, png_bytes in raster.images.items():
        store.put_page_image(key, page_no, png_bytes)

    return ProcessResult(key=key, status="processed")
