# ingestion-engine

Stateless PDF → `CanonicalEdition` ingestion pipeline. This is Goal 1 of the
two-service architecture in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md):
turn one PDF into a high-accuracy, provenance-tracked document tree and let a
human verify it through a confidence-sorted inspector — never a workflow queue.

Current scope (see `../docs/ARCHITECTURE.md` §7 build order, step 1): born-digital
PDFs via [Docling](https://github.com/docling-project/docling), no OCR/equation
extractors yet, no Neo4j/Qdrant/comparison-engine (that's Goal 2, a separate
service — see `AGENTS.md`'s two-service boundary). Scope and deferred work are
tracked in this repo's plan and in code comments at each stage's seam.

## Requirements

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/) for dependency management and running

Runs on:
- **macOS** (Apple Silicon or Intel) — CPU or MPS, for local development.
- **Linux with an NVIDIA GPU** — CUDA, for the deployment target gpu or mac

Device selection is automatic (see [Resource usage](#resource-usage) below) —
no code changes needed to move between the two.

## Setup

```bash
cd ingestion-engine
uv sync              # installs runtime + dev dependencies into .venv
uv run pytest -q     # 84 tests, ~25s
```

On Linux with an NVIDIA GPU, `uv sync` installs whatever PyTorch wheel PyPI
resolves by default, which may be CPU-only depending on your platform/index
configuration. If `nvidia-smi` works but the startup log (see below) shows
`device=cpu`, install a CUDA-enabled torch build matching your driver, e.g.:

```bash
uv pip install torch --index-url https://download.pytorch.org/whl/cu121
```

(pick the `cuXXX` index matching your installed CUDA version).

## Running

Point the server at a directory of PDFs (`DOCS_DIR`) and it serves a document
picker listing every file there, each linking to its own verification
inspector once processed:

```bash
./scripts/start_ingestion.sh /path/to/pdfs                    # http://127.0.0.1:8001
DOCS_DIR=/path/to/pdfs ./scripts/start_ingestion.sh            # equivalent
HOST=0.0.0.0 PORT=8080 ./scripts/start_ingestion.sh /path/to/pdfs  # bind elsewhere
INGESTION_RELOAD=1 ./scripts/start_ingestion.sh /path/to/pdfs  # dev: auto-restart on source changes
```

`INGESTION_RELOAD=1` watches source files (excluding `data/`, `.venv/`,
caches) and restarts the server on change via uvicorn's `--reload`. Each
restart re-runs the FastAPI lifespan's Docling model warm-up (a few
seconds) — expected, not a bug. Not recommended for production use.

Open `http://127.0.0.1:8001/` — every PDF under `DOCS_DIR` (searched
recursively) shows up with a status (`ready` / `not processed`) and, for
unprocessed files, a "parse now" button. Ready ones link straight to the
confidence-sorted inspector for that document. Nothing about this listing is
stored by the service: it's recomputed on every page load by re-hashing files
in `DOCS_DIR` and checking the artifact store, so any replica gives the same
answer (see `app/store/documents.py`).

The API and the UI are the same FastAPI app (`app/api.py`) — there's no
separate UI process.

To evaluate every document in the directory up front instead of one at a time
from the picker — e.g. before a review session, or on a large corpus — run
the batch evaluator in the background (see
[Batch evaluation](#batch-evaluation-run-in-background) below).

To parse one PDF from anywhere (not necessarily under `DOCS_DIR`) and jump
straight to its inspector:

```bash
./scripts/start_ui.sh path/to/document.pdf
```

Or drive it directly:

```bash
curl -X POST --data-binary @document.pdf http://127.0.0.1:8001/parse
# -> {"edition_id": "<sha256>+<pipeline_version>", "status": "processed"}

curl http://127.0.0.1:8001/editions/<edition_id>
# -> 202 while processing (shouldn't happen with the synchronous handler
#    today, but the API contract holds for a future async worker split),
#    200 + full CanonicalEdition once done

open http://127.0.0.1:8001/editions/<edition_id>/ui
```

`POST /parse` (and the picker's "parse now" / `POST /documents/{path}/parse`)
are idempotent and content-addressed: the same PDF bytes + the same pipeline
version always resolve to the same `edition_id`
(`sha256(pdf) + PIPELINE_VERSION`), and a repeat call is a fast no-op (200,
not reprocessed). See `app/store/artifact_store.py` and
`../docs/ARCHITECTURE.md` §0.

## Accuracy check (source-vs-extraction)

To verify factual accuracy against the source documents — per page, per
component — run the accuracy checker over N random PDFs:

```bash
./scripts/accuracy_check.sh -n 3            # random sample, seed logged
./scripts/accuracy_check.sh --seed 7        # reproducible
```

It compares each extracted `CanonicalEdition` against the source PDF's own
text layer (ground truth for born-digital pages) and reports, per page:
token **coverage**, table **numeric fidelity**, **reading-order** Kendall tau,
and a **genuine-miss** set — after excluding page furniture (headers/footers,
rotated watermarks, lines repeated across pages), excluded front-matter, and
hyphenation/wrap fragments (which extraction correctly rejoins). Reports land
in `data/eval-reports/accuracy-*.json`. See `app/cli/accuracy.py` for the
measurement design. Note: because artifacts are content-addressed by
`sha256(pdf)+PIPELINE_VERSION`, bump `PIPELINE_VERSION` (`app/version.py`)
whenever extraction behavior changes, or cached editions from old code will be
served — the checker will read stale output otherwise.

**Corpus scorecard** — `--all` checks every PDF in the sample dir instead of
a random sample, and adds per-component metrics: clause-heading recall,
formula-page recall (pages with strong math glyphs that should have produced
an `equation` node), caption attachment (is a caption a child of its
table/figure), and table-region fidelity ("TEDS-lite" — source tokens inside
a table's cell-bbox region must appear in that table's cells):

```bash
./scripts/accuracy_check.sh --all
```

**OCR validation** — score a synthetic scanned copy against the original PDF
it was rasterized from (free ground truth, no hand-labeling):

```bash
uv run python -m tests.fixtures.make_scanned_pdf original.pdf scanned.pdf
uv run python -m app.cli.accuracy_check --doc scanned.pdf --gold-source original.pdf
```

**Section-role gold set** — the Goal-1 merge gate (ARCHITECTURE.md §2.3):
false-exclusion rate on hand-labeled real documents must be 0 (a normative
clause silently marked non-compliance-relevant would drop real evidence).
Labels live in `tests/goldsets/section_roles/*.yaml`:

```bash
./scripts/section_role_gold.sh
```

## Batch evaluation (run in background)

For evaluating a whole directory of documents at once — the common case when
you want to visually spot-check extraction quality across a corpus — run the
batch evaluator detached from your terminal:

```bash
./scripts/evaluate_dir.sh /path/to/pdfs
# [evaluate_dir] started in background, pid=12345
# [evaluate_dir] tail progress:  tail -f ./data/eval-logs/eval-<timestamp>.log
# [evaluate_dir] check running:  kill -0 12345 && echo running || echo done
```

It writes to the same `ARTIFACT_STORE_PATH` the API server reads from (no
running server required to run this — it calls the pipeline directly), so
results appear in the document picker as they complete; reload `/` to see
progress. Match `ARTIFACT_STORE_PATH` / `DOCS_DIR` to whatever
`start_ingestion.sh` is using if you want the two to line up.

```bash
EVAL_WORKERS=2 ./scripts/evaluate_dir.sh /path/to/pdfs   # parallelize -- see caveat below
./scripts/evaluate_dir.sh /path/to/pdfs --foreground     # run inline, e.g. under your own supervisor
```

Default `EVAL_WORKERS=1` matches the single-GPU "one document at a time"
assumption (`AGENTS.md` §3). Raising it *and* running the interactive server
concurrently on the same GPU can oversubscribe it — the batch CLI and the API
server each bound their own concurrency independently
(`INGESTION_MAX_CONCURRENT_PARSES` for the server), but neither knows about
the other; the full Redis VRAM lease that would arbitrate between them is
still deferred (see [Resource usage](#resource-usage)). On a Mac or a
lightly-loaded GPU box, running both together is generally fine.

Equivalent direct invocation, e.g. from your own supervisor/cron:

```bash
uv run python -m app.cli.evaluate_dir /path/to/pdfs --workers 1
```

## Resource usage

This service is designed to run unmodified on a Mac dev machine and on the
Linux+NVIDIA production target (`../docs/TECHSTACK.md`'s 16 GB single-GPU
reality), without a full GPU-lease deployment (`../docs/gpu_orchestration.py`
is still a reference skeleton — not wired in yet). Two things make that
practical for a single-process service:

- **Device auto-detection.** `app/pipeline/extract_docling.py` passes
  `AcceleratorDevice.AUTO` to Docling by default, which resolves to CUDA on
  Linux, MPS on Apple Silicon, and CPU otherwise. Override with
  `INGESTION_DEVICE=cpu|cuda|mps` (e.g. to keep this process off a shared GPU).
  `INGESTION_NUM_THREADS` caps CPU threads (default: `min(cpu_count, 8)`).
- **Resident model cache + bounded concurrency.** Docling's layout/table model
  weights are loaded once per process (`get_converter()`, `lru_cache`) instead
  of being rebuilt per request. `INGESTION_MAX_CONCURRENT_PARSES` (default `1`)
  bounds how many pipeline runs execute at once — a single-process stand-in for
  the Redis VRAM lease described in `AGENTS.md` §3, appropriate until a real
  multi-replica deployment needs the full lease.

The startup log line shows what was actually selected:

```
ingestion-engine ready: device=AcceleratorDevice.AUTO num_threads=8 max_concurrent_parses=1
```

## Pipeline

`quarantine -> triage -> route -> extract(Docling, OCR auto-routed) ->
caption_attach -> lattice -> topology -> continuity -> canon.units ->
canon.equation -> lang -> classify.section_role -> nest_by_clause -> xref ->
assemble`.

- `triage` measures each page's text-layer quality
  (`DIGITAL_CLEAN`/`DIGITAL_DIRTY`/`SCANNED`/`UNCERTAIN`, PyMuPDF stats) and
  `route` looks up the extractor priority table (`app/config/ownership.yaml`)
  — both feed confidence/review flags and are recorded in
  `pipeline_provenance` (`page_classes`, `engine_by_page`). If any page is
  `SCANNED`/`UNCERTAIN`, the whole document is re-extracted with Docling's
  RapidOCR enabled (`INGESTION_OCR`, default on) — Docling only actually OCRs
  the pages that need it.
- `extract` walks Docling's real `body` tree, preserving **deep list/group
  nesting** and section hierarchy. Table/figure **captions** become
  `"caption"`-typed nodes; `caption_attach` re-parents an unclaimed caption
  under its adjacent table/figure by page/position proximity when Docling's
  own `.captions` link didn't fire (the common case). **Formulas** (Docling
  formula enrichment, `INGESTION_FORMULAS`, default on) become `"equation"`
  nodes with LaTeX in `Node.latex`.
- `continuity` stitches multi-page tables and assigns **multi-row, span-aware
  column `header_path` lineage** from the extractor's own header-cell flags.
- `canon.equation` normalizes LaTeX (and wraps chemistry as mhchem `\ce{}`);
  `lang` NFC-normalizes text and tags each node's language (`Node.lang`,
  `lang_primary`) via offline `lingua`.
- `topology` assigns `clause_id` from **leading or trailing** heading numbers
  ("4.2.3 Limits" and German "Grenzwertklassen 5.3.4"), then `nest_by_clause`
  rebuilds the flat section list into the real **clause hierarchy** (`5.3.5.1`
  under `5.3.5` → `5.3` → `5`) — the compliance tree, not a flat list.
- `canon.units` parses each table cell into `{value, unit, condition}`
  (`Cell.quantity`) — the value-level signal for limit changes (40 → 30
  dBµV/m). `xref` records cross-references ("see 4.2.3", "Table 22",
  "Anhang ZA") on `Node.xrefs`, resolving clause/annex targets within the
  edition.

## Evaluating extraction on real documents

`./scripts/eval_samples.sh` picks random PDFs from a sample directory, runs
the pipeline, and reports per-document quality metrics (page classes, table
`header_path` coverage, nesting depth, language coverage, equation/LaTeX
coverage, review counts) to `data/eval-reports/`:

```bash
./scripts/eval_samples.sh                 # 3 random docs
EVAL_N=5 ./scripts/eval_samples.sh
./scripts/eval_samples.sh --seed 42       # reproducible sample (seed logged each run)
```

Sample dir resolution: `--docs-dir` / `$EVAL_DOCS_DIR` / the configured
QuickSamples path / `./data/eval-samples`. On macOS, reading PDFs under
`~/Documents` requires granting the terminal **Full Disk Access** (System
Settings → Privacy & Security), or just copy PDFs into `data/eval-samples/`.

## Layout

```
app/
  api.py                  FastAPI: GET /, POST /parse, GET /editions/{hash}(/ui|/pages/{n}.png),
                            GET /documents, POST /documents/{path}/parse
  cli/evaluate_dir.py      batch-processes every PDF under a directory (no server needed)
  cli/evaluate_samples.py  random-sampling quality-eval harness (+ eval_metrics.py)
  pipeline/                quarantine -> triage -> route -> extract(Docling) -> lattice
                            -> topology -> continuity -> canon_equation -> lang
                            -> classify.section_role -> assemble
                            run.py: process_pdf() -- the one pipeline entrypoint the API
                            and the batch CLI both call
                            canon_equation.py (LaTeX/mhchem normalization),
                            lang.py (NFC + per-node language)
  store/                   artifact_store.py (content-addressed filesystem store)
                            documents.py (DOCS_DIR listing, re-derived on every request)
                            rasterize.py (page images for the UI)
  ui/templates/            documents.html (picker), inspector.html (confidence-sorted view)
  config/ownership.yaml    OWNERSHIP priority table (extractor per content-type/page-class)
canonical_schema.py        shared contract with comparison-engine (Goal 2, not in this repo yet)
rulepacks/section_roles.yaml   multilingual front-matter dictionary (confidence booster only)
scripts/                   start_ingestion.sh, start_ui.sh, evaluate_dir.sh, eval_samples.sh
tests/                     pytest suite + tests/fixtures/make_test_pdf.py (synthetic standard)
```

## Testing

```bash
uv run pytest -q
```

`tests/fixtures/make_test_pdf.py` generates a synthetic IEC/CISPR-shaped
standard (title page, table of contents, foreword, preface, numbered clauses
with a nested sub-clause and a table, back-of-book index) since no real
standards documents ship in this repo yet. `tests/test_pipeline_e2e.py` runs
the full pipeline against it and asserts front/back-matter exclusion,
clause_id assignment, table/header_path extraction, and provenance on every
node. `tests/test_documents.py` and `tests/test_evaluate_dir_cli.py` cover
the document picker and batch evaluator.

## Deferred (tracked, not forgotten)

See `../docs/ARCHITECTURE.md` §6/§7: OCR (Surya), equation extraction
(MinerU), the full Redis GPU lease, `comparison-engine` (Neo4j/Qdrant),
the `docker-compose.yml` deployment, and IEC/CISPR-specific clause-topology
rulepacks (need real standards documents as gold-set input).

See `CHANGELOG.md` for what's shipped so far.
