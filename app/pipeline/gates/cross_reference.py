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

Resolution sources:
  * clause/annex/section refs -> the edition's clause-id inventory
    (xref.annotate_tree already sets target_clause_id on the ones it resolved).
  * table/figure refs -> a (kind, number) inventory built from CAPTION labels
    ("Bild 1", "Table 11", "Tabelle 46", "Figure 3"). Captions carry the label
    and the xref surfaces normalize to the same (kind, number), so a "see Table
    8" that resolves to no caption is the spec's flagship "the parser dropped
    Table 8" case -> quarantine + alarm.
"""

from __future__ import annotations

import re

from canonical_schema import Node
from app.pipeline.gates import GateReport, Outcome, _iter, quarantine

_INTERNAL_RESOLVABLE = {"clause", "annex", "section"}

# A caption's leading label -> (kind, number). Multilingual: German figures are
# "Bild"/"Abbildung", tables "Tabelle"; English "Figure"/"Fig.", "Table".
_CAPTION_LABEL = re.compile(
    r"^\s*(?P<word>Bild|Abbildung|Figure|Fig\.?|Tabelle|Table)\s*"
    r"(?P<num>[A-Z]?\.?\d+(?:\.\d+)*)",
    re.IGNORECASE)
_FIGURE_WORDS = {"bild", "abbildung", "figure", "fig", "fig."}
# extract the number from an xref surface ("Table 11" -> "11", "Figure 1" -> "1")
_REF_NUM = re.compile(r"(\d+(?:\.\d+)*)")


def _clause_inventory(root: Node) -> set[str]:
    return {n.clause_id for n in _iter(root) if n.clause_id}


def _caption_inventory(root: Node) -> set[tuple[str, str]]:
    """(kind, number) for every table/figure caption in the document."""
    inv: set[tuple[str, str]] = set()
    for n in _iter(root):
        if n.type != "caption" or not n.text:
            continue
        m = _CAPTION_LABEL.match(n.text.replace("\xa0", " "))
        if m:
            kind = "figure" if m.group("word").lower().rstrip(".") in _FIGURE_WORDS else "table"
            inv.add((kind, m.group("num")))
    return inv


def _max_numeric(captions: set[tuple[str, str]], kind: str) -> int | None:
    """Highest purely-numeric caption number for a kind, for the in-range guard."""
    nums = [int(num) for k, num in captions if k == kind and num.isdigit()]
    return max(nums) if nums else None


def check(root: Node) -> GateReport:
    inventory = _clause_inventory(root)
    captions = _caption_inventory(root)
    max_num = {"table": _max_numeric(captions, "table"),
               "figure": _max_numeric(captions, "figure")}
    outcomes: list[Outcome] = []

    def _resolved(x) -> bool:
        if x.kind in _INTERNAL_RESOLVABLE:
            return x.target_clause_id is not None or x.text in inventory
        if x.kind in ("table", "figure"):
            m = _REF_NUM.search(x.text)
            if m is None:
                return True  # no parseable number -> can't judge, don't alarm
            num = m.group(1)
            if (x.kind, num) in captions:
                return True
            # In-range guard: a ref to a number beyond the highest captured
            # caption is a forward reference / out of this document slice, not a
            # dropped object. Only an IN-RANGE missing number is a real drop.
            hi = max_num[x.kind]
            if hi is not None and num.isdigit() and int(num) > hi:
                return True
            return False
        return True  # external refs are not gated here

    def visit(node: Node) -> Node:
        node = node.model_copy(update={"children": [visit(c) for c in node.children]})
        if not node.xrefs:
            return node
        dangling = [x for x in node.xrefs if not _resolved(x)]
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
