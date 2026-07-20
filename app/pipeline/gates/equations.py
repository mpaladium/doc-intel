"""Gate 7 (spec §"equation integrity"): every Equation has a `latex` field and
no Equation carries only `rendered_text`.

Detects an equation left with no structural LaTeX (only `rendered_text`) -- one
you cannot compare, which downstream gets diffed as prose and classified
editorial. Never fall back to rendered_text: a wrong comparison is worse than an
acknowledged gap.

Note on the LaTeX source: the reference's "Docling flattens formulas to garbled
text" describes pre-enrichment Docling. Current Docling CodeFormula enrichment
emits valid structured LaTeX (`N_{\\text{d}} = 2\\ B_{\\text{e}} \\times
T_{\\text{a}}`), so a Docling equation with balanced LaTeX passes -- it is not
quarantined merely for being Docling's. MinerU is the registered corroborator
that adds a second candidate + a real symbol_table.

Sub-checks: latex parses as balanced LaTeX (unbalanced == truncation); every
symbol in symbol_table appears in latex (a defined-but-absent symbol means the
region was cropped -- a real cropping guard only once symbol_table comes from an
INDEPENDENT source like MinerU; canon_equation's LaTeX-derived inventory is
metadata and trivially satisfies it). `computes_limit` needs a param<->symbol
graph (MinerU-grade symbol tables) and is deferred.

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
