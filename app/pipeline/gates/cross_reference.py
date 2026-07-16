"""Gate 8 (spec §"cross-reference resolution"): every internal Reference
resolves to an object that exists in this document (or to an external
NormativeReference).

Detects a lost clause/annex: if 5.2 says "see 5.4" and 5.4 isn't in the model,
the parser dropped it -- the standard almost certainly didn't ship a dangling
reference. Failure quarantines the *referenced-FROM* object and is a document-
level extraction alarm: an unresolvable internal reference is strong evidence
of upstream loss, warranting re-examination of the page it points at.

Runs LAST because it is the only gate needing a complete object inventory;
everything else is local and can stream during parse.

Scope note: clause/annex/section refs are resolved against the edition's
clause-id inventory (xref.annotate_tree already sets target_clause_id on the
ones it could resolve). `table`/`figure`/`external` refs are recorded but not
gated here -- the model does not yet carry a table/figure label inventory to
resolve them against, and inventing one would risk false alarms; that resolution
is a documented follow-up on the deferred list.
"""

from __future__ import annotations

from canonical_schema import Node
from app.pipeline.gates import GateReport, Outcome, _iter, quarantine

_INTERNAL_RESOLVABLE = {"clause", "annex", "section"}


def _clause_inventory(root: Node) -> set[str]:
    return {n.clause_id for n in _iter(root) if n.clause_id}


def check(root: Node) -> GateReport:
    inventory = _clause_inventory(root)
    outcomes: list[Outcome] = []

    def visit(node: Node) -> Node:
        node = node.model_copy(update={"children": [visit(c) for c in node.children]})
        if not node.xrefs:
            return node
        # xref.annotate_tree may not have resolved it; accept either a set
        # target_clause_id or a hit in the clause inventory as resolution.
        dangling = [
            x for x in node.xrefs
            if x.kind in _INTERNAL_RESOLVABLE
            and x.target_clause_id is None
            and x.text not in inventory
        ]
        if dangling:
            refs = [x.text for x in dangling]
            q, out = quarantine(
                node, "cross_reference",
                f"unresolved internal reference(s) {refs} -- referenced object missing "
                f"(document-level extraction alarm)")
            outcomes.append(out)
            return q
        return node

    return GateReport(root=visit(root), outcomes=outcomes)
