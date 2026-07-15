"""nested_table -- flag (never reconstruct) tables that TableFormer flattened.

A table nested inside another table's cell is a known ML-table-extractor
limitation: TableFormer emits the inner table's text crammed into a single
outer cell, losing the inner grid. We cannot faithfully reconstruct it, so --
per AGENTS.md §1.6 (fail toward review, make the reason inspectable) -- we
detect the signature and set `review_required` + a `possible_nested_table`
reason on the outer table node, so the confidence-sorted inspector surfaces it
for a human instead of silently trusting a flattened cell.

Signature: a single cell whose text holds >=2 newline-separated lines that
all tokenize into the SAME number of columns (>=2) -- i.e. a uniform mini-grid
packed into one cell. The column-count consistency is what separates a nested
grid from ordinary multi-line prose (whose lines have varying word counts).
"""

from __future__ import annotations

from canonical_schema import Cell, Node

_MIN_NESTED_ROWS = 2
_MIN_COLS_PER_ROW = 2
_REASON = "possible_nested_table"


def _cell_looks_nested(cell: Cell) -> bool:
    rows = [ln for ln in (cell.text or "").splitlines() if ln.strip()]
    if len(rows) < _MIN_NESTED_ROWS:
        return False
    col_counts = {len(ln.split()) for ln in rows}
    # a grid: every row has the same column count, and it's >= 2 columns.
    return len(col_counts) == 1 and next(iter(col_counts)) >= _MIN_COLS_PER_ROW


def flag_nested_tables(node: Node) -> Node:
    """Depth-first, same rebuild-children-first `model_copy` pattern as the
    other passes. Idempotent -- won't double-append the reason."""
    children = [flag_nested_tables(c) for c in node.children]
    node = node.model_copy(update={"children": children})

    if node.type == "table" and node.cells and any(_cell_looks_nested(c) for c in node.cells):
        if _REASON not in node.review_reasons:
            return node.model_copy(update={
                "review_required": True,
                "review_reasons": node.review_reasons + [_REASON],
            })
    return node
