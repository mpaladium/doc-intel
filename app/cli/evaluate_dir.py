"""Batch-evaluate every PDF under a directory: recompute each file's
content-address key, skip anything already in the artifact store, and run
the pipeline (`app.pipeline.run.process_pdf`) for the rest.

Meant to be launched as a detached background job (see
`scripts/evaluate_dir.sh`) ahead of browsing results in the verification UI
(`GET /`) -- it writes to the same `ARTIFACT_STORE_PATH` the API server reads
from, so results just show up there once done. No running server is required
to run this; it calls the pipeline directly, same as the API does.

Usage:
    uv run python -m app.cli.evaluate_dir /path/to/pdfs
    uv run python -m app.cli.evaluate_dir /path/to/pdfs --workers 2
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.pipeline.run import process_pdf
from app.store.artifact_store import ArtifactStore
from app.store.documents import discover_pdf_paths
from app.version import PIPELINE_VERSION

log = logging.getLogger("evaluate_dir")


def _process_one(path: Path, store: ArtifactStore):
    pdf_bytes = path.read_bytes()
    return path, process_pdf(pdf_bytes, store)


def run(docs_dir: Path, store: ArtifactStore, workers: int = 1) -> int:
    """Returns the number of documents that failed with an exception (0 = clean run)."""
    pdf_paths = discover_pdf_paths(docs_dir)
    if not pdf_paths:
        log.warning("no PDFs found under %s", docs_dir)
        return 0

    log.info("found %d PDF(s) under %s -- pipeline_version=%s workers=%d",
              len(pdf_paths), docs_dir, PIPELINE_VERSION, workers)

    counts = {"processed": 0, "already_processed": 0, "quarantined": 0, "failed": 0}
    start = time.time()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_one, p, store): p for p in pdf_paths}
        for i, future in enumerate(as_completed(futures), start=1):
            path = futures[future]
            try:
                _, result = future.result()
            except Exception:
                counts["failed"] += 1
                log.exception("[%d/%d] FAILED: %s", i, len(pdf_paths), path)
                continue

            counts[result.status] += 1
            if result.status == "processed":
                log.info("[%d/%d] processed: %s", i, len(pdf_paths), path.name)
            elif result.status == "already_processed":
                log.info("[%d/%d] already processed: %s", i, len(pdf_paths), path.name)
            elif result.status == "quarantined":
                log.warning("[%d/%d] quarantined (%s): %s", i, len(pdf_paths), result.cause, path.name)

    elapsed = time.time() - start
    log.info(
        "done in %.1fs -- processed=%d already_processed=%d quarantined=%d failed=%d",
        elapsed, counts["processed"], counts["already_processed"], counts["quarantined"], counts["failed"],
    )
    return counts["failed"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docs_dir", type=Path, help="directory containing PDFs (searched recursively)")
    parser.add_argument("--artifact-store", type=Path, default=None,
                         help="defaults to $ARTIFACT_STORE_PATH or ./data/artifacts -- must match "
                              "the running API server's store for results to show up in the UI")
    parser.add_argument("--workers", type=int, default=1,
                         help="concurrent pipeline runs (default 1 -- matches the single-GPU "
                              "'one document at a time' assumption; see AGENTS.md §3). Raise "
                              "only if the API server isn't processing concurrently too, or you "
                              "risk oversubscribing a shared GPU.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.docs_dir.is_dir():
        log.error("not a directory: %s", args.docs_dir)
        return 1

    store_path = args.artifact_store or Path(os.environ.get("ARTIFACT_STORE_PATH", "./data/artifacts"))
    store = ArtifactStore(store_path)
    log.info("artifact store: %s", store_path)

    failed = run(args.docs_dir, store, workers=args.workers)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
