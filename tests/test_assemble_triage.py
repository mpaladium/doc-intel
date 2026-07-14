"""Unit coverage for app/pipeline/assemble.py's triage wiring, isolated from
the real triage measurement (tests/test_pipeline_e2e.py already covers that
end-to-end against the real fixture) via monkeypatching
triage.classify_document so behavior is deterministic regardless of what
PyMuPDF actually measures on a given page.
"""

from pathlib import Path

import pytest

from app.pipeline import triage
from app.pipeline.triage import TriageResult
from app.pipeline import assemble as assemble_module
from app.pipeline.assemble import assemble
from tests.fixtures.make_test_pdf import build

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "standard_sample.pdf"


@pytest.fixture(scope="module")
def pdf_bytes():
    if not FIXTURE_PATH.exists():
        build(FIXTURE_PATH)
    return FIXTURE_PATH.read_bytes()


def _iter_all(node):
    yield node
    for child in node.children:
        yield from _iter_all(child)


def _fake_result(page_class: str) -> TriageResult:
    return TriageResult(page_class=page_class, char_count=100, garbage_ratio=0.0,
                         cid_ratio=0.0, long_run_ratio=0.0)


def _patch_triage(monkeypatch, num_pages: int, dirty_page_1indexed: int, dirty_class: str):
    results = [_fake_result("DIGITAL_CLEAN") for _ in range(num_pages)]
    results[dirty_page_1indexed - 1] = _fake_result(dirty_class)
    monkeypatch.setattr(assemble_module.triage, "classify_document", lambda doc: results)


def test_scanned_page_nodes_get_review_required_and_downgraded_confidence(pdf_bytes, monkeypatch):
    _patch_triage(monkeypatch, num_pages=10, dirty_page_1indexed=5, dirty_class="SCANNED")

    edition = assemble(pdf_bytes, source_sha256="deadbeef")

    page5_nodes = [n for n in _iter_all(edition.root) if n.provenance.page == 5 and n.text]
    assert page5_nodes
    for node in page5_nodes:
        assert node.review_required is True
        assert "page_class_scanned" in node.review_reasons
        assert node.provenance.confidence == 0.5

    # unaffected pages keep the flat placeholder confidence, untouched --
    # page 6 ("2 Normative references") is plain normative body content with
    # no independent review_required source (unlike e.g. the preface, which
    # section_role_classifier flags for review on its own).
    page6_nodes = [n for n in _iter_all(edition.root) if n.provenance.page == 6 and n.text]
    assert page6_nodes
    for node in page6_nodes:
        assert node.review_required is False
        assert node.provenance.confidence == 0.95


def test_digital_dirty_downgrade_value(pdf_bytes, monkeypatch):
    _patch_triage(monkeypatch, num_pages=10, dirty_page_1indexed=3, dirty_class="DIGITAL_DIRTY")

    edition = assemble(pdf_bytes, source_sha256="deadbeef")

    page3_nodes = [n for n in _iter_all(edition.root) if n.provenance.page == 3 and n.text]
    assert page3_nodes
    for node in page3_nodes:
        assert node.provenance.confidence == 0.75
        assert "page_class_digital_dirty" in node.review_reasons


def test_pipeline_provenance_page_classes_reflect_triage(pdf_bytes, monkeypatch):
    _patch_triage(monkeypatch, num_pages=10, dirty_page_1indexed=7, dirty_class="UNCERTAIN")

    edition = assemble(pdf_bytes, source_sha256="deadbeef")

    assert edition.pipeline_provenance["page_classes"]["7"] == "UNCERTAIN"
    assert edition.pipeline_provenance["page_classes"]["1"] == "DIGITAL_CLEAN"
    assert "engine_by_page" in edition.pipeline_provenance
    assert set(edition.pipeline_provenance["engine_by_page"]["7"]) == {
        "layout", "table", "equation", "text",
    }
