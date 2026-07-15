"""Unit coverage for nested_table.flag_nested_tables -- detect (never
reconstruct) a table flattened into a single cell, fail toward review."""

from canonical_schema import Cell, Node, Provenance
from app.pipeline.nested_table import flag_nested_tables


def _prov():
    return Provenance(page=1, bbox=(0, 0, 1, 1), parser="docling", model_version="v1", confidence=0.9)


def _table(cells):
    return Node(id="t", type="table", cells=cells, provenance=_prov())


def test_flags_cell_with_embedded_grid():
    nested = Cell(row=1, col=0, text="A 1 x\nB 2 y\nC 3 z")  # 3 rows x 3 cols crammed in
    table = _table([Cell(row=0, col=0, text="Header"), nested])
    out = flag_nested_tables(table)
    assert out.review_required is True
    assert "possible_nested_table" in out.review_reasons


def test_ordinary_multiline_prose_cell_not_flagged():
    prose = Cell(row=1, col=0, text="This is a normal\nmultiline note about the test.")
    table = _table([Cell(row=0, col=0, text="Header"), prose])
    out = flag_nested_tables(table)
    assert out.review_required is False
    assert "possible_nested_table" not in out.review_reasons


def test_single_line_cell_not_flagged():
    table = _table([Cell(row=0, col=0, text="H"), Cell(row=1, col=0, text="40 dBuV/m")])
    out = flag_nested_tables(table)
    assert out.review_required is False


def test_recurses_into_nested_sections():
    nested = Cell(row=1, col=0, text="A 1\nB 2\nC 3")
    table = _table([Cell(row=0, col=0, text="H"), nested])
    section = Node(id="s", type="section", text="4 Tables", children=[table], provenance=_prov())
    out = flag_nested_tables(section)
    assert out.children[0].review_required is True


def test_idempotent_does_not_double_append_reason():
    nested = Cell(row=1, col=0, text="A 1\nB 2")
    table = _table([nested])
    once = flag_nested_tables(table)
    twice = flag_nested_tables(once)
    assert twice.review_reasons.count("possible_nested_table") == 1
