"""Gate 5 (spec §"modal verb preservation"): the modal verbs in raw_text match
those in normalized_text, WITH CASE PRESERVED.

This gate exists because `shall`->`should` is the single most consequential
silent corruption available: it downgrades a requirement to a recommendation,
and downstream everything agrees nothing changed. Hard failure, no repair --
there is no uniquely-determined fix for "which modal did the source actually
use." The modal lexicon is per-language and never inferred by translating to
English first (French `il convient de` == `should`, not `must`).
"""

from __future__ import annotations

from canonical_schema import Node
from app.pipeline.gates import GateReport, Outcome, quarantine, transform_tree
from app.pipeline.modality import find_modals


def _check_node(node: Node) -> tuple[Node, list[Outcome]]:
    raw = node.raw_text
    norm = node.normalized_text
    # Only meaningful when both forms exist -- on the single-parser path
    # normalized_text is often unset (text == raw_text) and there is nothing to
    # compare, which is a pass, not a finding.
    if raw is None or norm is None:
        return node, []

    raw_modals = find_modals(raw, node.lang)
    norm_modals = find_modals(norm, node.lang)
    # Case-preserved exact comparison: SHALL != shall, shall != should.
    if raw_modals != norm_modals:
        node, out = quarantine(
            node, "modal_verbs",
            f"modal verbs differ raw_text{raw_modals} vs normalized_text{norm_modals}")
        return node, [out]
    return node, []


def check(root: Node) -> GateReport:
    return transform_tree(root, _check_node)
