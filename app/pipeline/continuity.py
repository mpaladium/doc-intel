"""continuity.stitch / continuity.header_path — multi-page table continuation
and column-header lineage.

A silent stitch failure looks like "table removed + table added" downstream
and is evidence-critical (ARCHITECTURE.md §2.1). `stitch` merges consecutive
table siblings that are really one table split across a page break; then
`assign_header_paths` gives every data cell its full column-header lineage
(the cell's diff identity, `canonical_schema.Cell.header_path`, not its raw
grid position).

Header detection uses the extractor's own `Cell.is_column_header` flag rather
than assuming "header = row 0", so **multi-row headers** (a spanning group
header above per-column sub-headers) produce a correct multi-level
`header_path`, and **column spans** are respected (a data cell picks up every
header cell whose column span covers it). Falls back to treating row 0 as the
header only when the extractor flagged no header cells at all.
"""

from __future__ import annotations

import unicodedata

from canonical_schema import Cell, Node


def _norm(text: str) -> str:
    """NFC + casefold + whitespace-collapse, for robust header comparison."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def _header_rows(cells: list[Cell]) -> list[int]:
    """Row indices that are column headers, in order. Uses the extractor's
    per-cell flag; falls back to {0} only if nothing was flagged (so a table
    with an unmarked header still gets a lineage)."""
    flagged = sorted({c.row for c in cells if c.is_column_header})
    if flagged:
        return flagged
    return [0] if cells else []


def _covers_col(cell: Cell, col: int) -> bool:
    return cell.col <= col < cell.col + cell.colspan


def _column_header_path(cells: list[Cell], header_rows: list[int], col: int) -> list[str]:
    """Header texts (top row -> bottom row) whose column span covers `col`,
    skipping empty header cells. This is the multi-row, span-aware lineage."""
    path: list[str] = []
    for hr in header_rows:
        for c in cells:
            if c.row == hr and _covers_col(c, col) and c.text.strip():
                path.append(c.text.strip())
                break
    return path


def _row_signature(cells: list[Cell], rows: list[int]) -> tuple[str, ...]:
    """Normalized text of all cells in the given header rows, in (row, col)
    order -- the fingerprint used to recognize a repeated header."""
    return tuple(
        _norm(c.text)
        for r in rows
        for c in sorted((c for c in cells if c.row == r), key=lambda c: c.col)
    )


def _col_count(cells: list[Cell]) -> int:
    return max((c.col + c.colspan for c in cells), default=0)


def _is_continuation(prev: Node, nxt: Node) -> bool:
    """`nxt` continues `prev` across a page break if they have the same column
    count AND `nxt`'s header rows repeat `prev`'s header rows (normalized).
    Column-count agreement alone is too weak (two unrelated 3-column tables);
    the repeated header is the real signal, per ARCHITECTURE.md §2.1."""
    if not prev.cells or not nxt.cells:
        return False
    if _col_count(prev.cells) != _col_count(nxt.cells):
        return False
    prev_sig = _row_signature(prev.cells, _header_rows(prev.cells))
    nxt_sig = _row_signature(nxt.cells, _header_rows(nxt.cells))
    return bool(prev_sig) and prev_sig == nxt_sig


def _merge_tables(prev: Node, nxt: Node) -> Node:
    """Append `nxt`'s data rows onto `prev`, dropping `nxt`'s repeated header
    rows and offsetting remaining row indices past `prev`'s last row."""
    next_row = max((c.row + c.rowspan for c in prev.cells), default=0)
    nxt_header_rows = set(_header_rows(nxt.cells))
    nxt_data = [c for c in nxt.cells if c.row not in nxt_header_rows]
    # Re-base nxt's data rows so they start right after prev's last row.
    base = min((c.row for c in nxt_data), default=0)
    merged = list(prev.cells)
    for c in nxt_data:
        merged.append(c.model_copy(update={"row": c.row - base + next_row}))
    return prev.model_copy(update={"cells": merged})


def stitch(nodes: list[Node]) -> list[Node]:
    """Depth-first: within each node's children, merge consecutive table
    siblings that continue one another. Recurses into children first so nested
    tables are stitched too."""
    stitched: list[Node] = []
    for node in nodes:
        node = node.model_copy(update={"children": stitch(node.children)})
        if (
            node.type == "table"
            and stitched
            and stitched[-1].type == "table"
            and _is_continuation(stitched[-1], node)
        ):
            stitched[-1] = _merge_tables(stitched[-1], node)
        else:
            stitched.append(node)
    return stitched


def _assign_table_header_paths(node: Node) -> Node:
    if node.type != "table" or not node.cells:
        return node
    header_rows = _header_rows(node.cells)
    header_set = set(header_rows)
    new_cells = [
        c if c.row in header_set
        else c.model_copy(update={"header_path": _column_header_path(node.cells, header_rows, c.col)})
        for c in node.cells
    ]
    return node.model_copy(update={"cells": new_cells})


def assign_header_paths(nodes: list[Node]) -> list[Node]:
    """Depth-first: give every table's data cells `header_path` -- the cell's
    diff identity across editions, independent of grid row/col position
    (canonical_schema.Cell docstring)."""
    out = []
    for node in nodes:
        node = node.model_copy(update={"children": assign_header_paths(node.children)})
        out.append(_assign_table_header_paths(node))
    return out
