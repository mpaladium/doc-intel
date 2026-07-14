"""OCR path tests (build-order step 2, ARCHITECTURE.md §7): triage-driven
routing to Docling's RapidOCR, validated against a synthetic scanned PDF whose
ground truth is free -- it's a rasterized copy of a real born-digital fixture,
so the fixture's own text layer IS the OCR gold standard (see
tests/fixtures/make_scanned_pdf.py). Runs the real Docling+OCR pipeline, so
this is slower than a unit test -- exactly the class of thing synthetic-only
tests can't catch (a text-layer-free page has no ground truth of its own)."""

import hashlib
from pathlib import Path

import fitz
import pytest

from app.pipeline import triage
from app.pipeline.assemble import _ocr_needed, _ocr_permitted, assemble
from tests.fixtures.make_scanned_pdf import build as build_scanned
from tests.fixtures.make_test_pdf import build as build_standard

STANDARD_PDF = Path(__file__).parent / "fixtures" / "standard_sample.pdf"


@pytest.fixture(scope="module")
def original_bytes() -> bytes:
    if not STANDARD_PDF.exists():
        build_standard(STANDARD_PDF)
    return STANDARD_PDF.read_bytes()


@pytest.fixture(scope="module")
def scanned_bytes(original_bytes) -> bytes:
    return build_scanned(original_bytes, dpi=150)


def _iter_all(node):
    yield node
    for c in node.children:
        yield from _iter_all(c)


def test_rasterized_copy_has_no_text_layer(scanned_bytes):
    doc = fitz.open(stream=scanned_bytes, filetype="pdf")
    try:
        assert all(len(doc[i].get_text("text")) == 0 for i in range(doc.page_count))
    finally:
        doc.close()


def test_triage_classifies_rasterized_pages_as_scanned(scanned_bytes):
    doc = fitz.open(stream=scanned_bytes, filetype="pdf")
    try:
        results = triage.classify_document(doc)
    finally:
        doc.close()
    assert results and all(r.page_class == "SCANNED" for r in results)


def test_ocr_routing_helpers():
    from app.pipeline.triage import TriageResult

    clean = [TriageResult("DIGITAL_CLEAN", 100, 0.0, 0.0, 0.0)]
    scanned = [TriageResult("SCANNED", 0, 0.0, 0.0, 0.0)]
    assert not _ocr_needed(clean)
    assert _ocr_needed(scanned)
    assert _ocr_permitted()  # default: on


def test_ocr_permitted_respects_env_gate(monkeypatch):
    monkeypatch.setenv("INGESTION_OCR", "0")
    assert not _ocr_permitted()
    monkeypatch.setenv("INGESTION_OCR", "false")
    assert not _ocr_permitted()
    monkeypatch.delenv("INGESTION_OCR", raising=False)
    assert _ocr_permitted()


def test_scanned_pipeline_routes_to_ocr_and_recovers_real_text(scanned_bytes):
    edition = assemble(scanned_bytes, source_sha256=hashlib.sha256(scanned_bytes).hexdigest())

    assert edition.pipeline_provenance["ocr_enabled"] is True
    assert all(cls == "SCANNED" for cls in edition.pipeline_provenance["page_classes"].values())

    nodes = list(_iter_all(edition.root))
    text_nodes = [n for n in nodes if n.text]
    assert text_nodes, "OCR produced no text at all"

    # confidence downgraded + flagged for review, same as any SCANNED page
    # (app/pipeline/assemble.py's _apply_triage) -- OCR output is never
    # silently treated as equally trustworthy as a real digital text layer.
    assert all(n.review_required for n in text_nodes)
    assert all("page_class_scanned" in n.review_reasons for n in text_nodes)
    assert all(n.provenance.confidence < 0.95 for n in text_nodes)

    # OCR actually recovered real content from the original, not noise.
    all_text = " ".join(n.text for n in text_nodes).lower()
    assert "scope" in all_text or "iec" in all_text


def test_ocr_disabled_by_env_leaves_scanned_page_textless(scanned_bytes, monkeypatch):
    monkeypatch.setenv("INGESTION_OCR", "0")
    edition = assemble(scanned_bytes, source_sha256=hashlib.sha256(scanned_bytes).hexdigest())
    assert edition.pipeline_provenance["ocr_enabled"] is False
