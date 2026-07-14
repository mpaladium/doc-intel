"""Merge-gate test (ARCHITECTURE.md §2.3 / build-order step 3): the
section-role classifier's false-exclusion rate on real, hand-labeled normative
clauses must be 0. Runs the real pipeline against the gold docs under
tests/goldsets/section_roles/ -- slower than a unit test (Docling conversion
per doc), but this is exactly the class of regression a unit test on synthetic
data cannot catch (see CHANGELOG for the title_page false-exclusion this gold
set found on real documents)."""

import pytest

from app.cli.evaluate_samples import _resolve_docs_dir
from app.cli.section_role_gold import GOLDSET_DIR, run
from app.store.artifact_store import ArtifactStore


@pytest.fixture(scope="module")
def docs_dir():
    resolved = _resolve_docs_dir(None)
    if resolved is None:
        pytest.skip("no accessible sample PDFs (see app/cli/evaluate_samples.py)")
    return resolved


def test_no_false_exclusions_on_gold_set(docs_dir, tmp_path_factory):
    store = ArtifactStore(tmp_path_factory.mktemp("gold_artifacts"))
    results, exit_code = run(GOLDSET_DIR, docs_dir, store)

    assert results, "gold set produced no results -- check goldset docs exist in the sample dir"
    all_false_exclusions = [d for r in results for d in r.false_exclusions]
    assert all_false_exclusions == [], (
        f"false-exclusion gate failed -- normative content would be silently "
        f"dropped: {all_false_exclusions}"
    )
    assert exit_code == 0
