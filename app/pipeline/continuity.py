"""continuity.stitch / continuity.header_path — multi-page table continuation.

A silent failure here looks like "table removed + table added" downstream and
is evidence-critical (ARCHITECTURE.md §2.1). `stitch` merges consecutive table
siblings that are really one table split across a page break (detected via
repeated-header rows); `assign_header_paths` then gives every data cell its
full column-header lineage, which is the cell's diff identity
(`canonical_schema.Cell.header_path`), not its raw grid position.
"""

from __future__ import annotations

from canonical_schema import Cell, Node


def _row_signature(cells: list[Cell], row: int) -> tuple[str, ...]:
    return tuple(c.text.strip().casefold() for c in cells if c.row == row)


def _header_row_index(node: Node) -> int | None:
    return 0 if node.cells else None


def _is_continuation(prev: Node, nxt: Node) -> bool:
    """Same header row (by text) repeated at the top of the next table -- the
    language-independent, structural signal for "this is a page-split
    continuation", not a new table."""
    if not prev.cells or not nxt.cells:
        return False
    prev_header_row = _header_row_index(prev)
    nxt_header_row = _header_row_index(nxt)
    if prev_header_row is None or nxt_header_row is None:
        return False
    prev_sig = _row_signature(prev.cells, prev_header_row)
    nxt_sig = _row_signature(nxt.cells, nxt_header_row)
    return bool(prev_sig) and prev_sig == nxt_sig


def _merge_tables(prev: Node, nxt: Node) -> Node:
    row_offset = max((c.row + c.rowspan for c in prev.cells), default=0)
    merged_cells = list(prev.cells)
    for c in nxt.cells:
        if c.row == 0:
            continue  # drop the repeated header row from the continuation
        merged_cells.append(c.model_copy(update={"row": c.row + row_offset - 1}))
    return prev.model_copy(update={"cells": merged_cells})


def stitch(nodes: list[Node]) -> list[Node]:
    """Depth-first: within each node's children, merge consecutive table
    siblings whose header row repeats. Recurses into non-table children first
    so nested tables (e.g. inside a section) are stitched too."""
    stitched_children: list[Node] = []
    for node in nodes:
        node = node.model_copy(update={"children": stitch(node.children)})
        if (
            node.type == "table"
            and stitched_children
            and stitched_children[-1].type == "table"
            and _is_continuation(stitched_children[-1], node)
        ):
            stitched_children[-1] = _merge_tables(stitched_children[-1], node)
        else:
            stitched_children.append(node)
    return stitched_children


def _assign_table_header_paths(node: Node) -> Node:
    if node.type != "table" or not node.cells:
        return node
    header_row = _header_row_index(node)
    if header_row is None:
        return node
    col_headers: dict[int, str] = {
        c.col: c.text.strip() for c in node.cells if c.row == header_row
    }
    new_cells = [
        c if c.row == header_row else c.model_copy(update={"header_path": [col_headers.get(c.col, "")]})
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
