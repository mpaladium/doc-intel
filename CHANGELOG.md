# Changelog

All notable changes to `ingestion-engine` are recorded here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); this project doesn't
tag releases yet, so entries are grouped by work session instead of version.

## Unreleased

### Fixed
- `app/pipeline/extract_docling.py`: `PictureItem` (images/charts) has no
  `.text` attribute either, same class of bug as the `TableItem` fix in
  0.2.0 — raised an unhandled `AttributeError` (500) on any real-world PDF
  containing an image, surfaced via `POST /documents/{path}/parse` and
  `POST /parse`. The synthetic e2e fixture had a table but no image, so this
  shipped unnoticed. Fixed generally: per-item building now goes through
  `_content_builder()`, which uses `getattr(item, "text", None)` instead of
  direct attribute access, so any other text-less item type Docling adds
  later degrades to "no text" rather than crashing the request. Regression
  tests in `tests/test_extract_docling.py` exercise `TableItem`- and
  `PictureItem`-shaped stub items directly (no full Docling conversion
  needed) to catch this class of bug without depending on Docling's layout
  model actually classifying a given test image as a picture.

### Added
- Document picker: point the server at a directory of PDFs (`DOCS_DIR`) and
  browse/parse them from the browser instead of the API. `GET /` renders
  every PDF found under `DOCS_DIR` (recursive) with a ready/not-processed
  status and a "parse now" action for unprocessed ones; `GET /documents`
  returns the same listing as JSON. Status is never cached by the
  service — `app/store/documents.py` recomputes it on every request by
  re-hashing files in `DOCS_DIR` and checking the artifact store, preserving
  the "no service-owned index" rule (`ARCHITECTURE.md` §0).
- `app/pipeline/run.py`: `process_pdf()` — the pipeline entrypoint factored
  out of `app/api.py` so the API and the new batch CLI share exactly one call
  site instead of two that could drift.
- `app/cli/evaluate_dir.py` + `scripts/evaluate_dir.sh` — batch-evaluate every
  PDF under a directory without a running server, writing to the same
  artifact store the API reads from. `evaluate_dir.sh` launches it detached
  (`nohup` + pid/log files under `data/eval-logs/`) so a large batch doesn't
  tie up the terminal; results show up in the document picker as they land.
  `--workers` / `EVAL_WORKERS` controls concurrency (default 1, matching the
  API's single-GPU assumption).
- `POST /documents/{relative_path}/parse` — parses one file from `DOCS_DIR`
  by path (path-traversal-checked against `DOCS_DIR`) instead of requiring
  the client to re-upload bytes it already has server-side.
- Test coverage: `tests/test_documents.py` (listing + picker routes + path
  traversal rejection), `tests/test_evaluate_dir_cli.py` (batch run
  idempotency, content-dedup across identical files, empty-dir no-op).

### Changed
- `scripts/start_ingestion.sh` now takes the docs directory as its first
  argument (or `DOCS_DIR` env var), defaulting to `./data/docs`.

## 0.2.0 — resource-efficient, cross-platform pipeline

### Added
- Cross-platform, resource-efficient Docling wiring
  (`app/pipeline/extract_docling.py`): `AcceleratorDevice.AUTO` device
  selection (CUDA on Linux, MPS on Apple Silicon, CPU fallback), overridable
  via `INGESTION_DEVICE` / `INGESTION_NUM_THREADS`; `get_converter()` caches
  the built `DocumentConverter` (and its loaded model weights) per process
  instead of reloading them on every request.
- Bounded parse concurrency in the API (`INGESTION_MAX_CONCURRENT_PARSES`,
  default `1`) plus thread-pool offload (`run_in_threadpool`) so a pipeline
  run no longer blocks the event loop — a single-process stand-in for the
  Redis VRAM lease described in `AGENTS.md` §3, which is still deferred.
- Model warm-up on startup via a FastAPI lifespan handler, logging the
  selected device/thread count/concurrency limit for operator visibility.
- `scripts/start_ingestion.sh` — starts the API + verification UI (one
  process, `uv run uvicorn`), with GPU/CPU detection logging.
- `scripts/start_ui.sh` — parses a given PDF against a running server, polls
  until the edition is ready, and opens the confidence-sorted inspector in
  the default browser.
- `README.md`, this `CHANGELOG.md`.

### Fixed
- `app/pipeline/section_role_classifier.py`: Docling can merge several
  visually-adjacent short lines (e.g. table-of-contents or index entries)
  into a single `TextItem`, space-joined with no line-break marker. The
  shape detectors (`looks_like_toc`, `looks_like_index`) count lines, so this
  silently defeated TOC/index detection on real extraction output. Fixed with
  a pseudo-line splitter (`_split_pseudo_lines`) that recovers entry
  boundaries from the trailing dot-leader/page-number pattern each original
  line still carries. Covered by a regression test
  (`test_merged_multiline_block_still_detected_as_toc`).
- `app/pipeline/extract_docling.py`: `TableItem` has no `.text` attribute in
  the installed Docling version; accessing it raised `AttributeError` on any
  document containing a table.

## 0.1.0 — initial born-digital pipeline

First vertical slice of Goal 1 (`../docs/ARCHITECTURE.md` §7 build order,
step 1): born-digital PDFs only, no OCR/GPU-lease/comparison-engine yet.

### Added
- `canonical_schema.py`, `rulepacks/section_roles.yaml`,
  `app/pipeline/section_role_classifier.py` — moved from `../docs/` (reference
  implementations, imported not rewritten).
- Pipeline stages: `quarantine` (encrypted/malformed PDF rejection), `triage`
  (per-page `DIGITAL_CLEAN`/`DIGITAL_DIRTY`/`SCANNED`/`UNCERTAIN` via PyMuPDF
  text-layer stats), `route` (`OWNERSHIP`-table-driven extractor selection,
  `app/config/ownership.yaml`), `extract_docling` (Docling as sole geometry
  owner, mapped onto `canonical_schema.Node` with full `Provenance`),
  `lattice` (pass-through reconciliation seam), `topology` (regex clause_id
  assignment), `continuity` (multi-page table stitching + `header_path`
  assignment), `assemble` (final `CanonicalEdition` construction).
- `app/store/artifact_store.py` — filesystem-backed, content-addressed store
  (`sha256(pdf) + pipeline_version`), atomic writes via rename-from-temp.
- `app/store/rasterize.py` — page image rendering for the verification UI.
- `app/api.py` — stateless FastAPI surface: `POST /parse`,
  `GET /editions/{hash}`, `GET /editions/{hash}/pages/{n}.png`,
  `GET /editions/{hash}/ui`.
- `app/ui/templates/inspector.html` — confidence-sorted verification
  inspector with bounding-box overlays colored by section-role/review status.
- `tests/fixtures/make_test_pdf.py` — synthetic IEC/CISPR-shaped standard PDF
  fixture (title page, TOC, foreword, preface, numbered clauses with a nested
  sub-clause and a table, index).
- Test suite: triage thresholds, section-role classification, artifact-store
  idempotency, full pipeline e2e, API statelessness (fresh process/store
  instance sees the same artifact).
