"""Unit coverage for the eval harness metrics (app/cli/eval_metrics.py) --
pure functions over a CanonicalEdition, so no Docling/model needed."""

from canonical_schema import CanonicalEdition, Cell, Node, Provenance
from app.cli.eval_metrics import compute_metrics


def _prov(page=1, conf=0.95):
    return Provenance(page=page, bbox=(0, 0, 1, 1), parser="docling",
                      model_version="v1", confidence=conf)


def _node(type_, text=None, children=None, **kw):
    return Node(id=f"n{id(text)}", type=type_, text=text, provenance=_prov(),
                children=children or [], **kw)


def _edition(root, lang_primary=None, page_classes=None):
    return CanonicalEdition(
        edition_id="e", source_sha256="s", schema_version="1.0",
        lang_primary=lang_primary, root=root,
        pipeline_provenance={"page_classes": page_classes or {}},
    )


def test_nesting_metrics_count_nested_list_items():
    inner = _node("list_item", text="b")
    outer = _node("list_item", text="a", children=[inner])
    root = _node("section", children=[outer])
    m = compute_metrics("d.pdf", _edition(root))
    assert m.list_items == 2
    assert m.nested_list_items == 1
    assert m.max_depth == 2  # section(0) -> list_item(1) -> list_item(2)


def test_table_header_path_coverage():
    cells = [
        Cell(row=0, col=0, text="H1"), Cell(row=0, col=1, text="H2"),
        Cell(row=1, col=0, header_path=["H1"], text="a"),
        Cell(row=1, col=1, header_path=[], text="b"),  # missing header_path
    ]
    table = Node(id="t", type="table", cells=cells, provenance=_prov())
    root = _node("section", children=[table])
    m = compute_metrics("d.pdf", _edition(root))
    assert m.tables == 1
    assert m.data_cells == 2
    assert m.data_cells_with_header_path == 1


def test_multi_row_header_not_miscounted_as_data_cells():
    # Rows 0 and 1 are both flagged headers; only row 2 is a data row. The
    # metric must not count the row-1 header cells as uncovered data cells.
    cells = [
        Cell(row=0, col=0, text="Group", is_column_header=True),
        Cell(row=1, col=0, text="Sub", is_column_header=True),
        Cell(row=2, col=0, header_path=["Group", "Sub"], text="v"),
    ]
    table = Node(id="t", type="table", cells=cells, provenance=_prov())
    root = _node("section", children=[table])
    m = compute_metrics("d.pdf", _edition(root))
    assert m.data_cells == 1
    assert m.data_cells_with_header_path == 1


def test_language_and_nfc_metrics():
    tagged = _node("paragraph", text="hello", lang="en")
    untagged = _node("paragraph", text="bonjour")
    root = _node("section", children=[tagged, untagged])
    m = compute_metrics("d.pdf", _edition(root, lang_primary="en"))
    assert m.text_nodes == 2
    assert m.lang_populated == 1
    assert m.distinct_langs == ["en"]
    assert m.lang_primary == "en"
    assert m.non_nfc_text_nodes == 0


def test_equation_latex_coverage():
    eq_with = _node("equation", text="E=mc^2", latex="E = mc^2")
    eq_without = _node("equation", text="x")
    root = _node("section", children=[eq_with, eq_without])
    m = compute_metrics("d.pdf", _edition(root))
    assert m.equation_nodes == 2
    assert m.equation_nodes_with_latex == 1


def test_page_class_and_uncertain_rate():
    root = _node("section")
    m = compute_metrics("d.pdf", _edition(root, page_classes={
        "1": "DIGITAL_CLEAN", "2": "UNCERTAIN", "3": "SCANNED", "4": "UNCERTAIN",
    }))
    assert m.pages == 4
    assert m.page_class_counts["UNCERTAIN"] == 2
    assert m.uncertain_rate == 0.5
