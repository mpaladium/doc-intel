"""xref -- detect and resolve cross-references in node text (the "xref edges"
half of topology.clauses in SKILLS.md).

Compliance clauses constantly reference each other and their tables/figures
("see 4.2.3", "siehe 5.3.5", "Table 22", "Anhang ZA", "Sec.3 [14.6]"). These
are recorded on `Node.xrefs`; clause/annex references are *resolved* to the
clause_id when that clause actually exists in this edition, giving the
downstream graph real within-edition reference targets instead of prose.

Deterministic, offline, multilingual by keyword (English + German lead words),
conservative: a reference is only recorded when it has an explicit lead word or
bracketed section marker, so ordinary numbers in prose are not mistaken for
references.
"""

from __future__ import annotations

import re

from canonical_schema import Node, XRef

_CLAUSE_NUM = r"\d{1,3}(?:\.\d{1,3})*"

# Clause references introduced by an explicit lead word (EN + DE). Captures the
# clause number so it can be resolved against the edition's clause set.
_CLAUSE_REF = re.compile(
    r"(?:see|refer\s+to|according\s+to|in\s+accordance\s+with|per|"
    r"siehe|gem[äa]ß|nach|entsprechend|"
    r"clause|subclause|section|abschnitt|kapitel|"
    r"sec\.?)\s*\[?\s*(" + _CLAUSE_NUM + r")\s*\]?",
    re.IGNORECASE,
)
_TABLE_REF = re.compile(r"\b(?:table|tabelle)\s+(" + _CLAUSE_NUM + r")", re.IGNORECASE)
_FIGURE_REF = re.compile(r"\b(?:figure|fig\.?|bild|abbildung)\s+(" + _CLAUSE_NUM + r")", re.IGNORECASE)
_ANNEX_REF = re.compile(r"\b(?:annex|anhang|annexe)\s+([A-Z]{1,3}(?:\.\d{1,3})*)", re.IGNORECASE)


def collect_clause_ids(node: Node) -> set[str]:
    ids: set[str] = set()
    if node.clause_id:
        ids.add(node.clause_id)
    for c in node.children:
        ids |= collect_clause_ids(c)
    return ids


def _find_xrefs(text: str, clause_ids: set[str]) -> list[XRef]:
    refs: list[XRef] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, surface: str, target: str | None) -> None:
        key = (kind, surface)
        if key not in seen:
            seen.add(key)
            refs.append(XRef(kind=kind, text=surface, target_clause_id=target))

    for m in _CLAUSE_REF.finditer(text):
        num = m.group(1)
        _add("clause", num, num if num in clause_ids else None)
    for m in _ANNEX_REF.finditer(text):
        target = f"Annex {m.group(1).upper()}"
        _add("annex", target, target if target in clause_ids else None)
    for m in _TABLE_REF.finditer(text):
        _add("table", f"Table {m.group(1)}", None)
    for m in _FIGURE_REF.finditer(text):
        _add("figure", f"Figure {m.group(1)}", None)
    return refs


def _annotate(node: Node, clause_ids: set[str]) -> Node:
    children = [_annotate(c, clause_ids) for c in node.children]
    node = node.model_copy(update={"children": children})
    if node.text:
        refs = _find_xrefs(node.text, clause_ids)
        if refs:
            node = node.model_copy(update={"xrefs": refs})
    return node


def annotate_tree(root: Node) -> Node:
    """Two passes: collect every clause_id in the edition, then record + resolve
    references on each node's text. Run after clause_ids are assigned."""
    clause_ids = collect_clause_ids(root)
    return _annotate(root, clause_ids)
