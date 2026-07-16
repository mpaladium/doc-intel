"""Gate 4 (spec §"continuation-table stitching"): a table fragment whose header
row is byte-identical to a preceding fragment's header, appearing at the top of
the next page, is a continuation.

Detects multi-page limit tables split into unrelated fragments -- extremely
common in IEC/CISPR and a top source of "the limit disappeared" false
positives. Set continues_from/continues_to on a clean (byte-identical header)
match; quarantine BOTH fragments on a partial header match (ambiguous). Do NOT
stitch on page adjacency alone -- two different tables can sit back to back.

`continuity.stitch` already merges clean continuations upstream in assemble;
this gate is the admission check on that -- it links fragments that survived as
siblings and quarantines the ambiguous partial matches stitch left alone.
"""

from __future__ import annotations

from canonical_schema import Node
from app.pipeline.continuity import _norm
from app.pipeline.gates import GateReport, Outcome, quarantine, repair


def _header_signature(node: Node) -> tuple[str, ...] | None:
    """The table's header row as a tuple of NORMALIZED cell texts (real
    `is_column_header` cells, else the row-0 fallback). Normalized with
    `continuity._norm` (NFC + casefold + whitespace) so a header the upstream
    stitcher already treated as matching isn't rejected here on a casing/space
    difference -- the gate must not be stricter than the stage it checks. None
    when the table has no discernible header."""
    if node.type != "table" or not node.cells:
        return None
    header_cells = [c for c in node.cells if c.is_column_header] or \
        [c for c in node.cells if c.row == 0]
    if not header_cells:
        return None
    return tuple(_norm(c.text) for c in sorted(header_cells, key=lambda c: c.col))


def _has_flagged_header(node: Node) -> bool:
    """True only when the extractor actually flagged column-header cells -- the
    partial-match quarantine runs only on real headers, never on the row-0
    fallback, so two data-row-0 fragments Docling split on one page aren't
    quarantined as an ambiguous continuation."""
    return bool(node.cells) and any(c.is_column_header for c in node.cells)


def _overlap(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    """Fraction of (normalized) header cells shared -- 1.0 == identical header."""
    if not a or not b:
        return 0.0
    common = sum(1 for x, y in zip(a, b) if x == y)
    return common / max(len(a), len(b))


def check(root: Node) -> GateReport:
    outcomes: list[Outcome] = []

    def visit(node: Node) -> Node:
        children = [visit(c) for c in node.children]
        # Walk sibling tables in order, comparing each to the previous table.
        prev_idx = None
        for i, child in enumerate(children):
            if child.type != "table":
                continue
            sig = _header_signature(child)
            if prev_idx is not None and sig is not None:
                prev = children[prev_idx]
                psig = _header_signature(prev)
                ov = _overlap(psig or (), sig)
                already_linked = child.continues_from or prev.continues_to
                if ov == 1.0 and not already_linked:
                    linked_prev, o1 = repair(
                        prev, "continuation", change={"continues_to": child.id},
                        reason=f"header-identical continuation -> {child.id}",
                        continues_to=child.id)
                    linked_cur, o2 = repair(
                        child, "continuation", change={"continues_from": prev.id},
                        reason=f"continuation of {prev.id}",
                        continues_from=prev.id)
                    children[prev_idx], children[i] = linked_prev, linked_cur
                    outcomes.extend([o1, o2])
                elif 0.0 < ov < 1.0 and _has_flagged_header(prev) and _has_flagged_header(child):
                    # partial match on REAL column headers -- genuinely ambiguous
                    # (is this a continuation with a slightly-changed header, or a
                    # different table?), quarantine both. Row-0 fallback fragments
                    # are excluded above: a partial match on non-header data cells
                    # is not evidence of a broken continuation.
                    q_prev, o1 = quarantine(prev, "continuation",
                                            f"partial header match with {child.id} ({ov:.0%})")
                    q_cur, o2 = quarantine(child, "continuation",
                                           f"partial header match with {prev.id} ({ov:.0%})")
                    children[prev_idx], children[i] = q_prev, q_cur
                    outcomes.extend([o1, o2])
            if sig is not None:
                prev_idx = i
        return node.model_copy(update={"children": children})

    return GateReport(root=visit(root), outcomes=outcomes)
