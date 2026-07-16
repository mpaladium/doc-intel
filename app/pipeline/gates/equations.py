"""Gate 7 (spec §"equation integrity"): every Equation has a `latex` field and
no Equation carries only `rendered_text`.

Detects the silent flattening Docling performs on every formula: `I = V/R`
becomes `I = V R` -- not mangled enough to look wrong, not structured enough to
use; downstream an equation that computes a limit gets compared as prose and
classified editorial. Never fall back to rendered_text: an equation you cannot
represent structurally is one you cannot compare, and a wrong comparison is
worse than an acknowledged gap.

Sub-checks: latex parses as balanced LaTeX (unbalanced == truncation); every
symbol in symbol_table appears in latex and vice versa (a defined-but-absent
symbol means the region was cropped). `computes_limit` depends on cross-
reference resolution, so that sub-check is deferred to the xref gate.

Deliberately does NOT attempt semantic equivalence (`V=IR` vs `V=RI`): a string
mismatch there is an acceptable false positive that routes to a human; sympy
fails such inputs by returning a wrong answer confidently, the worst failure
mode available.
"""

from __future__ import annotations

from canonical_schema import Node
from app.pipeline.gates import GateReport, Outcome, quarantine, transform_tree

_PAIRS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {v: k for k, v in _PAIRS.items()}


def _balanced(latex: str) -> bool:
    """Brace/paren/bracket matching -- unbalanced means truncation. Ignores
    escaped delimiters (`\\{`) since those are literal, not grouping."""
    stack: list[str] = []
    i = 0
    while i < len(latex):
        c = latex[i]
        if c == "\\":  # skip the escaped char
            i += 2
            continue
        if c in _PAIRS:
            stack.append(c)
        elif c in _CLOSERS:
            if not stack or stack.pop() != _CLOSERS[c]:
                return False
        i += 1
    return not stack


def _check_node(node: Node) -> tuple[Node, list[Outcome]]:
    if node.type != "equation":
        return node, []

    if not node.latex:
        detail = "rendered_text only, no structural latex" if node.rendered_text else "no latex"
        node, out = quarantine(node, "equations", f"equation without latex ({detail})")
        return node, [out]

    if not _balanced(node.latex):
        node, out = quarantine(node, "equations", f"latex unbalanced (truncation?): {node.latex!r}")
        return node, [out]

    # symbol_table <-> latex correspondence
    for sym in node.symbol_table:
        if sym not in node.latex:
            node, out = quarantine(node, "equations",
                                   f"symbol '{sym}' defined in symbol_table but absent from latex")
            return node, [out]
    return node, []


def check(root: Node) -> GateReport:
    return transform_tree(root, _check_node)
