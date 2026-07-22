# Changelog

All notable changes to `ingestion-engine` are recorded here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); this project doesn't
tag releases yet, so entries are grouped by work session instead of version.

## Unreleased

### Fixed — German captions became false sections; runs authority now guarded; table cells get runs (0.14.0)

Three findings from the Linux evaluation, each verified against the corpus
before and after.

**1. An English-only regex was corrupting German document structure.**
`extract_docling._CAPTION_LIKE` guards against Docling mislabelling a caption as
a heading — but only matched `table|figure|fig.`. In the German samples that
produced **14 spurious sections**: `Tabelle 3 – FPSC Luftentladung` became a
*section that then adopted the very table it captions*, and
`Tabelle 19 (fortgesetzt)` swallowed a table plus six siblings. All carried
`clause_id=None`.

The damage was not only structural. `gates/cross_reference._caption_inventory`
only scans `type == "caption"`, so a caption promoted to a section is invisible
to it and every `siehe Tabelle 3` dangles — this one regex was the **largest
single source of quarantines** in those documents. Extending it to
`tabelle|bild|abbildung|abb.|tab.`, plus a new `_BLOCK_LABEL` for standalone
`Legende`/`Zeichenerklärung`/`Legend`/`Key` (emitted as a `note` — a legend is
not the caption of a numbered object, and this keeps it out of the caption
inventory), gives: **spurious sections 14 → 0**, caption nodes 14 → 24,
**total gate quarantines 72 → 62**, `cross_reference` 28 → 19. The residual
unresolved refs (`Figure 3`, `Annex A`, …) are genuine page-slice artifacts.

This also makes `caption_attach.attach_captions_by_proximity` reachable for
German documents — it already handled captions above *or* below a table; German
captions simply never became captions.

**Read the eval report's caption-attachment line carefully: the ratio drops
(14/15 = 0.933 → 19/24 = 0.792) while the outcome improves.** The denominator
grew because 9 captions that were previously mis-typed as sections are now
counted as captions at all; captions actually attached rose 14 → 19. The 5 that
don't attach are page-straddling or have no detected adjacent object
(`Tabelle 4`, `Bild 14`, `Tabelle 20 (fortgesetzt)`), and `caption_attach`
requires an adjacent same-page table/figure — it declines to guess by design.
Relaxing that same-page constraint for a caption at a page boundary is the
obvious follow-up, and is not done here.

**2. `raw_text` is the right authority, but only when it corroborates `text`.**
`parameters.py` read Docling's flattened `text`, where `10 m/s²` has already
become `10 m/s 2` and parses as a bare `10 m` (a length!). `identity.py` and
`run_integrity.py` already preferred `raw_text`. But switching unconditionally
is *also* wrong: `raw_text` is reconstructed from runs found by an imprecise
bbox query and can be **incomplete** — measured, a node whose `text` was `6 Hz`
had `raw_text` of just `Hz`, so preferring it silently dropped the value.

New shared helper `canonical_schema.preferred_text()` / `same_text_content()`
uses the runs witness only when it agrees with `text` modulo whitespace and
vertical alignment. Measured over 420 real nodes: **418 unchanged, 2 changed,
both corrections** (`10 m` → `10 m/s^2`; a bogus `100 cm` → correctly nothing),
**zero regressions**. Applied to `parameters.py` and `classify_type.py`.

Note for anyone extending this: NFKC maps SUPERSCRIPT MINUS (U+207B) to MINUS
SIGN (U+2212), *not* ASCII hyphen, so `10⁻³` does not compare equal to `10-3`
without also folding the dash family — the most valuable case would otherwise be
silently rejected.

**3. Table cells now carry their own runs** (`Cell.runs`, `Cell.raw_text`).
Cells hold the actual limit values yet had no super/subscript protection at all;
`_backfill_runs` covered only prose types. Every cell now records its PyMuPDF
runs and a `parsers["pymupdf"]` candidate beside `parsers["docling"]`, reusing
the existing region helpers.

Enrichment is deliberately narrow, and the numbers are why. Content-equality
alone marked **360** cells enrichable — but nearly all differed only in
whitespace, where the runs version is strictly *worse*, because
`reconstruct_raw_text` is a bare `"".join()` and drops the space at an internal
line break (`industries. We` → `industries.We`). Requiring the runs to actually
carry vertical alignment cuts that to **14** cells. Those 14 are all footnote
markers (`b)` → `b⁾`), not exponents: **this corpus contains no flattened
exponent in a table cell, so the cell work is mechanism and evidence, not a
measured accuracy win.** `cell.text` is never overwritten, and a structural
mismatch (~20% of cells, dominated by imprecise cell geometry rather than real
corruption) raises no finding — that would manufacture false alarms.

`PIPELINE_VERSION` → `0.14.0` (tree shape, captions, parameters and the cell
schema all change).

**Known, deliberately out of scope:** `reconstruct_raw_text` drops the
line-break space in any multi-line region. Real and pre-existing (it affects
`identity`/`run_integrity` too); the guards above make it harmless here, but it
deserves its own change and its own measurement.

### Fixed — `INGESTION_DEVICE=auto` silently disabled GLM-OCR on every scripted start

`app/pipeline/device.resolve_device()` returned its `INGESTION_DEVICE` override
verbatim, so the documented default `auto` was handed to torch as a literal
device string. `.to("auto")` raises `RuntimeError` ("Expected one of cpu, cuda,
…") — and because the model engines treat a load failure as *unavailable,
degrade gracefully*, the exception was caught and logged as a warning. Net
effect: **GLM-OCR silently dropped out of the N-version consensus and the
pipeline ran single-parser**, on both CUDA and MPS hosts.

This hit the normal path, not an exotic one: `scripts/start_ingestion.sh`
*exports* `INGESTION_DEVICE=auto` (line 33), so every server started via the
documented script was affected. It stayed invisible because running a CLI
directly (`python -m app.cli.eval_report`) leaves the var unset, and then
resolution probes correctly and GLM-OCR loads — which is why eval runs logged
`GLM-OCR loaded on mps` while the service did not.

`auto` (and empty/whitespace) now means *probe* — `cuda` > `mps` > `cpu` —
matching what `extract_docling._select_device()` already did for Docling's own
accelerator. Explicit pins (`cpu`, `cuda:1`, `mps`) still pass through
untouched. Covered by `tests/test_device.py`, including an end-to-end assertion
that whatever is resolved is something torch can actually load a module onto.

### Added — environment variable reference for Mac and Linux, with GPU

New README section documenting all 17 environment variables with their real
defaults, verified against the code rather than written from memory: paths and
server, accelerator, and engines/models. Includes what `INGESTION_DEVICE=auto`
resolves to per host (Linux+NVIDIA / Linux CPU-only / Apple Silicon / Intel
Mac), GPU sizing guidance for a single 16 GB card, and two platform quirks that
are upstream behavior rather than settings: **TableFormer V2 runs on CPU when
the accelerator is MPS** (Docling forces that fallback, so table structure is
CPU-bound on Apple Silicon), and Docling vs. the model engines resolving the
device through separate code paths that `INGESTION_DEVICE` drives in common.

### Added — Docling confidence captured as diagnostics, and measured as unusable for gating (0.13.1)

Evaluated Docling's built-in [confidence scores](https://docling-project.github.io/docling/concepts/confidence_scores/)
(`ConversionResult.confidence`, v2.34.0+) as a replacement for the fixed
`Provenance.confidence` constants in `extract_docling.py` — the evaluator's
review queue sorts by that field, so a real signal would be valuable.
**Measured first, and rejected as a gate.** Three findings:

1. **Most components are dead.** Across the eval samples (born-digital):
   `table_score` is always NaN (unimplemented upstream), `ocr_score` is NaN with
   OCR off, and `parse_score` is *exactly* 1.000 on every page. Only
   `layout_score` varies (0.603–0.940). `mean_score` therefore averages a
   near-constant, which is why almost every page grades "excellent" even at
   layout 0.70 — so `mean_grade`/`low_grade` can't carry a threshold, despite
   being the two fields Docling's docs tell users to focus on.
2. **The one live score doesn't predict errors.** Over 67 pages scored against
   `accuracy_check` ground truth: **r=+0.009** with page coverage (none) and
   r=−0.362 with genuine misses. Pages *with* real misses average layout 0.819
   vs 0.848 without — a 0.029 gap across a 0.34-wide range. As a gate:
   **precision 0.26–0.31, recall 3/16–9/16.**
3. **The OCR path doesn't rescue it.** On an in-memory scanned copy
   (`make_scanned_pdf.build`), `ocr_score` comes alive (0.961–0.980) but is
   narrow, while `parse_score` goes NaN. At no point are more than 2 of 4
   components live, and `mean_score` averages a *different subset* per path, so
   it isn't comparable across documents.

Gating on this would have manufactured the same false-positive review flood
just fixed in `parameters.py` (0.12.1). So:

- **`Provenance.confidence` constants are unchanged**, and the stale "first
  thing replaced with a real signal" comment is replaced with the measurements
  above so this isn't re-litigated. A page-level score (every element on a page
  shares one number) that is uncorrelated with per-element correctness would
  make the review-queue ordering *worse* than the honest constant.
- **Captured as inert diagnostics**: `extract_with_confidence()` returns
  Docling's per-page report alongside the tree (same conversion — no second
  pass), stored under `pipeline_provenance["page_confidence"]`. NaN serializes
  as `null`, not the bare `NaN` token, which is invalid JSON and would break the
  evaluator's `fetch()`. A test asserts no gate/consensus code reads the field.
- **Aggregated per document in the eval report** (`docling layout score
  (diagnostic, not a gate)`) — the one place a weak signal earns its keep, since
  per-page noise averages out and a corpus-wide shift flags a model-upgrade
  regression. (The TableFormer V1→V2 swap landed with no such signal.)
- `ocr_score` is the component most likely to become useful once the
  scanned/dirty path is built out, but must be validated against ground truth
  first — `make_scanned_pdf.py` + `accuracy_check --gold-source` make that free.

`PIPELINE_VERSION` → `0.13.1`: extraction output is unchanged, but the edition
artifact gains a field, so cached editions must be rebuilt to carry it.
Verified non-behavioral: re-running the eval report at the same seed after a
full reprocess reproduced the rollup byte-for-byte (63/0 quarantines,
1505/1523 table-region fidelity, identical coverage/heading/caption metrics).

### Changed — TableFormer V2 is now the table-structure model (0.13.0)

Docling's table structure stage moves from TableFormer V1 to **V2**
(`docling-project/TableFormerV2`), set via `PdfPipelineOptions.table_structure_options`
in `app/pipeline/extract_docling.py`. This model owns the cell grid the rest of
the pipeline treats as ground truth for table geometry — the
`table_rectangularity` gate, every `Cell.bbox`/`header_path`, and the accuracy
evaluator's per-cell overlays all derive from it — so its structure quality is
the ceiling on table fidelity.

A/B over the same 4 documents (`eval_report --seed 4242 -n 4`), V1 → V2:

- **`table_rectangularity` gate findings: 6 → 2** — the gate fires on
  incomplete/malformed cell grids ("uncovered cell (dropped cell?)"), so this
  is the direct measure of the structure improvement.
- **Total gate quarantines: 72 → 63** (−12.5%).
- Paragraph coverage 0.9454 → 0.9478; heading recall and caption attachment
  unchanged (81/86, 14/15 — both are layout-stage metrics V2 doesn't touch).
- Table-region fidelity reads 0.9947 → 0.9882, but the *matched* count is
  identical (1505) and only the denominator grows (1513 → 1523): V2 detects
  ~10 more table-region units rather than losing any matched content.

V2 drops V1's fast/accurate `mode` split (single transformer, no knob).
`do_cell_matching` stays on, which is what keeps `Cell.bbox` in real page
coordinates instead of model space. Note V2 runs on CPU when the accelerator
is MPS (Docling forces that fallback); CUDA and CPU are unaffected.

`INGESTION_TABLEFORMER=v1` restores V1 as an escape hatch for a document where
V2 regresses.

**The variant is part of the content-address key**: `PIPELINE_VERSION` becomes
`0.13.0-tfv1` when V1 is selected (`app/version.py`), so the two models never
share a cache namespace. This was found the hard way — before folding it in,
an A/B run with `INGESTION_TABLEFORMER=v1` returned the *cached V2* editions
and reported byte-identical metrics for both models, with a "reprocessed"
document finishing in 0.2s. `extract_docling` now imports the selection from
`app.version` rather than re-reading the env var, so the model that gets built
and the key it is stored under cannot disagree. Regression-tested in
`tests/test_extract_docling.py`.

### Fixed — parameters.py prose extraction: three false-Parameter sources flooding the review queue (0.12.1)

The eval's largest quarantine driver, `units: parameter 'X' has no comparator`,
had three independent root causes in `_PARAM`/`_BAND`, all in prose extraction
(table-cell extraction via `canon_units.py` was already anchored and unaffected):

- **Unit-as-word-prefix false match**: `_PARAM`'s unit alternation had no
  trailing word boundary, so e.g. "...DNVGL-CP-0203 **may** be used..." matched
  `value=203, unit=mA` off the first two letters of "may" (any word starting
  with a unit-prefix letter was at risk after any standard-designation number).
  Fixed with a trailing `(?!\w)` and a matching leading `(?<!\w)` on the value,
  closing the same gap for designator numbers glued to a preceding letter.
- **Soft hyphen (U+00AD) breaks range detection**: a PDF hyphenation-break
  artifact sitting where the source meant a literal range separator
  ("3­100 Hz") defeated `_BAND`'s range-hi group, leaving a bare
  comparator-less `frequency` Parameter instead of a `condition` string.
  `parse_parameters` now normalizes soft hyphens between digits to real hyphens
  (elsewhere, dropped — same rationale as `consensus._SOFT_HYPHEN`). Also
  fixed a related, always-present `_BAND` gap: a unit stated once, covering
  the whole range ("3-100 Hz"), previously required a unit on both sides.
- **Standalone leading "± N%"**: wasn't recognized at all — `_TOL` only matched
  a tolerance immediately *after* a base value, so the ± was silently dropped
  and the bare number became a comparator-less Parameter (plus a separate gate
  finding: "symbol '±' ... dropped"). Now emitted range-shaped
  (`comparator="range", range=(-N, N)`), matching the existing numeric-range
  representation, with `tolerance` populated so the gate's symbol-survival
  check sees the ± accounted for.

`PIPELINE_VERSION` → `0.12.1` (extraction output changes for documents
hitting any of the three patterns). Re-run eval-report to verify the
`units`-gate quarantine-reason count drops.

### Changed — `/editions/{id}/ui` is now a visual accuracy evaluator

The verification UI grows from a page-image + confidence-sorted table into a
linked **source ↔ canonical accuracy evaluator** for confirming extraction
fidelity by eye after an extract. No `PIPELINE_VERSION` bump — extraction output
is unchanged; this is the review surface over it.

- Four **linked** views around one selection model: clicking a source-page
  overlay, a section-tree row, or a graph node highlights the object everywhere
  and loads its canonical record.
  - **Source pane** — status-colored, clickable bbox overlays (unanimous /
    majority / review / quarantined / excluded); a selected table also outlines
    its individual cells.
  - **Detail** — the selected object's full post-consensus record: a consensus
    block rendering every `parsers[engine]` candidate side-by-side with
    agree/disagree marks + `consensus` state + `quarantine_reason` (equations
    show LaTeX + rendered MathML per engine; tables render the cell grid with
    per-cell disagreement flags), plus parameters, cross-references (resolved →
    clickable / dangling), review reasons + gate repairs, and provenance. Empty
    selection shows a worst-first **review queue**.
  - **Section map** — the canonical clause outline (source-section → canonical
    object mapping) with per-node consensus/confidence.
  - **Graph** — a document graph: nesting tree + toggleable cross-reference,
    continuity, and translation-group edges, colored by status.
- Implemented as ONE self-contained page (`app/ui/templates/inspector.html`) —
  inline CSS + vanilla JS + inline SVG, no build step and no external/CDN JS
  (the platform is offline). It fetches everything from the existing
  `GET /editions/{id}` JSON, so `inspector_ui` (`app/api.py`) is trimmed to a
  readiness check + template shell (server-side overlay computation removed).
  Status colors follow the dataviz reference palette and always pair with a
  text label. Route + JSON-contract smoke tests added (`tests/test_documents.py`).

### Added — Wave 4 (cont.): MinerU + Surya as out-of-process N-version corroborators (0.12.0)

The two deferred engines named in the reference authority matrix
(`docs/references/parser-consensus.md`) are now integrated **alongside** GLM-OCR
— more genuinely-independent voters is the whole premise of N-version consensus
(a 3-way equation vote, a 4-way scanned-text vote). GLM-OCR stays the default,
in-process engine.

**They run out-of-process, and that is forced.** UniMERNet 0.2.3 hard-pins
`transformers==4.42.4`, Surya wants `transformers>=4.51`, and docling resolves
`5.8.x`: the three stacks cannot share one virtualenv (`uv lock` is provably
unsatisfiable — the "known dependency conflicts with Docling pins" the eval doc
warned about, now measured). So each runs as an HTTP **sidecar** in its own
environment; the in-repo adapters are thin, stdlib-only clients that degrade to
"unavailable" (one log line, pipeline continues) when the sidecar URL is unset
or unreachable — never a hard dependency, and the default lock gains no
MinerU/Surya deps.

- **MinerU/UniMERNet equation corroborator** (`app/pipeline/engines/mineru.py`,
  sidecar `INGESTION_MINERU_URL`) — MinerU's formula stage, UniMERNet
  (`wanderkid/unimernet_base`), **Apache-2.0 code + weights** (the AGPL concern
  is the full `magic-pdf` pipeline, not this model). An encoder-decoder trained
  on formula crops — architecturally independent of GLM-OCR's general VLM, which
  is what makes its agreement real corroboration.
  `assemble._apply_equation_corroboration` is now N-way over a deterministic
  engine list (`glm_ocr`, `mineru`): each contributes a LaTeX candidate next to
  Docling's authority; **any** disagreement (after `canon_equation.eq_compare_form`
  variant folding) quarantines with all candidates kept, authority never
  overwritten (SKILLS.md rule 5: a priority table, not a call-site heuristic).
- **Surya scanned-OCR corroborator** (`app/pipeline/engines/surya.py`, sidecar
  `INGESTION_SURYA_URL`) — an independent detection+recognition OCR stack.
  Apache-2.0 code; conditional-commercial Rail-M weights (a second reason to
  isolate it). Surya 2.x already runs as its own spawned inference server, so a
  sidecar is native. `assemble._backfill_ocr_candidates` now drives both OCR
  engines from one list; `surya` was already in `consensus.GENUINE_TEXT_PARSERS`
  / `TEXT_AUTHORITY_ORDER` so `_apply_text_consensus` votes over it with no
  call-site change, under the existing 0.95 OCR ceiling + "OCR-derived Parameter
  quarantined by default".
- Sidecar contract (`app/pipeline/engines/_sidecar.py`): `POST <url>` raw PNG
  crop → `200 {"latex"|"text": "..."}`; stdlib `urllib` only, so adding an
  engine adds no runtime dependency. `INGESTION_SIDECAR_TIMEOUT` (default 30s).
- Suite: wiring covered by monkeypatched tests (green with no sidecar running —
  the CI contract). `ownership.yaml`, `engines/__init__.py`,
  `docs/references/ocr-engine-evaluation.md`, `docs/SKILLS.md` updated.

### Added — Wave 4: deferred engines live — GLM-OCR corroborator, MathML, tree fixes (0.11.0)

Implements the previously-deferred tasks (licensing constraints lifted). Runs
on Mac (MPS) and NVIDIA CUDA, CPU fallback (`app/pipeline/device.py`,
`INGESTION_DEVICE` override).

- **GLM-OCR corroborator engine** (`app/pipeline/engines/glm_ocr.py`) — the
  deferred MinerU (equations) and Surya (OCR) lanes are filled by ONE engine:
  `zai-org/GLM-OCR`, 0.9B params (~2.5 GB), **MIT weights / Apache-2.0 code**,
  96.5 UniMERNet formula recognition (the `extract.equation` gold-set
  benchmark), #1 OmniDocBench v1.5 — run in-process via plain `transformers`
  `AutoModelForImageTextToText` (the class Docling already loads; zero new
  runtime stack). Lazy singleton, greedy decode (deterministic), gated by
  `INGESTION_GLM_OCR`, gracefully unavailable (no weights → one log line,
  single-parser pipeline as before). Evaluation + decision record:
  `../docs/references/ocr-engine-evaluation.md`.
  - **Equation lane**: a second independent LaTeX candidate per equation
    (`parsers["glm_ocr"]`), compared via `canon_equation.eq_compare_form` — a
    comparison-only normal form that folds transcription variants (`\text` vs
    `\mathrm`, `$$`, `\tag`, spaced subscripts) but preserves genuine glyph
    differences. Measured on DIN's 3 equations: 2 corroborated, 1 **real**
    disagreement surfaced (`\mathfrak{c}` vs `\mathrm{c}`) → quarantined with
    both candidates. GLM-OCR also recovers the `\tag{N}` equation numbers
    Docling drops.
  - **Scanned-OCR lane**: on SCANNED/UNCERTAIN pages the RapidOCR layer is
    recorded as the genuine `rapidocr` voter, GLM-OCR contributes the second
    opinion, and text consensus votes (`TEXT_AUTHORITY_ORDER`: PyMuPDF on
    born-digital, GLM-OCR on scanned). **OCR-derived Parameters are quarantined
    by default** per parser-consensus.md.
- **MathML** (`canon_equation.latex_to_mathml`, new dep `latex2mathml`, MIT,
  pure-Python): every equation carries a browser-renderable `mathml` form so
  compliance evidence can SHOW the formula; LaTeX stays the source of truth,
  conversion is failure-tolerant.
- **`computes_limit`** (`canon_equation.annotate_computes_limit`): set when a
  normative sibling in the same section subtree depends on the equation's
  defined symbol — conservative (multi-char symbol surfaces only, no
  cross-section inference; a manufactured dependency edge would misclassify an
  equation edit as an acceptance-criteria change).
- **`translation_group_id`** (`lang.link_translation_groups`): language
  instances of the same clause are LINKED (deterministic `tg:<clause_id>`),
  never merged; no-op on monolingual documents.
- **Misnested-clause hoist** (`topology.hoist_misnested_clauses`): a clause
  node Docling buried under the WRONG clause (DIN 3.24 — and 3.26 inside it —
  under 3.23) is pulled back to the flat list before `nest_by_clause`, so the
  compliance TREE now reflects clause numbering, completing what the
  numbering-gate inventory guard only tolerated. A running-context refinement
  (0.11.1) also reaches clause nodes inside stray top-level NON-clause
  containers mid-clause-run (the real DIN 3.26 chain: a "(en: final slope)"
  section holding it) while leaving TOC entries — which legitimately carry
  clause numbers before any clause opens — untouched. Verified: every DIN 3.2x
  definition is now a proper depth-1 sibling.
- Suite: GLM-OCR off by default in tests (2.5 GB VLM); wiring covered by
  monkeypatched tests + an opt-in real-inference smoke test (runs where the
  weights are cached).

### Added — Wave 3: equation richness, gate completeness, identity (0.10.0)

Two planning-time investigations reshaped this wave (both recorded in the plan):

- **Docling equation LaTeX is valid, not garble (T9).** The reference's "Docling
  flattens formulas to garbled text; never accept its version" describes
  *pre-enrichment* Docling. Verified: current CodeFormula enrichment emits proper
  structured LaTeX (`N_{\text{d}} = 2\ B_{\text{e}} \times T_{\text{a}}`). So the
  original "demote latex → rendered_text and quarantine every equation" would
  have *discarded good data*. Instead `canon_equation` now **keeps** the LaTeX
  and enriches it: `extract_defines` (the LHS definiendum), a `symbol_table`
  inventory, and the producing engine tagged in `Node.parsers`
  (`docling_formula`) so equation consensus activates when MinerU is added.
  `ownership.yaml` + the equation gate wording corrected: Docling owns the
  equation lane; **MinerU is the registered deferred corroborator, not a
  prerequisite**.
- **Table/figure cross-references resolve now (T10a).** `gates/cross_reference`
  builds a `(kind, number)` inventory from caption labels ("Bild 1", "Table 11",
  "Tabelle 46" → (figure,1)/(table,11)/(table,46)) and resolves
  `XRef(kind in {table,figure})` against it — the spec's flagship "see Table 8
  where Table 8 was dropped" guard. A fragment-boundary guard suppresses
  forward references (a ref to a number beyond the highest captured caption is
  out-of-slice, not a drop). Measured: DIN 3/3, DNVGL 10/10, TL 4/5 resolved
  with the one out-of-range ref correctly *not* flagged — zero false alarms.
- **Table mandatory-column check (T10b).** `gates/table_rectangularity` now also
  quarantines a rectangular table whose limit column (limit-keyword header) has
  an empty data cell — a compliance comparison against a hole. The units-row
  consistency sub-check and `computes_limit` (needs a param↔symbol graph /
  MinerU symbol tables) are deferred with reasons.
- **Content-addressed identity (T11a).** `app/pipeline/identity.py` re-stamps the
  random `uuid` node ids with `make_object_id` as the final assembly pass:
  `standard_id#section_path` for clause nodes (e.g. `DIN EN 60068-2-64#3.22`),
  `doc_id#sha256(raw_text)[:12]` otherwise. Deterministic (same PDF → same ids,
  verified), reference-integrity-preserving (`continues_from/to` and
  `Parameter.source_object_id` remapped through the same old→new map — zero
  dangling references on a real 201-node doc). `standard_id` is derived
  conservatively (a designation at the START of a page-1 title, so a prose
  reference to another standard can't hijack it — DIN resolves to its own
  designation, title-page-less fragments fall back to the doc hash).
  `translation_group_id` is deferred (no parallel bilingual gold to validate a
  linker; DIN's "(en: …)" glosses aren't separate language instances).

Net on the eval corpus: gate quarantines stable at 59 (no regression), heading
recall retained (0.955), table/figure xref resolution added with zero false
alarms, and every equation keeps its LaTeX + gains `defines`/`symbol_table`.

### Added — Wave 2: N-version consensus wired into the pipeline (0.9.0)

The consensus engine was built and unit-tested but inert — `Node.consensus`
stayed `unanimous` even on disagreement. Wave 2 wires it, driven by a
measurement of how the parsers actually disagree (so it records genuine
conflicts without re-flooding the review queue).

- **Text consensus (T6)** — `assemble._apply_text_consensus` runs
  `consensus.reconcile_text` over the GENUINE text transcribers in each node's
  `parsers`. Docling is deliberately excluded from the vote
  (`consensus.GENUINE_TEXT_PARSERS = {pymupdf, mineru, surya}`): its authority
  is structure, and the `node.text` it emits is reflow-derived (prepends clause
  numbers, flattens the sub/superscripts PyMuPDF is the sole authority for,
  follows its own reading order), so on the eval corpus it "disagrees" with
  PyMuPDF on 42–92 nodes/doc — all artifacts, not value conflicts. Hard-voting
  it would re-create the exact flood the gates exist to prevent. Docling's
  candidate stays recorded in `parsers` for audit; with PyMuPDF-only the vote is
  trivially unanimous, and consensus activates to real quarantine the moment a
  genuine alternate transcriber (MinerU/Surya) populates `parsers` — verified by
  test (a Surya `10 V/m` vs `1O V/m` on a Requirement quarantines).
- **Three-parser table geometry (T7)** — `table_geometry.py` adds the Docling
  (`docling_grid`, off the cells) and PyMuPDF (`pymupdf_grid`, word-bbox
  clustering) opinions to join pdfplumber's; `assemble._apply_table_geometry_consensus`
  quarantines a table whose grid the parsers don't agree on. Same
  measurement-driven calibration: Docling (layout) and pdfplumber (ruling lines)
  agree on n_rows/n_cols on **all 7 clean DNVGL tables** and disagree only on
  genuinely ambiguous ones, so they are the genuine voters; PyMuPDF word
  clustering counts each wrapped line of a multi-line cell as a row (a 14-row
  table reads as 52), so it is an approximate corroborator, never a hard voter.
  Result on the eval corpus: DNVGL 0 table quarantines (clean), TL **6** (3
  borderless that pdfplumber can't see + 3 real Docling-vs-pdfplumber geometry
  disagreements) — all spec-correct merged-cell-collapse guards. A genuine third
  table-structure parser (Camelot / second layout model) is the deferred swap-in
  to reach true three-way.
- **Parameter richness (T8)** — `parameters.parse_parameters` now emits the
  full `Parameter` sub-schema (canonical-model.md): **asymmetric** tolerances
  ("10 +0.5/-0.2 V/m"), **relative** tolerances ("± 5 %"), and **range**
  parameters ("10 - 15 V/m" → `comparator="range"`, `range=(lo, hi)`). Decimal
  parsing is now **language-aware** (`_to_decimal(surface, lang)`): a decimal
  comma is resolved from the document language instead of an unconditional
  `","→"."` that silently turned the EN thousands value `1,500` into `1.5`; a
  comma-number in a point-decimal / unknown locale is flagged
  `review_required` + `ambiguous_decimal_locale` rather than guessed
  (verification-rules.md "quarantine when document language is ambiguous").
- **Review-queue visibility** — `eval_metrics.DocMetrics` gains
  `consensus_quarantined`/`consensus_majority`: the gate-outcome count alone
  misses the geometry quarantines (which set `node.consensus` directly), so the
  benchmark now reports the true review-queue size (TL 18, DNVGL 23, DIN 21 on
  the eval corpus, incl. table-geometry disagreements).

### Changed — Wave 1 accuracy fixes (eval-driven, pipeline 0.8.2)

Driven by the 3-doc eval report + a gap analysis against the updated
`grc-doc-ingestion` skill/references. All reuse-and-extend, no new deps.

- **Parameter precision (T1)** — `parameters._cell_parameter` no longer promotes
  every numeric table cell to a `Parameter`. A cell becomes a Parameter only in
  a **limit context** (a comparator symbol in the cell, or a limit keyword —
  `limit`/`grenzwert`/`max`/`min`/… — in the column header) and with a resolved
  unit. This kills the largest quarantine driver: a 69-cell *conditions* table
  (frequency / forward power) previously emitted 21 comparator-less "value"
  params that flooded the units gate; now 0. `canon_units._HEADER_UNIT` also
  parses prose-form column units ("Frequenz **in MHz**"), not just parenthesized
  `(dBµV/m)`. Corpus params dropped from over-extraction while genuine limit
  columns are retained.
- **Definition-clause nesting + labeling (T2/T3)** —
  `topology.nest_by_clause` now nests clause-labeled `paragraph`/`list_item`
  nodes (a DIN Terms-&-Definitions entry "3.24 Angleichung" is often typed a
  paragraph), and `assign_clause_ids` no longer counts a trailing parenthetical
  gloss against the short-title bar, so "3.28 Anstieg des Spektrums (siehe auch
  Bild 1)" (7 words with the gloss, 4 without) gets its clause_id. Heading
  recall 0.945 → 0.955.
- **Numbering-gate honesty (T2)** — `gates/numbering.py` now filters a reported
  gap against a **whole-document clause inventory**: a clause that is present
  but merely mis-nested (Docling buries a definition paragraph under the
  previous clause) is not a *drop* and is no longer flagged. The DIN doc's three
  numbering findings — two of which were false ("expected 3.24", "expected
  3.28", both present) — become two **genuine** drop reports naming the truly
  absent clauses (`['3.25']`, `['3.33','3.34','3.35','3.36']`).
- **Continuation gate (T4)** — `gates/continuation.py` compares header
  signatures with `continuity._norm` (NFC + casefold + whitespace, matching the
  upstream stitcher instead of being stricter than it) and runs the
  partial-match quarantine only on real `is_column_header` cells, not the row-0
  fallback. Removes the DNVGL false "partial header match (50%)" quarantines on
  two data-row fragments Docling split on one page.
- **Net:** gate quarantines 71 → 61 across the 3-doc corpus, and the survivors
  are now genuine (real missing-comparator limits, real dropped clauses, real
  unresolved xrefs) rather than false-parameter noise.

Deferred (recorded, not silently dropped): the *structural* hoist of clause
nodes Docling buried (the tree still mis-nests 3.24 under 3.23 even though the
gate no longer misreads it); recovery of genuinely-dropped definitions
(3.25/3.33–3.36 — a Docling two-column reading-order drop, needs extract-level
work); numeric-fidelity loss from superscript/subscript reflow in prose (DIN
0.839 — the runs/inline-math accuracy path). These map to Wave 2/3.

### Added — combined benchmark/accuracy/verify_extraction report (`app/cli/eval_report.py`)

One report over the same randomly sampled documents, instead of three tools
that each did their own random draw:

- `eval_metrics.DocMetrics` extended with the Phase 1-6 signal it previously
  couldn't see: `gates_quarantined`/`gates_repaired`/`gates_by_gate` (copied
  from `pipeline_provenance["gates"]`), `cdm_type_counts`, `parameters_total`,
  `runs_coverage`.
- `ArtifactStore.edition_path(key)` -- public accessor for a cached edition's
  on-disk path (was previously private-only), so a caller can point a
  subprocess at it.
- `app/cli/eval_report.py` (+ `scripts/eval_report.sh`): draws N random PDFs
  once, and for each runs the structural benchmark, the factual-accuracy
  scorecard (`accuracy_check.check_document`), and `scripts/verify_extraction.py`
  as an actual subprocess against the SAME cached `CanonicalEdition` --
  `assemble()` runs exactly once per document. Writes a timestamped JSON+
  Markdown report to `data/eval-reports/` and overwrites the committed
  `docs/EVAL_REPORT.md`.
- Verified on 3 real sampled standards (DIN EN 60068-2-64, DNVGL-CG-0339,
  TL 81000): the gate-quarantine count embedded by `assemble()` and the count
  from re-running `verify_extraction.py` standalone over the persisted
  edition matched exactly on all 3 docs (19/19, 29/29, 23/23) -- confirms the
  gates are deterministic, not order- or cache-dependent. Standalone
  `accuracy_check.py --doc` on one sampled file reproduced the combined
  report's numbers exactly.

### Added — N-version consensus re-architecture (CDM v2 · runs · gates · typing)

Re-architecture toward the `grc-doc-ingestion` spec (`docs/references/*`):
extraction as an N-version consensus engine with per-character `runs`, a closed
normative type set, `Decimal` `Parameter`s, and eight deterministic admission
gates. Incremental — additive to the existing Docling pipeline, all prior tests
still green. Pipeline version → **0.8.0**, schema → **2.0**.

- **CDM v2** (`canonical_schema.py`): `Run` (font/size/`baseline_offset`/
  `vertical_align`/bbox) + `reconstruct_raw_text` emitting Unicode super/
  subscript codepoints; `Parameter` (`Decimal` value — never float — required
  `comparator` gte/lte/eq/range, `Tolerance`, `condition`, `quantity_kind`),
  superseding `Quantity`; closed `CDMType` literal + `_NORMATIVE_CDM_TYPES`;
  `consensus` state, `parsers` candidate dict, `quarantine_reason`, `repairs`,
  table `header_rows`/`continues_from`/`continues_to`, equation `mathml`/
  `symbol_table`/`computes_limit`. All new fields optional — old nodes stay
  trivially `unanimous`, un-quarantined.
- **PyMuPDF runs extractor** (`app/pipeline/runs.py`): `rawdict` per-span
  font/baseline → `vertical_align`, reading-order clustering by baseline so a
  subscript sorts *within* its line (`H₂O`, not `HO₂`). The sole layer that can
  catch `10⁻³ V/m` flattening to `10-3` (which parses as 7). Back-filled into
  every text node in `assemble` by bbox intersection; PyMuPDF is the raw-text
  authority, so `raw_text` is reconstructed *from* the runs and Docling's
  transcription is kept as a `parsers` corroborator candidate.
- **Consensus engine** (`app/pipeline/consensus.py`) + **pdfplumber third
  table opinion** (`extract_pdfplumber.py`): the exact disagreement branch from
  `parser-consensus.md` (unanimous / majority / quarantined; normative objects
  require unanimity; authority-isolated → quarantine; never longest-string /
  most-alnum / LLM / merge). Tables require **all three** geometry parsers to
  agree on `n_rows`/`n_cols`/span-map or the table quarantines. NFKC+whitespace
  +dash-fold normalization that deliberately preserves case, `±`, `≤`, `°`, `µ`,
  confusables, and super/subscript. Surya 0.95 confidence ceiling.
- **Eight verification gates** (`app/pipeline/gates/`) in spec order — header/
  footer suppression → run-integrity → numbering monotonicity → table
  rectangularity → continuation stitching → modal-verb preservation → unit/
  tolerance integrity → equation integrity → cross-reference resolution — each
  `pass | repair(+auditable repairs entry) | quarantine`. Repair only when
  uniquely determined (a byte-identical running header injected mid-body);
  quarantine otherwise (a numbering gap is never guessed). Wired into `assemble`
  after `xref`; the review queue is summarized in `pipeline_provenance.gates`.
- **`scripts/verify_extraction.py`** rewritten to run the gate package over a
  real `CanonicalEdition` JSON: exit 0 clean, 1 quarantined, 2 doc-level alarm
  (unresolved internal cross-reference). A CI admission gate, not a scorecard —
  injecting a flattened-superscript corruption makes it exit non-zero.
- **Normative typing** (`classify_type.py`) + **modality lexicon**
  (`modality.py`): per-language modal set (EN/DE/FR — `il convient de` == should,
  never inferred by translating to English) → closed CDM type, admonition
  outranks modal, `shall` outranks `may`. **Parameter extraction**
  (`parameters.py`): prose and cell limits → `Decimal` Parameters with
  symbol/phrase comparators, `±` tolerance, frequency-band condition, and a
  unit→`quantity_kind` vocabulary. Missing comparator is left unset so the
  units gate quarantines it — never defaulted to `eq`.
- **Deferred but registered swap-ins** (documented in the authority matrix, not
  silently dropped): MinerU (equations, GPU/AGPL) and Surya 2 (scanned OCR, GPU,
  conditional-commercial weights) — Docling formula-enrichment + RapidOCR fill
  those lanes today; the DOCX lane (python-docx / OMML / revision marks). LTS
  dependency pins with upper bounds landed in `pyproject.toml` so extraction
  output stays reproducible across the content-address contract.

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
