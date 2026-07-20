# ingestion-engine eval report

Generated 2026-07-16 23:29 UTC · pipeline_version `0.11.1` · sample dir `data/eval-samples` · seed `1610877652`

Combines the structural benchmark (`app/cli/evaluate_samples.py`), the factual-accuracy scorecard (`app/cli/accuracy_check.py`), and the verification-gate CI check (`scripts/verify_extraction.py`) over the same randomly sampled documents. Regenerate with `uv run python -m app.cli.eval_report`.

## Corpus rollup

- Documents: 3  ·  Pages: 30
- Paragraph coverage (page-weighted mean): 0.9603
- Heading recall: 105/110 (0.9545)
- Formula page recall: 0/0 (1.0)
- Caption attachment: 17/18 (0.9444)
- Table-region fidelity: 1201/1203 (0.9983)
- Gate quarantines / repairs (total): 59 / 0
- `verify_extraction.py`: 0/3 clean (exit 0), 2 document-level alarm(s) (exit 2)

## TL_81000_2021-09_GER_p093-102.pdf

**Status:** processed · 10 pages · page classes: {'DIGITAL_CLEAN': 10}

**Benchmark (structure)**
- node types: {'section': 19, 'table': 9, 'figure': 2, 'caption': 5, 'paragraph': 31, 'note': 2, 'list_item': 33} (max depth 5)
- tables: 9, cells: 265 (229/231 data cells with header_path)
- lists: 33 (nested: 0)
- languages: ['de', 'en'] (primary: de), 74/89 text nodes tagged
- equations: 0 (0 with LaTeX)
- runs coverage: 0.7978  ·  CDM types: {'Permission': 1, 'Recommendation': 1, 'Requirement': 5}  ·  parameters extracted: 19
- consensus: 18 quarantined, 2 majority (incl. table-geometry disagreements)
- review_required: 18  ·  mean confidence: 0.945

**Accuracy (vs. source text layer)**
- coverage: mean 0.965, min 0.866  ·  numeric fidelity: 0.958  ·  reading-order tau: 0.925
- headings: 10/10 (1.0)  ·  captions attached: 5/5 (1.0)  ·  table region: 1065/1065 (1.0)
- genuine content misses: 1

**Verification gates (`verify_extraction.py`)**
- exit code: 2 (document-level alarm)
- objects checked: 101  ·  quarantined: 14  ·  repaired: 0
- example findings:
  - `table_rectangularity` [quarantine] 65145109d1150e00#e3b0c44298fc~6: 1 uncovered cell(s), e.g. [(0, 0)] (dropped cell?)
  - `units` [quarantine] 65145109d1150e00#f78b2780cf60: parameter 'time' has no comparator
  - `units` [quarantine] 65145109d1150e00#c7c56c69d9d3: parameter 'time' has no comparator
  - `units` [quarantine] 65145109d1150e00#befa2f5be465: parameter 'length' has no comparator; parameter 'length' has no comparator; parameter 'length' has no comparator; parameter 'length' has no comparator; parameter 'length' has no comparator; parameter ... [26 more chars]
  - `units` [quarantine] 65145109d1150e00#e3b0c44298fc~4: parameter 'frequency' has no comparator; parameter 'frequency' has no comparator; parameter 'frequency' has no comparator; parameter 'frequency' has no comparator
  - ... 9 more

## DNVGL-CG-0339_Dez_2019_p023-032.pdf

**Status:** processed · 10 pages · page classes: {'DIGITAL_CLEAN': 10}

**Benchmark (structure)**
- node types: {'section': 67, 'paragraph': 106, 'figure': 3, 'caption': 10, 'table': 7, 'list_item': 8} (max depth 5)
- tables: 7, cells: 73 (54/54 data cells with header_path)
- lists: 8 (nested: 0)
- languages: ['en'] (primary: en), 114/190 text nodes tagged
- equations: 0 (0 with LaTeX)
- runs coverage: 0.6526  ·  CDM types: {'Requirement': 55, 'Recommendation': 1, 'Permission': 2}  ·  parameters extracted: 32
- consensus: 23 quarantined, 3 majority (incl. table-geometry disagreements)
- review_required: 23  ·  mean confidence: 0.9478

**Accuracy (vs. source text layer)**
- coverage: mean 1.0, min 1.0  ·  numeric fidelity: 0.945  ·  reading-order tau: 0.992
- headings: 60/60 (1.0)  ·  captions attached: 9/10 (0.9)  ·  table region: 136/138 (0.986)
- genuine content misses: 0

**Verification gates (`verify_extraction.py`)**
- exit code: 1 (quarantined objects)
- objects checked: 201  ·  quarantined: 24  ·  repaired: 0
- example findings:
  - `table_rectangularity` [quarantine] 553a2442043ef531#e3b0c44298fc~9: 2 uncovered cell(s), e.g. [(1, 0), (1, 1)] (dropped cell?)
  - `units` [quarantine] 553a2442043ef531#6502c035d42a: parameter 'temperature' has no comparator; parameter 'length' has no comparator
  - `units` [quarantine] 553a2442043ef531#c68bfd40189c: parameter 'ratio' has no comparator
  - `units` [quarantine] 553a2442043ef531#b60512739402: parameter 'temperature' has no comparator; symbol '±' in runs dropped from parameters
  - `units` [quarantine] 553a2442043ef531#10486622e631: parameter 'ratio' has no comparator; symbol '±' in runs dropped from parameters
  - ... 19 more

## DIN_EN_60068-2-64-2009_p013-022.pdf

**Status:** processed · 10 pages · page classes: {'DIGITAL_CLEAN': 10}

**Benchmark (structure)**
- node types: {'section': 50, 'paragraph': 114, 'list_item': 30, 'figure': 3, 'caption': 3, 'equation': 3} (max depth 6)
- tables: 0, cells: 0 (0/0 data cells with header_path)
- lists: 30 (nested: 0)
- languages: ['de', 'en'] (primary: de), 160/199 text nodes tagged
- equations: 3 (3 with LaTeX)
- runs coverage: 0.7387  ·  CDM types: {'Permission': 5, 'Requirement': 36, 'Recommendation': 5}  ·  parameters extracted: 33
- consensus: 22 quarantined, 0 majority (incl. table-geometry disagreements)
- review_required: 22  ·  mean confidence: 0.948

**Accuracy (vs. source text layer)**
- coverage: mean 0.916, min 0.731  ·  numeric fidelity: 0.839  ·  reading-order tau: 0.961
- headings: 35/40 (0.875)  ·  captions attached: 3/3 (1.0)  ·  table region: 0/0 (1.0)
- genuine content misses: 8

**Verification gates (`verify_extraction.py`)**
- exit code: 2 (document-level alarm)
- objects checked: 203  ·  quarantined: 21  ·  repaired: 0
- example findings:
  - `numbering` [quarantine] DIN EN 60068-2-64#3.26: dropped clause(s) ['3.25'] between 3.24 and 3.26 (absent from the whole document)
  - `numbering` [quarantine] DIN EN 60068-2-64#3.37: dropped clause(s) ['3.33', '3.34', '3.35', '3.36'] between 3.32 and 3.37 (absent from the whole document)
  - `units` [quarantine] DIN EN 60068-2-64#3.24: parameter 'current' has no comparator
  - `units` [quarantine] DIN EN 60068-2-64#3.28: parameter 'current' has no comparator
  - `units` [quarantine] 177d792c4782bfe7#1418e4ad10e3: parameter 'frequency' has no comparator; parameter 'frequency' has no comparator
  - ... 16 more
