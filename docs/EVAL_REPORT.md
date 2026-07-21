# ingestion-engine eval report

Generated 2026-07-21 19:11 UTC · pipeline_version `0.13.0` · sample dir `data/eval-samples` · seed `4242`

Combines the structural benchmark (`app/cli/evaluate_samples.py`), the factual-accuracy scorecard (`app/cli/accuracy_check.py`), and the verification-gate CI check (`scripts/verify_extraction.py`) over the same randomly sampled documents. Regenerate with `uv run python -m app.cli.eval_report`.

## Corpus rollup

- Documents: 4  ·  Pages: 37
- Paragraph coverage (page-weighted mean): 0.9478
- Heading recall: 81/86 (0.9419)
- Formula page recall: 0/0 (1.0)
- Caption attachment: 14/15 (0.9333)
- Table-region fidelity: 1505/1523 (0.9882)
- Gate quarantines / repairs (total): 63 / 0
- `verify_extraction.py`: 0/4 clean (exit 0), 4 document-level alarm(s) (exit 2)

## TL_81000_2021-09_GER_p033-042.pdf

**Status:** processed · 10 pages · page classes: {'DIGITAL_CLEAN': 10}

**Benchmark (structure)**
- node types: {'section': 24, 'figure': 3, 'paragraph': 77, 'caption': 5, 'table': 7, 'note': 2, 'list_item': 4} (max depth 6)
- tables: 7, cells: 913 (759/842 data cells with header_path)
- lists: 4 (nested: 0)
- languages: ['de'] (primary: de), 73/111 text nodes tagged
- equations: 0 (0 with LaTeX)
- runs coverage: 0.7928  ·  CDM types: {'Requirement': 7, 'Recommendation': 1, 'Permission': 5}  ·  parameters extracted: 43
- consensus: 14 quarantined, 0 majority (incl. table-geometry disagreements)
- review_required: 14  ·  mean confidence: 0.9463

**Accuracy (vs. source text layer)**
- coverage: mean 0.95, min 0.904  ·  numeric fidelity: 0.976  ·  reading-order tau: 0.721
- headings: 16/16 (1.0)  ·  captions attached: 4/5 (0.8)  ·  table region: 952/967 (0.984)
- genuine content misses: 6

**Verification gates (`verify_extraction.py`)**
- exit code: 2 (document-level alarm)
- objects checked: 122  ·  quarantined: 12  ·  repaired: 0
- example findings:
  - `table_rectangularity` [quarantine] c456bde95439c3bd#e3b0c44298fc~6: empty cell in a mandatory limit column (row 8, col 6, header ['Grenzwert U in dB (µV)'])
  - `table_rectangularity` [quarantine] c456bde95439c3bd#e3b0c44298fc~9: empty cell in a mandatory limit column (row 4, col 9, header ['QP Grenzwert', 'f in kHz 5'])
  - `units` [quarantine] c456bde95439c3bd#987d2c1d9216: parameter 'frequency' has no comparator
  - `units` [quarantine] c456bde95439c3bd#7904c58c7645: parameter 'length' has no comparator
  - `units` [quarantine] c456bde95439c3bd#6d18a78754f2: parameter 'length' has no comparator
  - ... 7 more

## DNVGL-CG-0339_Dez_2019_p043-049.pdf

**Status:** processed · 7 pages · page classes: {'DIGITAL_CLEAN': 7}

**Benchmark (structure)**
- node types: {'section': 20, 'figure': 1, 'caption': 1, 'paragraph': 51, 'equation': 1, 'list_item': 46, 'table': 1} (max depth 4)
- tables: 1, cells: 7 (0/6 data cells with header_path)
- lists: 46 (nested: 0)
- languages: ['en'] (primary: en), 85/118 text nodes tagged
- equations: 1 (1 with LaTeX)
- runs coverage: 0.8305  ·  CDM types: {'Requirement': 6, 'Permission': 5, 'Scope': 1, 'Recommendation': 2}  ·  parameters extracted: 5
- consensus: 8 quarantined, 0 majority (incl. table-geometry disagreements)
- review_required: 8  ·  mean confidence: 0.9488

**Accuracy (vs. source text layer)**
- coverage: mean 1.0, min 1.0  ·  numeric fidelity: 1.0  ·  reading-order tau: 1.0
- headings: 21/21 (1.0)  ·  captions attached: 1/1 (1.0)  ·  table region: 77/77 (1.0)
- genuine content misses: 0

**Verification gates (`verify_extraction.py`)**
- exit code: 2 (document-level alarm)
- objects checked: 121  ·  quarantined: 6  ·  repaired: 0
- example findings:
  - `numbering` [quarantine] 53f22bc481b31a29#1: non-increasing sibling numbering: 16 then 1
  - `units` [quarantine] 53f22bc481b31a29#848f0e358f17: parameter 'length' has no comparator
  - `units` [quarantine] 53f22bc481b31a29#7c50c891ce60: parameter 'voltage' has no comparator; parameter 'voltage' has no comparator; parameter 'voltage' has no comparator; parameter 'voltage' has no comparator
  - `cross_reference` [quarantine] 53f22bc481b31a29#6c1857ea7658: unresolved internal reference(s) ['Table 2'] -- referenced object missing (document-level extraction alarm)
  - `cross_reference` [quarantine] 53f22bc481b31a29#0fb988b3009f: unresolved internal reference(s) ['Figure 4'] -- referenced object missing (document-level extraction alarm)
  - ... 1 more

## DIN_EN_60068-2-64-2009_p013-022.pdf

**Status:** processed · 10 pages · page classes: {'DIGITAL_CLEAN': 10}

**Benchmark (structure)**
- node types: {'section': 50, 'paragraph': 114, 'list_item': 30, 'figure': 3, 'caption': 3, 'equation': 3} (max depth 6)
- tables: 0, cells: 0 (0/0 data cells with header_path)
- lists: 30 (nested: 0)
- languages: ['de', 'en'] (primary: de), 160/199 text nodes tagged
- equations: 3 (3 with LaTeX)
- runs coverage: 0.7387  ·  CDM types: {'Permission': 5, 'Requirement': 36, 'Recommendation': 5}  ·  parameters extracted: 16
- consensus: 13 quarantined, 0 majority (incl. table-geometry disagreements)
- review_required: 13  ·  mean confidence: 0.948

**Accuracy (vs. source text layer)**
- coverage: mean 0.916, min 0.731  ·  numeric fidelity: 0.839  ·  reading-order tau: 0.961
- headings: 35/40 (0.875)  ·  captions attached: 3/3 (1.0)  ·  table region: 0/0 (1.0)
- genuine content misses: 8

**Verification gates (`verify_extraction.py`)**
- exit code: 2 (document-level alarm)
- objects checked: 203  ·  quarantined: 12  ·  repaired: 0
- example findings:
  - `numbering` [quarantine] DIN EN 60068-2-64#3.26: dropped clause(s) ['3.25'] between 3.24 and 3.26 (absent from the whole document)
  - `numbering` [quarantine] DIN EN 60068-2-64#3.37: dropped clause(s) ['3.33', '3.34', '3.35', '3.36'] between 3.32 and 3.37 (absent from the whole document)
  - `units` [quarantine] 177d792c4782bfe7#1418e4ad10e3: parameter 'frequency' has no comparator; parameter 'frequency' has no comparator
  - `units` [quarantine] 177d792c4782bfe7#fb0a4c5a522a: parameter 'frequency' has no comparator; parameter 'frequency' has no comparator; parameter 'gain' has no comparator; parameter 'ratio' has no comparator; parameter 'gain' has no comparator
  - `units` [quarantine] 177d792c4782bfe7#c3d501c1940c: parameter 'gain' has no comparator; parameter 'gain' has no comparator
  - ... 7 more

## TL_81000_2021-09_GER_p013-022.pdf

**Status:** processed · 10 pages · page classes: {'DIGITAL_CLEAN': 10}

**Benchmark (structure)**
- node types: {'section': 19, 'list_item': 32, 'paragraph': 49, 'figure': 6, 'table': 10, 'caption': 6} (max depth 5)
- tables: 10, cells: 252 (220/220 data cells with header_path)
- lists: 32 (nested: 0)
- languages: ['de'] (primary: de), 100/105 text nodes tagged
- equations: 0 (0 with LaTeX)
- runs coverage: 0.8286  ·  CDM types: {'Requirement': 16, 'Recommendation': 3, 'Permission': 2}  ·  parameters extracted: 19
- consensus: 37 quarantined, 5 majority (incl. table-geometry disagreements)
- review_required: 37  ·  mean confidence: 0.9439

**Accuracy (vs. source text layer)**
- coverage: mean 0.941, min 0.878  ·  numeric fidelity: 0.953  ·  reading-order tau: 0.951
- headings: 9/9 (1.0)  ·  captions attached: 6/6 (1.0)  ·  table region: 476/479 (0.994)
- genuine content misses: 5

**Verification gates (`verify_extraction.py`)**
- exit code: 2 (document-level alarm)
- objects checked: 122  ·  quarantined: 33  ·  repaired: 0
- example findings:
  - `units` [quarantine] 906470f6402c2047#bb66da89e825: parameter 'ratio' has no comparator
  - `units` [quarantine] 906470f6402c2047#c88362ca4c6c: parameter 'length' has no comparator
  - `units` [quarantine] 906470f6402c2047#0b84a7d42ac0: parameter 'length' has no comparator
  - `units` [quarantine] 906470f6402c2047#515843845938: parameter 'time' has no comparator
  - `units` [quarantine] 906470f6402c2047#bb66da89e825~1: parameter 'ratio' has no comparator
  - ... 28 more
