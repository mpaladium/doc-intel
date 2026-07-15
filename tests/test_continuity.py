"""Unit coverage for continuity.stitch / continuity.header_path
(app/pipeline/continuity.py) -- multi-page stitching and multi-row / spanned
column-header lineage. Pure functions over Node/Cell, no Docling needed."""

from canonical_schema import Cell, Node, Provenance
from app.pipeline import continuity


def _prov():
    return Provenance(page=1, bbox=(0, 0, 1, 1), parser="docling",
                      model_version="v1", confidence=0.9)


def _table(cells):
    return Node(id=f"t{id(cells)}", type="table", cells=cells, provenance=_prov())


def _hdr(row, col, text, colspan=1):
    return Cell(row=row, col=col, colspan=colspan, text=text, is_column_header=True)


def _data(row, col, text):
    return Cell(row=row, col=col, text=text)


def test_single_row_header_path():
    t = _table([
        _hdr(0, 0, "Freq"), _hdr(0, 1, "Limit"),
        _data(1, 0, "30-230"), _data(1, 1, "40"),
    ])
    out = continuity.assign_header_paths([t])[0]
    by = {(c.row, c.col): c for c in out.cells}
    assert by[(1, 0)].header_path == ["Freq"]
    assert by[(1, 1)].header_path == ["Limit"]


def test_multi_row_header_lineage_with_span():
    # Row 0: "Emissions" spans cols 0-1; Row 1: "Radiated", "Conducted".
    t = _table([
        _hdr(0, 0, "Emissions", colspan=2),
        _hdr(1, 0, "Radiated"), _hdr(1, 1, "Conducted"),
        _data(2, 0, "40"), _data(2, 1, "66"),
    ])
    out = continuity.assign_header_paths([t])[0]
    by = {(c.row, c.col): c for c in out.cells}
    assert by[(2, 0)].header_path == ["Emissions", "Radiated"]
    assert by[(2, 1)].header_path == ["Emissions", "Conducted"]


def test_fallback_to_row_zero_when_no_header_flags():
    t = _table([
        Cell(row=0, col=0, text="Freq"), Cell(row=0, col=1, text="Limit"),
        Cell(row=1, col=0, text="30-230"), Cell(row=1, col=1, text="40"),
    ])
    out = continuity.assign_header_paths([t])[0]
    by = {(c.row, c.col): c for c in out.cells}
    assert by[(1, 0)].header_path == ["Freq"]


def test_stitch_merges_continuation_with_repeated_header():
    t1 = _table([
        _hdr(0, 0, "Freq"), _hdr(0, 1, "Limit"),
        _data(1, 0, "30-230"), _data(1, 1, "40"),
    ])
    t2 = _table([
        _hdr(0, 0, "Freq"), _hdr(0, 1, "Limit"),  # repeated header
        _data(1, 0, "230-1000"), _data(1, 1, "47"),
    ])
    section = Node(id="s", type="section", children=[t1, t2], provenance=_prov())
    out = continuity.stitch([section])[0]
    tables = [c for c in out.children if c.type == "table"]
    assert len(tables) == 1  # merged into one
    data_rows = sorted({c.row for c in tables[0].cells if not c.is_column_header})
    assert len(data_rows) == 2  # both data rows kept
    texts = {c.text for c in tables[0].cells}
    assert {"30-230", "230-1000"}.issubset(texts)


def test_stitch_does_not_merge_different_headers():
    t1 = _table([_hdr(0, 0, "Freq"), _data(1, 0, "x")])
    t2 = _table([_hdr(0, 0, "Voltage"), _data(1, 0, "y")])  # different header
    section = Node(id="s", type="section", children=[t1, t2], provenance=_prov())
    out = continuity.stitch([section])[0]
    assert len([c for c in out.children if c.type == "table"]) == 2


def test_stitch_does_not_merge_different_column_counts():
    t1 = _table([_hdr(0, 0, "Freq"), _hdr(0, 1, "Limit"), _data(1, 0, "x"), _data(1, 1, "y")])
    t2 = _table([_hdr(0, 0, "Freq"), _data(1, 0, "z")])  # only 1 column
    section = Node(id="s", type="section", children=[t1, t2], provenance=_prov())
    out = continuity.stitch([section])[0]
    assert len([c for c in out.children if c.type == "table"]) == 2


def test_stitch_preserves_each_cells_source_page():
    # The continuation's data cells keep their OWN page through the merge, so a
    # multi-page table doesn't collapse all cells onto the first page.
    t1 = _table([
        Cell(row=0, col=0, text="Freq", is_column_header=True, page=7),
        Cell(row=0, col=1, text="Limit", is_column_header=True, page=7),
        Cell(row=1, col=0, text="30-230", page=7), Cell(row=1, col=1, text="40", page=7),
    ])
    t2 = _table([
        Cell(row=0, col=0, text="Freq", is_column_header=True, page=8),
        Cell(row=0, col=1, text="Limit", is_column_header=True, page=8),
        Cell(row=1, col=0, text="230-1000", page=8), Cell(row=1, col=1, text="47", page=8),
    ])
    section = Node(id="s", type="section", children=[t1, t2], provenance=_prov())
    merged = [c for c in continuity.stitch([section])[0].children if c.type == "table"][0]

    page_of = {c.text: c.page for c in merged.cells}
    assert page_of["30-230"] == 7
    assert page_of["230-1000"] == 8  # continuation data cell keeps page 8
    assert sorted({c.page for c in merged.cells}) == [7, 8]


# --------------------------------------------------------------------------- #
# Stitch decision F1 (SKILLS.md gate: continuity.stitch F1 >= 0.95). A
# deterministic gold set of continuation / non-continuation table pairs; the
# stitch decision must score F1 = 1.0 on it. Guards against a regression that
# either over-merges (unrelated tables) or under-merges (real continuations).
# --------------------------------------------------------------------------- #
def _hdr_row(cols):
    return [_hdr(0, i, c) for i, c in enumerate(cols)]


def _stitch_decision(prev_cells, next_cells) -> bool:
    """Did the stitcher merge these two adjacent tables into one?"""
    section = Node(id="s", type="section", provenance=_prov(),
                   children=[_table(prev_cells), _table(next_cells)])
    out = continuity.stitch([section])[0]
    return len([c for c in out.children if c.type == "table"]) == 1


def test_stitch_decision_f1_on_gold_pairs():
    same_hdr = _hdr_row(["Freq", "Limit"])
    # (prev, next, should_merge)
    gold = [
        # true continuations: same columns + repeated header
        (same_hdr + [_data(1, 0, "30-230"), _data(1, 1, "40")],
         _hdr_row(["Freq", "Limit"]) + [_data(1, 0, "230-1000"), _data(1, 1, "47")], True),
        (_hdr_row(["A", "B", "C"]) + [_data(1, 0, "1"), _data(1, 1, "2"), _data(1, 2, "3")],
         _hdr_row(["A", "B", "C"]) + [_data(1, 0, "4"), _data(1, 1, "5"), _data(1, 2, "6")], True),
        # non-continuations
        (same_hdr + [_data(1, 0, "x"), _data(1, 1, "y")],
         _hdr_row(["Voltage", "Time"]) + [_data(1, 0, "a"), _data(1, 1, "b")], False),  # diff header
        (_hdr_row(["Freq", "Limit"]) + [_data(1, 0, "x"), _data(1, 1, "y")],
         _hdr_row(["Freq"]) + [_data(1, 0, "z")], False),  # diff column count
    ]
    tp = fp = fn = tn = 0
    for prev, nxt, should in gold:
        merged = _stitch_decision(prev, nxt)
        if should and merged: tp += 1
        elif should and not merged: fn += 1
        elif not should and merged: fp += 1
        else: tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 1.0
    assert f1 == 1.0, f"stitch F1={f1} (tp={tp} fp={fp} fn={fn} tn={tn})"
