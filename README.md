# ingestion-engine

Stateless PDF → `CanonicalEdition` ingestion pipeline. This is Goal 1 of the
two-service architecture in [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md):
turn one PDF into a high-accuracy, provenance-tracked document tree and let a
human verify it through a visual accuracy evaluator (source ↔ canonical, section
map, document graph) — never a workflow queue.

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
picker listing every file there, each linking to its own visual accuracy
evaluator once processed:

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
accuracy evaluator for that document. Nothing about this listing is
stored by the service: it's recomputed on every page load by re-hashing files
in `DOCS_DIR` and checking the artifact store, so any replica gives the same
answer (see `app/store/documents.py`).

The API and the UI are the same FastAPI app (`app/api.py`) — there's no
separate UI process.

To evaluate every document in the directory up front instead of one at a time
from the picker — e.g. before a review session, or on a large corpus — run
the batch evaluator in the background (see
[Batch evaluation](#batch-evaluation-run-in-background) below).

To open the document picker for a directory of PDFs (it lists every file under
the server's `DOCS_DIR` and lets you parse/view each from the browser):

```bash
./scripts/start_ui.sh path/to/docs-dir
```

The directory must match the `DOCS_DIR` the running server was started against —
`start_ui.sh` verifies this and refuses to open a mismatched listing rather than
repointing a live server. To parse a single arbitrary PDF (not necessarily under
`DOCS_DIR`), POST it directly:

```bash
curl -X POST --data-binary @document.pdf http://127.0.0.1:8001/parse
# -> {"edition_id": "<sha256>+<pipeline_version>", "status": "processed"}

curl http://127.0.0.1:8001/editions/<edition_id>
# -> 202 while processing (shouldn't happen with the synchronous handler
#    today, but the API contract holds for a future async worker split),
#    200 + full CanonicalEdition once done

open http://127.0.0.1:8001/editions/<edition_id>/ui
```

### Visual accuracy evaluator (`/editions/{id}/ui`)

The post-extract review surface for confirming extraction fidelity by eye. It is
a self-contained page (no build step, no external JS) that fetches the full
post-consensus `CanonicalEdition` from `GET /editions/{id}` and renders four
linked views around a single selection — click a source region, a section-tree
row, or a graph node and it highlights everywhere and loads the object's
canonical record:

- **Source pane** — the rasterized page with status-colored, clickable bbox
  overlays (green unanimous · amber majority · orange review · red quarantined ·
  dashed excluded); selecting a table also outlines its individual cells.
- **Detail** — the selected object's full canonical record: the **consensus
  block** shows every parser/engine candidate side-by-side with agree/disagree
  marks, the `consensus` state, and any `quarantine_reason` (equations render
  LaTeX + MathML and each engine's candidate; tables render the cell grid with
  per-cell disagreement flags); plus parameters, cross-references (resolved →
  clickable / dangling), review reasons + gate repairs, and provenance. With
  nothing selected it shows a **worst-first review queue**.
- **Section map** — the canonical clause outline (the "which source section
  became which canonical object" mapping), with per-node consensus/confidence.
- **Graph** — a document graph: the nesting tree plus toggleable cross-reference,
  multi-page continuity, and translation-group edges, nodes colored by status.

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
- **Table structure model.** TableFormer **V2** by default — it owns the cell
  grid everything downstream treats as ground truth (the `table_rectangularity`
  gate, each `Cell.bbox`/`header_path`, the evaluator's per-cell overlays).
  `INGESTION_TABLEFORMER=v1` falls back to V1 for a document where V2 regresses.
  The choice is part of the content-address key (`PIPELINE_VERSION` gains a
  `-tfv1` suffix), so the two never share a cache namespace and switching always
  reprocesses. Docling runs V2 on CPU when the accelerator is MPS.
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

## Environment variables

Every variable is optional — the defaults are the supported configuration on
both platforms. Values are read at process start (`INGESTION_TABLEFORMER` also
at import, since it feeds the content-address key), so set them *before*
launching, not mid-run.

Boolean vars accept `0`/`false`/`no` to disable (case-insensitive); anything
else, including unset, means enabled.

### Paths and server

| Variable | Default | Notes |
|---|---|---|
| `DOCS_DIR` | `./data/docs` | Read-only PDF input volume the picker lists. `scripts/start_ingestion.sh <dir>` sets it from its first argument. |
| `ARTIFACT_STORE_PATH` | `./data/artifacts` | Content-addressed output store. Safe to delete — it's a cache, everything rebuilds. |
| `HOST` / `PORT` | `127.0.0.1` / `8001` | Server bind. Scripts only. |
| `INGESTION_RELOAD` | `0` | Dev auto-restart on source change. Re-runs model warm-up per restart; not for production. |
| `INGESTION_MAX_CONCURRENT_PARSES` | `1` | Concurrent pipeline runs. The single-process stand-in for a VRAM lease — see GPU sizing below. |
| `EVAL_DOCS_DIR` / `EVAL_N` / `EVAL_WORKERS` | — / `3` / `1` | Evaluation CLIs only (`scripts/eval_*.sh`, `evaluate_dir.sh`). |

### Accelerator (Mac and Linux, with and without a GPU)

| Variable | Default | Notes |
|---|---|---|
| `INGESTION_DEVICE` | `auto` | `auto` **probes**: `cuda` > `mps` > `cpu`. Accepts `cpu`, `cuda`, `cuda:1` (pin one GPU on a multi-GPU box), `mps`. |
| `INGESTION_NUM_THREADS` | `min(cpu_count, 8)` | CPU threads for CPU-side inference. |

What `auto` actually resolves to:

| Host | Docling (layout/tables/formulas) | Model engines (GLM-OCR) |
|---|---|---|
| **Linux + NVIDIA** | `AcceleratorDevice.AUTO` → CUDA | `cuda` |
| **Linux, no GPU** | → CPU | `cpu` |
| **Apple Silicon Mac** | → MPS | `mps` |
| **Intel Mac** | → CPU | `cpu` |

Two platform quirks worth knowing, both upstream behavior, not settings:

- **TableFormer V2 runs on CPU when the accelerator is MPS** — Docling forces
  that fallback internally. Table structure is therefore CPU-bound on Apple
  Silicon; CUDA and CPU hosts are unaffected.
- **Docling and the model engines resolve the device separately**
  (`extract_docling._select_device()` hands Docling an `AcceleratorDevice`;
  `app/pipeline/device.resolve_device()` returns a torch device string for
  GLM-OCR). `INGESTION_DEVICE` drives both, so they can't disagree.

**GPU sizing.** Keep `INGESTION_MAX_CONCURRENT_PARSES=1` on a single 16 GB GPU
— that's one document resident at a time (Docling layout + table + formula
models, plus GLM-OCR if it loads). The batch CLI (`app/cli/evaluate_dir.py`)
has its own `EVAL_WORKERS` limit, so running the server and a batch job
together on one GPU can still oversubscribe it. To keep this process off a
shared GPU entirely: `INGESTION_DEVICE=cpu`.

### Engines and models

| Variable | Default | Notes |
|---|---|---|
| `INGESTION_TABLEFORMER` | `v2` | `v1` falls back to TableFormer V1. Part of the content-address key (`PIPELINE_VERSION` gains `-tfv1`), so switching always reprocesses. |
| `INGESTION_OCR` | on | Auto-OCR when triage finds a `SCANNED`/`UNCERTAIN` page. Docling still only OCRs pages that need it. |
| `INGESTION_FORMULAS` | on | Docling formula enrichment (LaTeX). A per-formula VLM pass — turn off for documents with no maths. The test suite forces this off. |
| `INGESTION_GLM_OCR` | on | GLM-OCR as a second opinion in the N-version consensus. Degrades gracefully to single-parser if weights are absent. |
| `INGESTION_MINERU_URL` | unset | MinerU/UniMERNet equation sidecar (e.g. `http://127.0.0.1:8101/predict`). Unset = engine unavailable, pipeline continues. |
| `INGESTION_SURYA_URL` | unset | Surya OCR sidecar, same contract. |
| `INGESTION_SIDECAR_TIMEOUT` | `30` | Seconds per sidecar HTTP call. A timeout is treated as "unavailable", never an error. |

MinerU and Surya run **out of process** because their dependency pins conflict
with Docling's; they get their own GPU/host, so `INGESTION_DEVICE` doesn't
apply to them. Every model engine is optional by design — if it can't load, the
pipeline records fewer corroborating parsers rather than failing.

### Examples

```bash
# Mac dev box, defaults (MPS where supported)
./scripts/start_ingestion.sh ~/pdfs

# Linux GPU box, pin the second GPU, allow two documents at once
INGESTION_DEVICE=cuda:1 INGESTION_MAX_CONCURRENT_PARSES=2 \
  ./scripts/start_ingestion.sh /data/pdfs

# Shared GPU box: stay on CPU entirely
INGESTION_DEVICE=cpu ./scripts/start_ingestion.sh /data/pdfs

# Fastest structural pass: no formula VLM, no second-opinion OCR
INGESTION_FORMULAS=0 INGESTION_GLM_OCR=0 ./scripts/start_ingestion.sh /data/pdfs
```

## Pipeline

`quarantine -> triage -> route -> extract(Docling, OCR auto-routed) ->
caption_attach -> lattice -> topology -> continuity -> runs backfill ->
canon.units -> canon.equation -> lang -> classify_type -> parameters ->
classify.section_role -> nest_by_clause -> xref -> table-geometry consensus ->
text consensus -> gates -> content-addressed identity -> assemble`.

- `triage` measures each page's text-layer quality
  (`DIGITAL_CLEAN`/`DIGITAL_DIRTY`/`SCANNED`/`UNCERTAIN`, PyMuPDF stats) and
  `route` looks up the extractor priority table (`app/config/ownership.yaml`)
  — both feed confidence/review flags and are recorded in
  `pipeline_provenance` (`page_classes`, `engine_by_page`, `page_confidence`).
  `page_confidence` is Docling's own per-page score set, kept **diagnostic
  only**: it was measured against `accuracy_check` ground truth and found
  unusable as a gate (r=+0.009 with page coverage; 0.26–0.31 precision), so
  nothing branches on it — see the note on `_DIGITAL_TEXT_CONFIDENCE` in
  `extract_docling.py`. The eval report tracks its document mean to catch
  model-upgrade drift. If any page is
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
- `topology` first **reunites two-column split clauses**
  (`merge_split_clause_numbers`: a lone gutter number "3.2" + its separate
  term "tatsächliche Bewegung" → one node), then assigns `clause_id` from
  **leading or trailing** heading numbers (and numbered `list_item`s like
  "16.1 Flame-retardant test."), then `nest_by_clause` rebuilds the flat
  section list into the real **clause hierarchy** (`5.3.5.1` under `5.3.5` →
  `5.3` → `5`) — the compliance tree, not a flat list.
- `nested_table` flags (never fabricates) a table that TableFormer flattened —
  a cell holding a uniform mini-grid gets the table `review_required` +
  `possible_nested_table` so a human sees it.
- `canon.units` parses each table cell into `{value, unit, condition}`
  (`Cell.quantity`) — the value-level signal for limit changes (40 → 30
  dBµV/m). `xref` records cross-references ("see 4.2.3", "Table 22",
  "Anhang ZA") on `Node.xrefs`, resolving clause/annex targets within the
  edition.
- **runs backfill** (`app/pipeline/runs.py`) attaches PyMuPDF per-character
  `runs` (font/size/baseline → `vertical_align`) to every text node by bbox
  intersection. PyMuPDF is the raw-text authority, so `raw_text` is
  reconstructed *from* the runs (byte-exact, keeps the en-dash / `±` /
  superscript the content stream actually has); Docling's transcription is
  recorded as a `parsers` corroborator candidate. This is the only layer that
  can catch `10⁻³ V/m` flattening to `10-3` (which parses as 7).
  **Table cells get the same treatment** (`Cell.runs` / `Cell.raw_text` /
  `Cell.parsers`) — a cell *is* the limit value, so a flattened exponent costs
  most there. Because the runs come from a bbox query that isn't always precise,
  `raw_text` is only set when it provably says the same thing as `text` (modulo
  whitespace and vertical alignment) *and* actually carries super/subscript;
  `text` is never overwritten and a mismatch raises no finding. Content passes
  read `canonical_schema.preferred_text()`, which falls back to `text` whenever
  the runs witness doesn't corroborate it — an incomplete `raw_text` must not
  silently drop a value.
- `classify_type` assigns the closed **normative CDM type** (`Requirement`/
  `Recommendation`/`Permission`/`Warning`/`Scope`/…) from a per-language modal
  lexicon (`modality.py`; `il convient de` == *should*, never inferred by
  translating to English). `parameters` extracts compliance-grade `Parameter`s
  (`Decimal` value, comparator, `±` tolerance, frequency-band condition,
  `quantity_kind`) from prose and cells; a missing comparator is left unset so
  the units gate quarantines it rather than defaulting to `eq`.
- **`gates`** runs the eight deterministic admission gates
  (`app/pipeline/gates/`, spec order: header/footer → run-integrity →
  numbering → table-rectangularity → continuation → modal-verb → unit/tolerance
  → equation → cross-reference). Each is `pass | repair | quarantine`: a repair
  writes an auditable `Node.repairs` entry and fires only when the fix is
  uniquely determined; everything else quarantines (`consensus="quarantined"` +
  a reason) into a review queue. The queue is summarized in
  `pipeline_provenance.gates`. Extraction never guesses, repairs silently, or
  discards — disagreement is recorded, not resolved.

Run the gates as a CI admission check over a saved edition:

```bash
uv run python scripts/verify_extraction.py edition.json   # 0 clean · 1 quarantined · 2 doc-level alarm
```

### Parser authority & deferred engines

Text/`runs`/`±`/superscript authority is **PyMuPDF**; section-tree authority is
**Docling**. Consensus is wired **measurement-first** so it records genuine
disagreement without flooding the review queue:

- **Text consensus** (`_apply_text_consensus`): only genuine *transcribers* vote
  (`GENUINE_TEXT_PARSERS = {pymupdf, glm_ocr, rapidocr, mineru, surya}`).
  Docling's `node.text` is reflow-derived (it prepends clause numbers, flattens
  subscripts PyMuPDF owns), so it is a recorded corroborator, not a voter —
  hard-voting it disagreed on 42–92 nodes/doc, all artifacts. Trivially
  unanimous on born-digital pages; activates to real quarantine on scanned pages
  once the OCR engines (GLM-OCR, Surya) populate `parsers`.
- **Table geometry** (`table_geometry.reconcile`): **Docling** (layout) and
  **pdfplumber** (ruling lines) are the genuine independent voters — they agree
  on n_rows/n_cols on every clean ruled table and disagree only on ambiguous
  ones (the merged-cell-collapse guard). **PyMuPDF** word-clustering is an
  approximate corroborator (multi-line cells inflate its row count), never a
  hard voter. A genuine third table-structure parser (Camelot) is the deferred
  swap-in for true three-way.
- **Equations**: Docling's CodeFormula enrichment emits **valid structured
  LaTeX** (verified — not the flattened garble the reference's "never accept
  Docling equations" note assumed, which predates enrichment), so Docling owns
  the equation lane; `canon_equation` enriches it with `defines`/`symbol_table`.

**GLM-OCR corroborator (live)** — `app/pipeline/engines/glm_ocr.py` runs
`zai-org/GLM-OCR` (0.9B, MIT weights / Apache-2.0 code, 96.5 UniMERNet formula
recognition, #1 OmniDocBench v1.5) in-process via plain `transformers`,
serving BOTH previously-deferred lanes (see
`../docs/references/ocr-engine-evaluation.md`): a second independent LaTeX
candidate per equation (disagreement → quarantine with both candidates; the
comparison folds `\text`/`\mathrm`-style transcription variants but preserves
genuine glyph differences), and the scanned-page OCR second opinion (RapidOCR
layer recorded as the `rapidocr` voter; OCR-derived Parameters quarantined by
default; 0.95 confidence ceiling). Device: `cuda > mps > cpu`
(`app/pipeline/device.py`, `INGESTION_DEVICE` override); gated by
`INGESTION_GLM_OCR` (default on) and gracefully unavailable — no weights means
one log line and the single-parser pipeline, never a crash. Equations also
carry a browser-renderable **MathML** form (`latex2mathml`) alongside the
LaTeX source of truth, plus `defines`/`symbol_table`/`computes_limit`, and
same-clause language instances are linked by `translation_group_id`.

**MinerU + Surya corroborators (out-of-process sidecars)** — the two engines
named in the reference authority matrix are integrated **alongside** GLM-OCR
(more independent voters strengthens the N-version cross-check). They run
out-of-process because their `transformers` pins are mutually incompatible and
incompatible with docling's (UniMERNet hard-pins `transformers==4.42.4`, Surya
wants `>=4.51`, docling resolves `5.8.x` — one venv is `uv lock`-unsatisfiable),
so each runs as an HTTP sidecar in its own environment and the in-repo adapter
is a thin stdlib client:
- `app/pipeline/engines/mineru.py` — MinerU's formula stage, **UniMERNet**
  (`wanderkid/unimernet_base`, Apache-2.0), a third independent equation LaTeX
  candidate (encoder-decoder, not a VLM). Enable with `INGESTION_MINERU_URL=<url>`.
- `app/pipeline/engines/surya.py` — **Surya OCR** (Apache-2.0 code;
  conditional-commercial Rail-M weights), an independent scanned-OCR text
  candidate. Enable with `INGESTION_SURYA_URL=<url>`.
Sidecar contract: `POST <url>` raw PNG crop → `{"latex"|"text": "..."}`
(`_sidecar.py`; `INGESTION_SIDECAR_TIMEOUT`, default 30s). Both degrade
gracefully (URL unset or sidecar unreachable → one log line, pipeline
continues), exactly like GLM-OCR without cached weights.

Still deferred: the DOCX lane, per-family clause rulepacks, and the
licensing-allowlist CI gate (PyMuPDF is AGPL — revisit before the customer
bundle).

Node ids are **content-addressed** (`identity.py`): `standard_id#section_path`
for clause-numbered objects (stable across editions when numbering is stable —
the premise of ID-first alignment), `doc_id#sha256(raw_text)[:12]` otherwise.

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
                            -> topology -> continuity -> runs -> canon_units
                            -> canon_equation -> lang -> classify_type -> parameters
                            -> classify.section_role -> xref -> gates -> assemble
                            run.py: process_pdf() -- the one pipeline entrypoint the API
                            and the batch CLI both call
                            runs.py (PyMuPDF per-char runs / super-subscript authority),
                            consensus.py (N-version disagreement engine),
                            extract_pdfplumber.py (3rd table-geometry opinion),
                            classify_type.py + modality.py (closed CDM type),
                            parameters.py (Decimal Parameter extraction),
                            gates/ (8 deterministic admission gates),
                            canon_equation.py (LaTeX/mhchem normalization),
                            lang.py (NFC + per-node language)
  store/                   artifact_store.py (content-addressed filesystem store)
                            documents.py (DOCS_DIR listing, re-derived on every request)
                            rasterize.py (page images for the UI)
  ui/templates/            documents.html (picker), inspector.html (visual accuracy
                            evaluator: source↔canonical, section map, document graph — one
                            self-contained page rendered client-side from GET /editions/{id})
  config/ownership.yaml    OWNERSHIP priority table (extractor per content-type/page-class)
canonical_schema.py        shared contract with comparison-engine (Goal 2, not in this repo yet)
rulepacks/section_roles.yaml   multilingual front-matter dictionary (confidence booster only)
scripts/                   start_ingestion.sh, start_ui.sh, evaluate_dir.sh, eval_samples.sh,
                            verify_extraction.py (run the 8 gates as a CI admission check)
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

See `../docs/references/parser-consensus.md` and `../docs/ARCHITECTURE.md`
§6/§7. The consensus **engines** GLM-OCR (default), MinerU/UniMERNet equations
and Surya scanned OCR are now wired in (the last two opt-in — see the engines
section above); still deferred is the **DOCX lane** (python-docx / OMML /
revision marks, shares CDM + gates).
Also deferred: the full text-consensus wiring across all parsers (the engine +
gates are built and unit-tested; only PyMuPDF↔Docling text authority is wired
into `assemble` so far), the licensing-allowlist CI gate, the full Redis GPU
lease, `comparison-engine` (Neo4j/Qdrant), the `docker-compose.yml` deployment,
and IEC/CISPR-specific clause-topology rulepacks (need real standards documents
as gold-set input).

See `CHANGELOG.md` for what's shipped so far.
