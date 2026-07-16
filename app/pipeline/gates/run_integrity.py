"""Gate 2: run integrity (verification-rules.md).

Property: `raw_text` is reconstructible from `runs`, and every text-bearing
object has `runs`. This is the loss no other gate can see -- `10⁻³` flattens to
`10-3` *before* raw_text exists, `10-3` parses as 7, and nothing downstream
errors. Reconstruct raw_text from runs (emitting the Unicode super/subscript
codepoint wherever vertical_align != normal); a mismatch means one of the two
is lying -> quarantine. Objects with runs==[] that contain digits are
quarantined regardless -- OCR'd scientific notation is exactly the
unverifiable case.

Sits at position 2 because every later text-based gate checks raw_text; if
raw_text isn't reconstructible from runs, those gates validate a corrupted
witness and report all-clear.
"""

from __future__ import annotations

import re

from canonical_schema import Node, reconstruct_raw_text
from app.pipeline.consensus import normalize_for_compare
from app.pipeline.gates import GateReport, Outcome, quarantine, transform_tree

# Body prose objects where a flattened superscript / dropped ± hides. Deliberately
# NOT "section": a section node's text is a structural heading and its digits are
# a clause number (validated by the numbering gate), never a limit value -- so
# "2 Normative references" must not quarantine for lacking runs.
_TEXT_TYPES = {"paragraph", "heading", "list_item", "caption", "note"}
_HAS_DIGIT = re.compile(r"\d")


def _is_text_bearing(node: Node) -> bool:
    return node.type in _TEXT_TYPES and bool((node.raw_text or node.text or "").strip())


def _check_node(node: Node) -> tuple[Node, list[Outcome]]:
    if not _is_text_bearing(node):
        return node, []
    raw = node.raw_text if node.raw_text is not None else node.text or ""

    if not node.runs:
        # No runs at all: only a problem if the text carries digits (a limit,
        # a clause number, scientific notation) -- prose without digits can't
        # hide the super/subscript loss this gate exists to catch.
        if _HAS_DIGIT.search(raw):
            node, out = quarantine(node, "run_integrity",
                                   "text bears digits but has no runs to verify against")
            return node, [out]
        return node, []

    reconstructed = reconstruct_raw_text(node.runs)
    if normalize_for_compare(reconstructed) != normalize_for_compare(raw):
        node, out = quarantine(
            node, "run_integrity",
            f"raw_text not reconstructible from runs: runs->{reconstructed!r} vs raw_text->{raw!r}")
        return node, [out]
    return node, []


def check(root: Node) -> GateReport:
    return transform_tree(root, _check_node)
