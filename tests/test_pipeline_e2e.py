import hashlib
from pathlib import Path

import pytest

from canonical_schema import Node
from app.pipeline.assemble import assemble

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "standard_sample.pdf"


@pytest.fixture(scope="module")
def edition():
    if not FIXTURE_PATH.exists():
        from tests.fixtures.make_test_pdf import build
        build(FIXTURE_PATH)
    pdf_bytes = FIXTURE_PATH.read_bytes()
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    return assemble(pdf_bytes, source_sha256=sha, ocr_enabled=False)


def _iter_all(node: Node):
    yield node
    for child in node.children:
        yield from _iter_all(child)


def _by_heading(edition, heading: str) -> Node:
    for n in edition.root.children:
        if n.text == heading:
            return n
    raise AssertionError(f"no top-level section titled {heading!r}")


def test_every_node_has_sane_provenance(edition):
    for node in _iter_all(edition.root):
        assert node.provenance is not None
        assert node.provenance.page >= 1
        assert 0.0 <= node.provenance.confidence <= 1.0
        assert node.provenance.parser


def test_front_matter_excluded_but_not_deleted(edition):
    for heading, expected_role in [
        ("IEC 61000-6-3", "title_page"),
        ("Table of Contents", "toc"),
        ("Foreword", "foreword"),
    ]:
        section = _by_heading(edition, heading)
        assert section.section_role == expected_role
        assert section.compliance_relevant is False
        # never deleted -- content is still there, just flagged
        assert len(section.children) > 0 or section.text


def test_preface_excluded_but_always_flagged_for_review(edition):
    intro = _by_heading(edition, "Introduction")
    assert intro.section_role == "preface"
    assert intro.compliance_relevant is False
    assert intro.review_required is True  # AGENTS.md §1.5/§2.4: preface always review_required


def test_back_matter_index_excluded(edition):
    index = _by_heading(edition, "Index")
    assert index.section_role == "index"
    assert index.compliance_relevant is False


def test_numbered_normative_body_never_excluded(edition):
    for heading, clause_id in [
        ("1 Scope", "1"),
        ("2 Normative references", "2"),
        ("3 Terms and definitions", "3"),
        ("4 Test methods", "4"),
        ("4.1 General", "4.1"),
        ("4.2 Radiated emissions", "4.2"),
        ("4.2.3 Limits", "4.2.3"),
        ("5 Compliance criteria", "5"),
    ]:
        section = _by_heading(edition, heading)
        assert section.section_role == "normative"
        assert section.compliance_relevant is True
        assert section.clause_id == clause_id


def test_table_extracted_with_header_path(edition):
    tables = [n for n in _iter_all(edition.root) if n.type == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.cells is not None and len(table.cells) == 12

    header_row = [c for c in table.cells if c.row == 0]
    assert {c.text for c in header_row} == {
        "Frequency range (MHz)", "Limit (dBuV/m)", "Distance (m)",
    }

    data_cells = [c for c in table.cells if c.row > 0]
    assert all(c.header_path for c in data_cells)
    first_data_row = [c for c in data_cells if c.row == 1]
    assert {c.text for c in first_data_row} == {"30 - 230", "40", "10"}


def test_pipeline_provenance_recorded(edition):
    assert edition.pipeline_provenance["pipeline_version"]
    assert edition.pipeline_provenance["docling_version"]
    assert edition.pipeline_provenance["ocr_enabled"] is False
