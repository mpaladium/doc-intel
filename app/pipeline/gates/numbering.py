"""Gate 3 (spec §"numbering monotonicity"): within a section subtree, sibling
numbers are strictly increasing and the depth sequence is well-formed.

Detects reordered content, dropped clauses, misparsed multi-level numbering. A
gap is a QUARANTINE, never a repair: `5.1, 5.2, 5.4` might mean 5.3 was dropped
by the parser, or 5.3 doesn't exist in this edition, or 5.3 is on a page that
failed OCR -- three meanings, one symptom, and "the standard deleted 5.3" is a
finding while "our parser dropped 5.3" is a bug. Never guess between them.

Legitimately non-monotonic constructs are NOT flagged: annexes (`A.1`, `B.1`,
lettered so `_numeric_parts` returns None and they're skipped) and reserved
numbers (`5.3 (void)` is real and means something).
"""

from __future__ import annotations

from canonical_schema import Node
from app.pipeline.gates import GateReport, Outcome, quarantine
from app.pipeline.topology import _numeric_parts


def _same_level_siblings(children: list[Node]):
    """Numeric-clause children at this level, paired with their parts, in tree
    order. Non-numeric (annex, unnumbered) children are skipped -- they are the
    legitimately non-monotonic constructs the spec warns against flagging."""
    out = []
    for c in children:
        parts = _numeric_parts(c.clause_id)
        if parts is not None:
            out.append((c, parts))
    return out


def _clause_inventory(node: Node) -> set[str]:
    """Every numeric clause id present ANYWHERE in the document (normalized
    "3.24"), regardless of where it sits in the tree. The numbering gate exists
    to catch DROPPED clauses; a clause that's present but merely mis-nested
    (Docling buries a definition paragraph under the previous clause) was not
    dropped, so it must not be reported as a missing-clause gap."""
    inv: set[str] = set()

    def walk(n: Node):
        parts = _numeric_parts(n.clause_id)
        if parts is not None:
            inv.add(".".join(map(str, parts)))
        for c in n.children:
            walk(c)

    walk(node)
    return inv


def _gap_between(prev: list[int], cur: list[int], inventory: set[str]) -> str | None:
    """Report genuinely-absent siblings between two consecutive numeric clause
    ids at the same depth. Only compares siblings sharing a parent prefix and
    depth (5.2 -> 5.4 at depth 2 with prefix [5]); a depth/prefix change is a
    level transition, not a gap. A missing number that nonetheless exists in the
    clause inventory (present but mis-nested elsewhere) is filtered out -- only a
    number absent from the WHOLE document is a real drop."""
    if len(prev) != len(cur) or prev[:-1] != cur[:-1]:
        return None
    if cur[-1] <= prev[-1]:
        return (f"non-increasing sibling numbering: "
                f"{'.'.join(map(str, prev))} then {'.'.join(map(str, cur))}")
    # every sibling number strictly between prev and cur that is absent everywhere
    missing = []
    for n in range(prev[-1] + 1, cur[-1]):
        cid = ".".join(map(str, prev[:-1] + [n]))
        if cid not in inventory:
            missing.append(cid)
    if missing:
        return (f"dropped clause(s) {missing} between {'.'.join(map(str, prev))} "
                f"and {'.'.join(map(str, cur))} (absent from the whole document)")
    return None


def check(root: Node) -> GateReport:
    outcomes: list[Outcome] = []
    inventory = _clause_inventory(root)

    def visit(node: Node) -> Node:
        children = [visit(c) for c in node.children]
        node = node.model_copy(update={"children": children})
        siblings = _same_level_siblings(children)
        for (prev_node, prev), (cur_node, cur) in zip(siblings, siblings[1:]):
            gap = _gap_between(prev, cur, inventory)
            if gap:
                # Quarantine the subtree headed by the later sibling (the point
                # the sequence broke), not the whole document.
                idx = children.index(cur_node)
                q, out = quarantine(cur_node, "numbering", gap)
                children[idx] = q
                outcomes.append(out)
        return node.model_copy(update={"children": children})

    return GateReport(root=visit(root), outcomes=outcomes)
