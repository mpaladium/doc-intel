"""Test-session defaults.

Formula enrichment (`INGESTION_FORMULAS`) is disabled for the test suite:
it loads a VLM (CodeFormulaV2) and depends on Docling's layout model actually
isolating FORMULA regions, which is non-deterministic on the tiny synthetic
fixtures — so the e2e/assemble tests would be slow and flaky if it ran. The
formula code path is instead covered deterministically by the stub tests in
`test_extract_docling.py` (FormulaItem.text -> Node.latex mapping) and
`test_canon_equation.py` (LaTeX normalization); real-document formula
detection is measured by the eval harness (`app/cli/evaluate_samples.py`),
not the unit suite. A test that specifically wants enrichment can set
`INGESTION_FORMULAS=1` itself.
"""

import os


def pytest_configure(config):
    os.environ.setdefault("INGESTION_FORMULAS", "0")
