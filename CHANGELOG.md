# Changelog

All notable changes to `ingestion-engine` are recorded here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); this project doesn't
tag releases yet, so entries are grouped by work session instead of version.

## Unreleased

### Added — clause reunification + honest scorecard (heading-recall accuracy)
- **Reunite two-column split clauses** (`topology.merge_split_clause_numbers`,
  wired into `assemble` before `assign_clause_ids`). ISO/DIN definition lists
  put the clause number in a left gutter and its term beside it, which Docling
  emits as *separate* nodes ("3.2" as a lone paragraph, "tatsächliche
  Bewegung" as a separate section). A reading-order pass prepends a lone
  clause-number node to the short title/term immediately following it on the
  same page and drops the lone node, so the definition is reunited
  ("3.2 tatsächliche Bewegung") and `assign_clause_ids`/`nest_by_clause` label
  and nest it. Fail-safe: only when the number is a node's *entire* text and
  the next node is a short (≤12-word) title with no clause number of its own.
  On DIN 60068 all 23 of the `3.x` definitions (incl. `3.4.1`/`3.4.2`) now
  carry clause_ids and reunited titles, from 0 before.
- **Clause_ids on numbered `list_item`/title nodes**: `assign_clause_ids`
  previously labeled only `section`/`heading`. Docling correctly joins a
  numbered list entry ("16.1 Flame-retardant test.") but types it
  `list_item`, so it got no clause_id. Now `list_item`s (and short title-like
  paragraphs, held to a stricter ≤6-word bar to exclude prose like
  "3.2 m/s applies…") with a leading numbered title get a clause_id too. Fixes
  DNVGL-CG-0339's 17 `16.x` clauses.
- **Scorecard honesty** (`accuracy.clause_heading_candidate`): reject
  standards-classification codes (`ICS 19.040`, `CCS …`, `UDC …`) that look
  like clause numbers but sit on cover/metadata pages — so heading recall
  measures real clause headings, not cover metadata.
- **Deep-nested-table review flag** (`app/pipeline/nested_table.py`): a table
  cell holding a uniform mini-grid (≥2 lines, all with the same ≥2 column
  count) is TableFormer flattening a table-in-cell; we can't faithfully
  reconstruct the inner grid, so the outer table node is flagged
  `review_required` + `possible_nested_table` (fail-toward-review, AGENTS
  §1.6) rather than silently trusted. Column-count *consistency* is what
  separates a nested grid from ordinary multi-line prose.
- **`continuity.stitch` F1 gate** (SKILLS.md ≥0.95): a deterministic gold set
  of continuation / non-continuation table pairs, asserting the stitch
  decision scores F1 = 1.0 — guards against over- or under-merging
  regressions.
- `PIPELINE_VERSION` → `0.6.0` (clause reunification + nested-table flag both
  change extraction output).

**Corpus scorecard (34 docs, 339 pages), before → after:** clause-heading
recall **83.3% → 91.2%** (549/659 → 599/657 — the two worst docs, DNVGL
`16.x` and DIN `3.x`, went 4/21 → 21/21 and 10/25 → 23/23). Also fixed the
scorecard itself: `extracted_clause_ids` now collects clause_ids from *all*
node types, not just section/heading, or the `list_item`/paragraph
enrichment above would have been invisible to the metric (it initially showed
a spurious +0.9pt until this was corrected). Other components unchanged:
paragraph 96.4%, caption 85.2%, table-region 99.1%, section-role gate 0/28.
Genuine-miss lines moved 73 → 87, which is Docling/formula-VLM run variance
between sessions (±14 lines out of ~5000+), not a regression — the clause
merge preserves every page token and `nested_table` only sets review flags,
so neither can raise per-page miss counts.

### Remaining Goal-1 gaps (deferred, documented — not silently dropped)
- `extract.table` TEDS ≥0.90 and `extract.equation` BLEU ≥0.90 gates need
  labeled HTML/LaTeX ground truth; the TEDS-lite region-fidelity proxy (99.1%)
  and formula-presence recall stand in until a labeled set exists.
- `topology.clauses` IEC/CISPR per-standards-family rulepacks + their own gold
  set; `extract.equation` MinerU/UniMERNet engine (Docling formula enrichment
  substituted); `extract.ocr` Surya2 (RapidOCR path in place via OWNERSHIP);
  annex normative-vs-informative distinction.

### Added — 98%-accuracy program: component scorecard, section-role gold set, OCR path
- **Component scorecard** (`app/cli/accuracy.py`/`accuracy_check.py --all`):
  corpus-wide per-component metrics on top of the per-page factual-accuracy
  checker — clause-heading recall (source lines matching the same
  clause-number pattern `topology.assign_clause_ids` itself uses, so the
  candidate definition can't drift from what extraction recognizes), a
  formula-page recall probe (pages with strong math glyphs `√∑∏∫∂` that
  should have produced an `equation` node), caption attachment (is a caption
  actually a child of its table/figure), and a "TEDS-lite" table-region
  fidelity check (source tokens inside a table's own cell-bbox region must
  appear in that table's cells). Full 34-doc / 339-page real corpus baseline:
  paragraph coverage 96.4%, table region fidelity 99.1%, heading recall
  83.3%, **caption attachment 68.9%** (the clear outlier).
- **Section-role gold set + false-exclusion gate** (build-order step 3,
  ARCHITECTURE.md §2.3): `tests/goldsets/section_roles/*.yaml`, 5 hand-labeled
  real docs (EN + DE) against source text, `app/cli/section_role_gold.py`
  runner computing the false-exclusion rate (the hard gate: a normative
  clause silently marked `compliance_relevant=false` drops real compliance
  evidence). `scripts/section_role_gold.sh` + `tests/test_section_role_gold.py`
  merge-gate test. **Gate: 0/28 false exclusions.**
  - Building the gold set found a real bug: `looks_like_title_page`
    (`section_role_classifier.py`) checked only page number + short text, with
    no requirement that the section be the document's *first* one. On a
    document chunk where an early glossary/definition entry ("Bündelfunk",
    "Dauerzustand") happened to land on the chunk's page 1, it was
    misclassified `title_page` and wrongly excluded -- a genuine false
    exclusion. Fixed by requiring `is_first_section` (position is already the
    primary, language-independent signal elsewhere in this classifier; this
    is the same principle applied more strictly).
- **OCR path** (build-order step 2, ARCHITECTURE.md §7): `assemble()` now
  auto-routes to Docling's RapidOCR when triage finds a `SCANNED`/`UNCERTAIN`
  page (`INGESTION_OCR`, default on; explicit `ocr_enabled=True` always wins).
  `app/config/ownership.yaml`'s `text` row for those page classes now names
  `rapidocr` (previously a `docling` best-effort placeholder).
  `tests/fixtures/make_scanned_pdf.py` rasterizes a born-digital PDF into a
  text-layer-free copy for **free OCR ground truth** -- the original's text
  layer is exactly what OCR should recover. `accuracy_check.py --doc
  <scanned> --gold-source <original>` scores against it: **86.8% measured
  coverage** on the synthetic fixture. OCR'd nodes get downgraded confidence
  and `review_required` like any `SCANNED` page (never silently trusted as
  much as a real digital text layer).
- **Proximity-based caption attachment** (`app/pipeline/caption_attach.py`):
  the scorecard's clear #1 gap. An unclaimed caption (Docling didn't link it
  via `.captions` -- the common case) adjacent to exactly one table/figure
  sibling on the same page becomes that table/figure's child instead of a
  loose sibling; ambiguous cases (caption between two tables, or no adjacent
  table/figure) stay untouched -- fail-safe, never guesses. Corpus-wide:
  **68.9% -> 85.2%** (164/238 -> 201/236 captions correctly attached).
- `PIPELINE_VERSION` bumped twice this session (`0.5.0` for OCR routing,
  `0.5.1` for caption attachment) -- each is an extraction-behavior change,
  so the content-address key must change too (see the earlier stale-cache
  entry below for why this matters).

### Corpus scorecard: before -> after this session (34 docs, 339 pages)

| Component | Before | After | Notes |
|---|---|---|---|
| Paragraph/text coverage | 96.4% | 96.4% | unchanged (not targeted this pass) |
| Clause-heading recall | 83.3% | 83.3% | unchanged; residual gap is single-digit trailing clause numbers ("Anwendungsbereich 1") deliberately not parsed as clause_ids to avoid false positives on figure/quantity labels ("Prüffeldstärke 2") -- a documented, not a silent, limitation |
| **Caption attachment** | **68.9%** | **85.2%** | fixed this session (`caption_attach.py`) |
| Table region fidelity | 99.1% | 99.1% | unchanged, already near target |
| Section-role false-exclusion | unmeasured | **0/28 (gate PASS)** | new gold set this session |
| Genuine content misses | 73 | 73 | unchanged; mostly un-rejoined hyphenation fragments across node boundaries ("ten.", "ben."), a distinct gap from the existing single-node de-hyphenation -- noted, not fixed this pass |

Not every component reached 98% this session -- heading recall's remaining
gap is a known, deliberate trade-off (documented above) rather than an
oversight, and the hyphen-fragment miss class is a real, scoped follow-up.
Stated honestly rather than claiming a blanket 98% across the board.

### Added — accurate compliance clause tree (topology, units, cross-refs)
- **Clause-number-driven hierarchy** (`topology.nest_by_clause`, wired into
  `assemble`). The tree was **flat** — every section a sibling — because
  `_build_tree` nests only by Docling's typographic heading level, which is
  uniform across clause depths in real standards. Now `5.3.5.1` nests under
  `5.3.5` → `5.3` → `5` using the clause number as the authoritative parent
  key. On a real German TL 81000 doc: was 0 clause_ids / depth 1 → now
  **16/23 clause_ids, depth 4**. Front/back matter and annexes stay top-level
  (never buried under the last clause); missing intermediate parents attach to
  the nearest present ancestor.
- **Trailing clause-number parsing** (`topology.assign_clause_ids`). German
  TL/DIN headings put the number at the END ("Grenzwertklassen 5.3.4",
  "Prüfaufbau 5.3.5.1"); the old regex only matched leading numbers, so those
  docs got **0 clause_ids**. Both positions are now parsed, with guards so
  dates ("2009-04-01"), standard numbers ("IEC 61000-4-3"), and trailing
  figure counts ("Prüffeldstärke 2") are not mistaken for clauses.
- **`canon.units`** (`app/pipeline/canon_units.py`, `Cell.quantity`): parses a
  table cell's `{value, unit, condition}` — the value-level signal
  comparison-engine needs to see a limit change (40 → 30 dBµV/m) rather than
  diffing raw strings. Curated EMC unit vocabulary (dBµV/m, MHz, m/s², µA, …),
  German decimal comma, comparators/ranges preserved, unit filled from the
  column header when the cell is a bare value. Conservative: prose cells
  ("Test Sec.3 [14.6]") are never parsed. On a real doc: **279/358 data cells**
  got structured quantities.
- **Cross-reference resolution** (`app/pipeline/xref.py`, `Node.xrefs`,
  `canonical_schema.XRef`): detects "see 4.2.3", "siehe 5.3.5", "Table 22",
  "Bild 15", "Anhang ZA" (EN + DE lead words) and resolves clause/annex
  references to the target `clause_id` when it exists in the edition. This is
  the "xref edges" half of `topology.clauses` (SKILLS.md), recorded as
  within-edition annotations (not comparison-engine graph edges). Conservative:
  plain numbers in prose are not treated as references.
- Regression tests: `tests/test_topology.py`, `tests/test_canon_units.py`,
  `tests/test_xref.py`, plus e2e assertions that the fixture's clause tree is
  nested and its table cells carry quantities + page provenance.

### Added
- **Per-page factual-accuracy checker** (`app/cli/accuracy.py`,
  `app/cli/accuracy_check.py`, `scripts/accuracy_check.sh`): compares each
  extracted `CanonicalEdition` against the source PDF's own text layer (ground
  truth for born-digital pages) and reports faithfulness per page + per
  component — token coverage, table numeric fidelity, reading-order Kendall
  tau, and a genuine-miss set. The measurement separates real misses from
  three expected-exclusion classes it detects rather than assumes: **furniture**
  (top/bottom band, *rotated* watermarks like the Beuth side-margin licensing
  stamp, and lines repeated across ≥3 pages), **front-matter** (excluded
  sections), and **wrap fragments** (token-subset coverage makes a rejoined
  hyphenated word a non-miss). Random sample each run, `--seed` to replay,
  JSON report under `data/eval-reports/`. Unit-tested in
  `tests/test_accuracy_check.py`.

### Fixed
- **Table cells now carry their own page/bbox provenance**
  (`canonical_schema.Cell.page` / `.bbox`, populated in
  `extract_docling._table_cells`). A table is the one element whose sub-parts
  can span pages: `continuity.stitch` merges a continuation table onto the
  previous page's node, so the node's single `provenance.page` is not the page
  of every cell. Cells previously had no provenance (an ARCHITECTURE.md §1.9
  gap), so a stitched page-8 cell was attributed to page 7. This produced a
  **false accuracy finding** — DNVGL-CG-0339 p8 looked like dropped table text
  ("Electrical slow transient", "surge"). It was **not** dropped: Docling's raw
  table has it (0 empty cells), and p7/p8 share identical 6-column headers so
  the stitch is a correct continuation. With per-cell pages the accuracy
  checker attributes each cell to its real page; DNVGL went from
  coverage 0.96 / 22 genuine misses to **coverage 1.0 / 0 genuine misses**.
  (Corrects the earlier changelog note that blamed TableFormer — the real
  cause was missing cell provenance, now fixed rather than deferred to a
  MinerU swap-in.)
- **Stale-cache extraction served indefinitely.** `PIPELINE_VERSION` was never
  bumped across the formula/multilingual/nesting/caption/triage extraction
  changes, so the content-address key (`sha256(pdf)+PIPELINE_VERSION`) stayed
  constant and the artifact store kept serving editions produced by old
  (sometimes buggy, near-empty) code. Surfaced by the accuracy checker: one
  real doc read at **coverage 0.10** from a stale 7-node cached edition vs
  **0.96** when re-extracted. `PIPELINE_VERSION` is now bumped whenever
  extraction changes (currently `0.3.0-digital-only`) — the correct
  content-address contract: extraction changes ⇒ key changes ⇒ old cache is
  unreachable.
- Accuracy checker's reading-order metric assumed a top-left origin; Docling
  uses **bottom-left** (higher-on-page = larger y), so reading order is
  *descending* y. `reading_order_tau` now accounts for this (was reporting −1
  for correctly-ordered pages; real docs now score ~0.98–1.0).

### Findings (real-doc accuracy baseline, random EMC/marine standards)
- After the fixes above: coverage **0.95–1.0**, numeric fidelity **0.94–1.0**,
  reading-order tau **~0.98–1.0**. Captured content is faithful; the residual
  genuine misses are a couple of wrapped note lines (e.g. a German "Hinweis:"
  note split across lines) — extraction has the text, the per-line check flags
  the wrap. No systemic dropped-content defect remains on the sampled docs.

### Added (real-doc eval + Goal-1 extractors: formulas, multilingual, tables, nesting)
- **Random-sampling evaluation harness** (`app/cli/evaluate_samples.py` +
  `scripts/eval_samples.sh`): picks N random PDFs from a sample dir
  (`EVAL_DOCS_DIR` → the user's QuickSamples path → `./data/eval-samples`),
  runs the full pipeline, and reports extraction-quality metrics per doc
  (page-class mix, node-type counts, table `header_path` coverage + spans,
  nesting depth + nested-list count, `Node.lang` coverage / languages /
  non-NFC text, equation LaTeX coverage, review counts). Fresh random each
  run; RNG seed logged for replay (`--seed`). Reports JSON to
  `data/eval-reports/`. Metrics module `app/cli/eval_metrics.py` is pure and
  unit-tested. (Note: reading the user's `~/Documents` sample set requires
  granting the terminal macOS Full Disk Access, or copying PDFs into
  `data/eval-samples/`.)
- **Formulas / maths / chemical equations** (`extract.equation` +
  `canon.equation`): Docling `do_formula_enrichment` enabled (behind
  `INGESTION_FORMULAS`, default on) — `FormulaItem` LaTeX flows into
  `Node.latex`; new `app/pipeline/canon_equation.py` normalizes LaTeX
  (delimiters, `\left`/`\right`, spacing macros, whitespace; idempotent) so
  equal-rendering equations compare equal, and wraps recognizably chemical
  formulas/reactions as mhchem `\ce{...}`. MinerU stays the documented GPU
  `OWNERSHIP` swap-in, not pulled in.
- **Multilingual ingestion** (`lang.detect` + `text.normalize`): new
  `app/pipeline/lang.py` tags each text node with a BCP-47 language and
  NFC-normalizes all text; `CanonicalEdition.lang_primary` now populated from
  the dominant language. Uses `lingua` (fully offline, bundled models, no
  runtime download) restricted to a curated ~17-language set — fastText
  `lid.176` (the doc's tool of record) doesn't build on this Python
  toolchain; lingua is the offline-equivalent substitute.
- **Deep nested content**: `extract_docling._build_tree` rewritten to walk
  Docling's real `body` tree (resolving `children` refs) instead of the
  flattened `iterate_items()`, so `ListGroup`/`ListItem` nesting and
  list-under-clause depth are preserved rather than collapsed to siblings.
  Section nesting (heading-level stack) and group/list nesting are now
  layered correctly; structural group containers are flattened without
  losing their contents.
- **Accurate table extraction**: `continuity.py` now uses the extractor's
  per-cell `column_header` flag (new `Cell.is_column_header`) instead of
  assuming "header = row 0", giving correct **multi-row, span-aware**
  `header_path` lineage; stitch detection compares normalized (NFC+casefold)
  full header-row signatures plus column-count agreement, so continuations
  merge and unrelated same-shape tables don't. (TableFormer was already in
  accurate mode.)
- Tests: `tests/test_eval_metrics.py`, `tests/test_canon_equation.py`,
  `tests/test_lang.py`, `tests/test_continuity.py`, plus new nesting/formula
  cases in `tests/test_extract_docling.py` (now stub the Docling `body` tree).
  `tests/conftest.py` disables formula enrichment for the suite (kept fast +
  offline; the formula path is covered by fast stub/unit tests and by the
  eval harness on real docs). 84 tests total.

### Added
- `INGESTION_RELOAD=1` dev flag for `scripts/start_ingestion.sh`: passes
  `--reload` to uvicorn, watching the whole project root and excluding
  `data/`, `.venv/`, `.git/`, caches, and fixture PDFs (the artifact store
  and `DOCS_DIR` both default under `./data` and both mutate on every
  parse — watching them would thrash the reloader). Each restart re-runs
  the FastAPI lifespan's Docling model warm-up.
- `triage.py`/`route.py` are now actually wired into the pipeline
  (`app/pipeline/assemble.py`) instead of being unused, fully-tested dead
  code. Every page is now really classified `DIGITAL_CLEAN` /
  `DIGITAL_DIRTY` / `SCANNED` / `UNCERTAIN` via PyMuPDF text-layer stats
  (`triage.classify_document`), and nodes on a non-`DIGITAL_CLEAN` page get
  a downgraded confidence (`DIGITAL_DIRTY: 0.75, SCANNED: 0.5,
  UNCERTAIN: 0.6`, replacing the flat 0.95 placeholder) plus
  `review_required=True` and a `page_class_*` reason — this is what makes
  the confidence-sorted inspector actually correlate with real page quality
  instead of every born-digital node looking equally trustworthy
  (ARCHITECTURE.md §2.3). `route.Ownership` is also exercised (not yet
  behavior-changing, since only Docling is wired in) and recorded per page
  in `pipeline_provenance["engine_by_page"]` for forward compatibility once
  OCR/equation extractors are added. `pipeline_provenance["page_classes"]`
  is recorded for auditability.
- `canonical_schema.py`: new `"caption"` `NodeType`.

### Fixed
- Table and figure captions were being extracted as generic `"paragraph"`
  nodes (disconnected from the table/figure they describe) or, when
  Docling's layout model mislabeled caption-like text ("Table 1 ...",
  "Figure 2 ...") as a section heading, as spurious top-level `"section"`
  nodes. `app/pipeline/extract_docling.py` now: resolves
  `TableItem`/`PictureItem.captions` (a list of `RefItem`) via Docling's
  own `ref.resolve(dldoc)` and nests the result as a `"caption"`-typed
  child when Docling does populate that link; falls back to attaching an
  unclaimed caption as its own correctly-typed node at its reading-order
  position when Docling doesn't (confirmed empirically that `.captions`
  isn't reliably populated even for an adjacent caption — a design that
  only trusted it would have silently dropped captions); and guards the
  heading branch so caption-like text never opens a spurious section.
  `extract()` was split into `extract()` (I/O) + `_build_tree(dldoc)`
  (pure) to make this testable against stub `dldoc` objects without a real
  Docling conversion. Covered by 7 new tests in `test_extract_docling.py`
  and 4 new tests in `test_pipeline_e2e.py`; the test fixture
  (`tests/fixtures/make_test_pdf.py`) now includes a captioned table and a
  captioned figure (previously had neither, which is why this shipped
  unnoticed).
- `scripts/start_ingestion.sh`: empty-array expansion
  (`"${RELOAD_ARGS[@]}"`) crashed the server on startup under macOS's
  default bash 3.2 (`set -u` + empty array is mishandled). Fixed by
  building the whole uvicorn invocation as one command array and
  conditionally appending to it, instead of expanding a separately-built,
  possibly-empty array.
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
