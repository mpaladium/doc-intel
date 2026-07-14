import hashlib
import re
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
    # search the whole tree: clauses now nest (4.1 under 4), so a section is not
    # necessarily a direct child of root.
    for n in _iter_all(edition.root):
        if n.type == "section" and n.text == heading:
            return n
    raise AssertionError(f"no section titled {heading!r}")


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


def _find_section(node, clause_id):
    for n in _iter_all(node):
        if n.type == "section" and n.clause_id == clause_id:
            return n
    return None


def test_clause_hierarchy_is_nested_not_flat(edition):
    # 4.1 / 4.2 nest under 4; 4.2.3 nests under 4.2 -- the compliance clause tree.
    c4 = _find_section(edition.root, "4")
    assert c4 is not None
    child_ids = {c.clause_id for c in c4.children if c.type == "section"}
    assert {"4.1", "4.2"}.issubset(child_ids)
    c42 = _find_section(edition.root, "4.2")
    assert "4.2.3" in {c.clause_id for c in c42.children if c.type == "section"}
    # top-level clauses stay at root
    root_clause_ids = {c.clause_id for c in edition.root.children if c.type == "section"}
    assert {"1", "2", "4", "5"}.issubset(root_clause_ids)


def test_table_cells_have_quantities_and_page(edition):
    table = next(n for n in _iter_all(edition.root) if n.type == "table")
    data_cells = [c for c in table.cells if not c.is_column_header]
    # limit values parsed into structured quantities
    assert any(c.quantity and c.quantity.value for c in data_cells)
    # cells carry source-page provenance
    assert all(c.page is not None for c in table.cells)


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


def test_triage_page_classes_and_engine_choices_recorded(edition):
    page_classes = edition.pipeline_provenance["page_classes"]
    engine_by_page = edition.pipeline_provenance["engine_by_page"]
    assert set(page_classes) == set(engine_by_page)
    assert all(
        cls in ("DIGITAL_CLEAN", "DIGITAL_DIRTY", "SCANNED", "UNCERTAIN")
        for cls in page_classes.values()
    )
    # dense dot-leader pages (TOC, index) measure dirtier than prose pages --
    # confirms triage is actually running against real page content, not a
    # placeholder.
    assert "DIGITAL_DIRTY" in page_classes.values()


def test_dirty_page_nodes_get_review_flag_and_downgraded_confidence(edition):
    dirty_pages = {
        int(p) for p, cls in edition.pipeline_provenance["page_classes"].items()
        if cls != "DIGITAL_CLEAN"
    }
    assert dirty_pages  # this fixture is expected to have at least one
    dirty_nodes = [n for n in _iter_all(edition.root) if n.provenance.page in dirty_pages and n.text]
    assert dirty_nodes
    for node in dirty_nodes:
        assert node.review_required is True
        assert any(r.startswith("page_class_") for r in node.review_reasons)
        assert node.provenance.confidence < 0.95  # downgraded from the flat placeholder


def test_no_spurious_sections_from_caption_text(edition):
    caption_like = re.compile(r"^(table|figure|fig\.)\s*\d+", re.IGNORECASE)
    for node in _iter_all(edition.root):
        if node.type == "section" and node.text:
            assert not caption_like.match(node.text.strip()), (
                f"caption-like text became a spurious section: {node.text!r}"
            )


def test_captions_are_typed_caption_nodes_not_paragraphs_or_sections(edition):
    captions = [n for n in _iter_all(edition.root) if n.type == "caption"]
    caption_texts = {n.text for n in captions}
    assert "Table 1 -- Radiated emission limits by frequency" in caption_texts
    assert "Figure 1 -- Test setup diagram" in caption_texts

    # never duplicated as a generic paragraph or a spurious section elsewhere
    other_nodes_with_caption_text = [
        n for n in _iter_all(edition.root)
        if n.type in ("paragraph", "section") and n.text in caption_texts
    ]
    assert other_nodes_with_caption_text == []


def test_figure_extracted(edition):
    figures = [n for n in _iter_all(edition.root) if n.type == "figure"]
    assert len(figures) == 1
