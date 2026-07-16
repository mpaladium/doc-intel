"""Verification gates (verification-rules.md) -- deterministic checks between
consensus and admission. Every gate is a claim "if extraction were correct,
this property would hold"; a violation means extraction is wrong, and the gate
says only *that*, not *how*. A gate may **repair** only when the correct output
is uniquely determined by data already present; otherwise it **quarantines**.
Every repair writes an auditable `repairs` entry -- repairs are auditable or
they are corruption.

Gates run in the spec's exact order (later gates assume earlier ones passed):

    1. header/footer suppression   (cleans the text field)
    2. run integrity               (establishes raw_text can be trusted at all)
    3. numbering monotonicity      (establishes the tree)
    4. table rectangularity
    5. continuation stitching      (tables must be well-formed before stitching)
    6. modal verb preservation
    7. unit and tolerance integrity(reads runs -- depends on gate 2)
    8. equation integrity
    9. cross-reference resolution   (needs the full object inventory -- runs last)

`run_all` chains them, threading the (possibly repaired) tree through and
accumulating outcomes. Quarantine is not failure: a document with 40
quarantined objects out of 8,000 is a successful ingestion with a 40-item
review queue; a scanned multilingual standard with zero quarantines is a
pipeline that isn't checking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from canonical_schema import Node


@dataclass(frozen=True)
class Outcome:
    """One gate's verdict on one object. Only repairs and quarantines are
    recorded -- a passing object produces no Outcome (silence is admission)."""
    gate: str
    object_id: str
    verdict: str  # "repair" | "quarantine"
    reason: str


@dataclass
class GateReport:
    root: Node
    outcomes: list[Outcome] = field(default_factory=list)

    @property
    def quarantined(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.verdict == "quarantine"]

    @property
    def repaired(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.verdict == "repair"]

    @property
    def ok(self) -> bool:
        """True iff nothing was quarantined. Repairs are fine -- they are
        recorded and uniquely determined. verify_extraction.py exits non-zero
        when this is False."""
        return not self.quarantined


def quarantine(node: Node, gate: str, reason: str) -> tuple[Node, Outcome]:
    """Mark a node quarantined without discarding anything: set consensus,
    append the reason, flag for review. Idempotent-ish -- reasons accumulate so
    a node failing two gates records both."""
    existing = node.quarantine_reason
    combined = f"{existing}; {gate}: {reason}" if existing else f"{gate}: {reason}"
    updated = node.model_copy(update={
        "consensus": "quarantined",
        "quarantine_reason": combined,
        "review_required": True,
        "review_reasons": node.review_reasons + [f"gate_{gate}"],
    })
    return updated, Outcome(gate, node.id, "quarantine", reason)


def repair(node: Node, gate: str, change: dict, reason: str, **updates) -> tuple[Node, Outcome]:
    """Apply a uniquely-determined fix and record it. `change` is the audit
    entry appended to `repairs`; `updates` are the actual field changes."""
    entry = {"gate": gate, "reason": reason, **change}
    updated = node.model_copy(update={"repairs": node.repairs + [entry], **updates})
    return updated, Outcome(gate, node.id, "repair", reason)


def _iter(node: Node):
    yield node
    for c in node.children:
        yield from _iter(c)


def transform_tree(root: Node, fn) -> GateReport:
    """Rebuild the tree bottom-up, applying a per-node gate `fn(node) ->
    (new_node, list[Outcome])` to each node after its children are rebuilt
    (same children-first pattern as topology/continuity). The common shape for a
    purely local gate; whole-document gates (continuation, cross-reference)
    walk the tree themselves."""
    outcomes: list[Outcome] = []

    def visit(node: Node) -> Node:
        node = node.model_copy(update={"children": [visit(c) for c in node.children]})
        new_node, outs = fn(node)
        outcomes.extend(outs)
        return new_node

    return GateReport(root=visit(root), outcomes=outcomes)


# Gate registry, in spec order. Each gate is `check(root) -> GateReport`.
def _gate_list():
    return [
        header_footer.check,
        run_integrity.check,
        numbering.check,
        table_rectangularity.check,
        continuation.check,
        modal_verbs.check,
        units.check,
        equations.check,
        cross_reference.check,
    ]


def run_all(root: Node) -> GateReport:
    """Run every gate in order, threading the repaired tree through and
    accumulating outcomes into one report."""
    outcomes: list[Outcome] = []
    for gate in _gate_list():
        report = gate(root)
        root = report.root
        outcomes.extend(report.outcomes)
    return GateReport(root=root, outcomes=outcomes)


# Imported last (they depend on the helpers above) so `gates.<name>.check` works
# and `_gate_list` resolves. Order here is the spec's gate order.
from app.pipeline.gates import (  # noqa: E402
    header_footer, run_integrity, numbering, table_rectangularity, continuation,
    modal_verbs, units, equations, cross_reference,
)
