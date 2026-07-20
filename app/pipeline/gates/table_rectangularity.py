"""Gate 3 (spec §"table rectangularity"): for every table, every (row, col)
cell position is covered exactly once once spans are expanded -- sum(colspan)
per row equals n_cols and sum(rowspan) per column equals n_rows.

Detects dropped cells, merged-cell collapse, header rows misread as data. A
table with an ambiguous cell is worse than no table, because downstream it will
silently produce a parameter comparison against a hole -> quarantine (no
repair: which cell was dropped is not uniquely determined).

Additional sub-check (verification-rules.md): no MANDATORY column is empty --
"mandatory" is per-table-role, and a limit table's limit column is the case that
matters (a hole in a limit column is a compliance comparison against nothing).
The units-row consistency sub-check (a units row agrees with the values it
governs) is fuzzier -- distinguishing a genuine units row from a data row of
unit-like strings needs table-role modelling -- and is deferred.
"""

from __future__ import annotations

from canonical_schema import Node
from app.pipeline.gates import GateReport, Outcome, quarantine, transform_tree
from app.pipeline.parameters import _LIMIT_KEYWORD


def _coverage_ok(cells) -> tuple[bool, str]:
    """Expand every cell over its rowspan x colspan footprint; each (r, c) must
    be covered exactly once and the covered region must be a full rectangle."""
    if not cells:
        return False, "table has no cells"
    covered: dict[tuple[int, int], int] = {}
    max_r = max_c = 0
    for cell in cells:
        for dr in range(cell.rowspan):
            for dc in range(cell.colspan):
                pos = (cell.row + dr, cell.col + dc)
                covered[pos] = covered.get(pos, 0) + 1
                max_r = max(max_r, pos[0])
                max_c = max(max_c, pos[1])
    n_rows, n_cols = max_r + 1, max_c + 1

    overlaps = [pos for pos, n in covered.items() if n > 1]
    if overlaps:
        return False, f"cells overlap at {sorted(overlaps)[:5]} (merged-cell collapse?)"
    holes = [(r, c) for r in range(n_rows) for c in range(n_cols) if (r, c) not in covered]
    if holes:
        return False, f"{len(holes)} uncovered cell(s), e.g. {holes[:5]} (dropped cell?)"
    return True, ""


def _mandatory_column_ok(cells) -> tuple[bool, str]:
    """No limit-bearing column (its header matches a limit keyword) has an empty
    data cell. A blank in a limit column is a compliance comparison against
    nothing -- worth a human look even when the grid is rectangular."""
    header_rows = {c.row for c in cells if c.is_column_header} or {0}
    for c in cells:
        if c.row in header_rows or not c.header_path:
            continue
        if any(_LIMIT_KEYWORD.search(h) for h in c.header_path) and not (c.text or "").strip():
            return False, (f"empty cell in a mandatory limit column "
                           f"(row {c.row}, col {c.col}, header {c.header_path})")
    return True, ""


def _check_node(node: Node) -> tuple[Node, list[Outcome]]:
    if node.type != "table" or node.cells is None:
        return node, []
    ok, reason = _coverage_ok(node.cells)
    if not ok:
        node, out = quarantine(node, "table_rectangularity", reason)
        return node, [out]
    ok, reason = _mandatory_column_ok(node.cells)
    if not ok:
        node, out = quarantine(node, "table_rectangularity", reason)
        return node, [out]
    return node, []


def check(root: Node) -> GateReport:
    return transform_tree(root, _check_node)
