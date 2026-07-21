# ingestion-engine eval report

Generated 2026-07-21 18:22 UTC · pipeline_version `0.12.1` · sample dir `data/eval-samples` · seed `1480020808`

Combines the structural benchmark (`app/cli/evaluate_samples.py`), the factual-accuracy scorecard (`app/cli/accuracy_check.py`), and the verification-gate CI check (`scripts/verify_extraction.py`) over the same randomly sampled documents. Regenerate with `uv run python -m app.cli.eval_report`.

## Corpus rollup

- Documents: 3  ·  Pages: 34
- Paragraph coverage (page-weighted mean): 0.9823
- Heading recall: 82/93 (0.8817)
- Formula page recall: 0/0 (1.0)
- Caption attachment: 31/34 (0.9118)
- Table-region fidelity: 1763/1767 (0.9977)
- Gate quarantines / repairs (total): 48 / 2
- `verify_extraction.py`: 0/3 clean (exit 0), 1 document-level alarm(s) (exit 2)

## DNVGL-CG-0339_Dez_2019_p033-042.pdf

**Status:** processed · 10 pages · page classes: {'DIGITAL_CLEAN': 10}

**Benchmark (structure)**
- node types: {'section': 34, 'paragraph': 65, 'table': 11, 'caption': 12, 'note': 3, 'figure': 2, 'list_item': 4} (max depth 4)
- tables: 11, cells: 162 (142/142 data cells with header_path)
- lists: 4 (nested: 0)
- languages: ['en'] (primary: en), 90/117 text nodes tagged
- equations: 0 (0 with LaTeX)
- runs coverage: 0.7179  ·  CDM types: {'Requirement': 48, 'Permission': 3}  ·  parameters extracted: 31
- consensus: 18 quarantined, 4 majority (incl. table-geometry disagreements)
- review_required: 18  ·  mean confidence: 0.9454

**Accuracy (vs. source text layer)**
- coverage: mean 1.0, min 1.0  ·  numeric fidelity: 0.977  ·  reading-order tau: 0.983
- headings: 33/42 (0.786)  ·  captions attached: 11/12 (0.917)  ·  table region: 468/469 (0.998)
- genuine content misses: 0

**Verification gates (`verify_extraction.py`)**
- exit code: 1 (quarantined objects)
- objects checked: 131  ·  quarantined: 19  ·  repaired: 0
- example findings:
  - `continuation` [quarantine] 749b925431d9c856#e3b0c44298fc~1: partial header match with 749b925431d9c856#e3b0c44298fc~2 (50%)
  - `continuation` [quarantine] 749b925431d9c856#e3b0c44298fc~2: partial header match with 749b925431d9c856#e3b0c44298fc~1 (50%)
  - `continuation` [quarantine] 749b925431d9c856#e3b0c44298fc~4: partial header match with 749b925431d9c856#e3b0c44298fc~5 (50%)
  - `continuation` [quarantine] 749b925431d9c856#e3b0c44298fc~5: partial header match with 749b925431d9c856#e3b0c44298fc~4 (50%)
  - `units` [quarantine] 749b925431d9c856#2454e3e99312: parameter 'power' has no comparator
  - ... 14 more

## TL_81000_2018-03_p093-106.pdf

**Status:** processed · 14 pages · page classes: {'DIGITAL_CLEAN': 14}

**Benchmark (structure)**
- node types: {'section': 28, 'paragraph': 23, 'list_item': 40, 'table': 13, 'figure': 3, 'caption': 9, 'equation': 7, 'note': 3} (max depth 4)
- tables: 13, cells: 450 (398/398 data cells with header_path)
- lists: 40 (nested: 0)
- languages: ['de'] (primary: de), 90/109 text nodes tagged
- equations: 7 (7 with LaTeX)
- runs coverage: 0.6881  ·  CDM types: {'Requirement': 8, 'Permission': 2}  ·  parameters extracted: 15
- consensus: 32 quarantined, 5 majority (incl. table-geometry disagreements)
- review_required: 32  ·  mean confidence: 0.9385

**Accuracy (vs. source text layer)**
- coverage: mean 0.957, min 0.878  ·  numeric fidelity: 0.949  ·  reading-order tau: 0.964
- headings: 2/2 (1.0)  ·  captions attached: 9/9 (1.0)  ·  table region: 1031/1032 (0.999)
- genuine content misses: 1

**Verification gates (`verify_extraction.py`)**
- exit code: 2 (document-level alarm)
- objects checked: 126  ·  quarantined: 20  ·  repaired: 0
- example findings:
  - `table_rectangularity` [quarantine] 495be56f90f02565#e3b0c44298fc~7: 6 uncovered cell(s), e.g. [(11, 0), (11, 1), (11, 2), (11, 3), (11, 4)] (dropped cell?)
  - `units` [quarantine] 495be56f90f02565#c887915f2c01: parameter 'resistance' has no comparator
  - `units` [quarantine] 495be56f90f02565#5679a8118bd3: parameter 'time' has no comparator
  - `units` [quarantine] 495be56f90f02565#12d07e51d2b7: parameter 'ratio' has no comparator
  - `units` [quarantine] 495be56f90f02565#9b18e7fc4b76: parameter 'gain' has no comparator
  - ... 15 more

## DNVGL-CG-0339_Nov_2016_p023-032.pdf

**Status:** processed · 10 pages · page classes: {'DIGITAL_CLEAN': 10}

**Benchmark (structure)**
- node types: {'section': 54, 'figure': 3, 'caption': 13, 'paragraph': 90, 'list_item': 8, 'table': 11, 'note': 1} (max depth 5)
- tables: 11, cells: 115 (91/91 data cells with header_path)
- lists: 8 (nested: 0)
- languages: ['en'] (primary: en), 100/165 text nodes tagged
- equations: 0 (0 with LaTeX)
- runs coverage: 0.6788  ·  CDM types: {'Requirement': 49, 'Permission': 2}  ·  parameters extracted: 15
- consensus: 8 quarantined, 3 majority (incl. table-geometry disagreements)
- review_required: 8  ·  mean confidence: 0.9464

**Accuracy (vs. source text layer)**
- coverage: mean 1.0, min 1.0  ·  numeric fidelity: 0.948  ·  reading-order tau: 0.987
- headings: 47/49 (0.959)  ·  captions attached: 11/13 (0.846)  ·  table region: 264/266 (0.992)
- genuine content misses: 0

**Verification gates (`verify_extraction.py`)**
- exit code: 1 (quarantined objects)
- objects checked: 180  ·  quarantined: 9  ·  repaired: 0
- example findings:
  - `table_rectangularity` [quarantine] 696ed3d93c901d7f#e3b0c44298fc~7: 2 uncovered cell(s), e.g. [(1, 0), (1, 1)] (dropped cell?)
  - `continuation` [quarantine] 696ed3d93c901d7f#e3b0c44298fc~12: partial header match with 696ed3d93c901d7f#e3b0c44298fc~13 (50%)
  - `continuation` [quarantine] 696ed3d93c901d7f#e3b0c44298fc~13: partial header match with 696ed3d93c901d7f#e3b0c44298fc~12 (50%)
  - `units` [quarantine] 696ed3d93c901d7f#78e800fba870: parameter 'temperature' has no comparator
  - `units` [quarantine] 696ed3d93c901d7f#e3b0c44298fc~7: parameter 'resistance' has no comparator; parameter 'resistance' has no comparator; parameter 'resistance' has no comparator; parameter 'resistance' has no comparator
  - ... 4 more
