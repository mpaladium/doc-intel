"""Gate 6 (spec §"unit and tolerance integrity"): every Parameter has a unit
and a comparator, and the symbols `±`, `≤`, `≥`, `°`, `µ`, `Ω` survive from
`runs` into the parameter.

The source is `runs`, NOT raw_text: checking raw_text only detects loss between
the string and the parameter, but the loss that matters happens between the
page and the string. Never default a missing comparator to `eq` -- quarantine
instead. These are the highest-severity extraction failures in the domain (a
dropped `±` turns `10 ± 0.5` into `100.5`; a dropped `≤` turns an upper bound
into an exact value).
"""

from __future__ import annotations

from canonical_schema import Node, reconstruct_raw_text
from app.pipeline.gates import GateReport, Outcome, quarantine, transform_tree

# Symbols that carry compliance meaning and must not vanish between the runs and
# the parameter (verification-rules.md specific traps).
_CRITICAL_SYMBOLS = ("±", "≤", "≥", "°", "µ", "Ω")


def _check_node(node: Node) -> tuple[Node, list[Outcome]]:
    if not node.parameters:
        return node, []

    reasons: list[str] = []
    for p in node.parameters:
        if not p.unit:
            reasons.append(f"parameter '{p.name}' has no unit")
        if p.comparator is None:
            # Never default to eq -- a missing comparator is unverifiable intent.
            reasons.append(f"parameter '{p.name}' has no comparator")

    # Symbol survival: any critical symbol present in the object's runs must
    # still be accounted for in the parameters, either as a literal char in the
    # unit/condition OR as structured meaning (`≤`->comparator lte, `≥`->gte,
    # `±`->tolerance). A symbol present in runs but absent from both was dropped.
    if node.runs:
        run_text = reconstruct_raw_text(node.runs)
        param_blob = " ".join(
            f"{p.value} {p.unit or ''} {p.raw_unit or ''} {p.condition or ''}"
            for p in node.parameters)
        comparators = {p.comparator for p in node.parameters}
        has_tolerance = any(p.tolerance is not None for p in node.parameters)
        # a symbol "survives" if it is literal in the blob OR structurally encoded
        structural = {
            "≤": "lte" in comparators,
            "≥": "gte" in comparators,
            "±": has_tolerance,
        }
        for sym in _CRITICAL_SYMBOLS:
            if sym in run_text and sym not in param_blob and not structural.get(sym, False):
                reasons.append(f"symbol '{sym}' in runs dropped from parameters")

    if reasons:
        node, out = quarantine(node, "units", "; ".join(reasons))
        return node, [out]
    return node, []


def check(root: Node) -> GateReport:
    return transform_tree(root, _check_node)
