"""topology.clauses — clause/annex numbering + clause-hierarchy reconstruction.

Two deterministic passes over the section tree (ARCHITECTURE.md §2.1):

1. `assign_clause_ids` — populate `Node.clause_id` from heading text. Clause
   numbers appear at the START ("4.2.3 Limits", English/ISO style) OR the END
   ("Grenzwertklassen 5.3.4", common in German TL/DIN standards); both are
   parsed. Date-like and standard-reference numbers (`2009-04`, `IEC
   61000-4-3`) are guarded against.

2. `nest_by_clause` — rebuild the flat top-level section list into the real
   clause hierarchy: `5.3.5.1` becomes a child of `5.3.5` → `5.3` → `5`, using
   the clause NUMBER as the authoritative parent key. Docling's typographic
   heading `level` is uniform across clause depths in these documents, so
   without this the compliance tree stays flat. Sections whose parent clause
   is absent attach to the nearest present ancestor; non-clause sections
   attach to the currently-open clause; annexes reset to top level.

Cross-reference edges ("see 4.2.3") are handled separately in `xref.py`
(within-edition references on `Node.xrefs`), not here.
"""

from __future__ import annotations

import re

from canonical_schema import Node

# Leading clause number: "5", "5.3", "4.2.3.1" -- must be followed by space+
# non-digit or end-of-string, so a date like "2009-04-01 ..." (followed by "-")
# does not match.
_LEADING_NUMERIC = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)(?=\s+\D|\s*$)")
# Trailing clause number: requires at least one dot (a multi-level number like
# "5.3" / "5.3.5.1"), so a trailing figure/quantity ("Prüffeldstärke 2") or a
# year does NOT match. Digit groups capped to 3 to avoid standard numbers.
_TRAILING_NUMERIC = re.compile(r"(?:^|\s)(\d{1,3}(?:\.\d{1,3})+)\s*$")
_ANNEX_CLAUSE = re.compile(r"^(?:Annex|Anhang|Annexe)\s+([A-Z]{1,3}(?:\.\d{1,3})*)\b",
                           re.IGNORECASE)


def _extract_clause_id(heading_text: str) -> str | None:
    text = heading_text.strip()
    if m := _ANNEX_CLAUSE.match(text):
        return f"Annex {m.group(1).upper()}"
    if m := _LEADING_NUMERIC.match(text):
        return m.group(1)
    if m := _TRAILING_NUMERIC.search(text):
        return m.group(1)
    return None


def assign_clause_ids(node: Node) -> Node:
    """Depth-first: any section/heading node whose text carries a numbered or
    Annex clause label (leading or trailing) gets a normalized `clause_id`.
    Returns a new tree (children rebuilt bottom-up)."""
    new_children = [assign_clause_ids(child) for child in node.children]
    update: dict = {"children": new_children}

    if node.type in ("section", "heading") and node.clause_id is None and node.text:
        clause_id = _extract_clause_id(node.text)
        if clause_id is not None:
            update["clause_id"] = clause_id

    return node.model_copy(update=update)


# --------------------------------------------------------------------------- #
# Clause-hierarchy reconstruction
# --------------------------------------------------------------------------- #
def _numeric_parts(clause_id: str | None) -> list[int] | None:
    if clause_id and re.fullmatch(r"\d{1,3}(?:\.\d{1,3})*", clause_id):
        return [int(x) for x in clause_id.split(".")]
    return None


def _is_proper_prefix(a: list[int], b: list[int]) -> bool:
    return len(a) < len(b) and b[: len(a)] == a


class _Entry:
    __slots__ = ("node", "parts", "subs")

    def __init__(self, node: Node, parts: list[int] | None):
        self.node = node
        self.parts = parts
        self.subs: list["_Entry"] = []

    def assemble(self) -> Node:
        if not self.subs:
            return self.node
        sub_nodes = [e.assemble() for e in self.subs]
        # sub-clause sections come after the parent's own content, in order.
        return self.node.model_copy(update={"children": list(self.node.children) + sub_nodes})


def nest_by_clause(top_sections: list[Node]) -> list[Node]:
    """Rebuild a flat top-level section list into the clause hierarchy implied
    by each section's `clause_id`. Non-section nodes pass through at top level."""
    forest: list[_Entry] = []
    stack: list[_Entry] = []  # currently-open numeric-clause ancestors

    def _place(entry: _Entry) -> None:
        (stack[-1].subs if stack else forest).append(entry)

    for node in top_sections:
        parts = _numeric_parts(node.clause_id) if node.type == "section" else None
        is_annex = node.type == "section" and node.clause_id and parts is None \
            and node.clause_id.lower().startswith("annex")

        if parts is not None:
            # pop until the stack top is a proper prefix of this clause (an
            # annex entry has parts=None and is never a prefix -> popped).
            while stack and (stack[-1].parts is None
                             or not _is_proper_prefix(stack[-1].parts, parts)):
                stack.pop()
            entry = _Entry(node, parts)
            _place(entry)
            stack.append(entry)
        elif is_annex or (node.type == "section" and not node.compliance_relevant):
            # annexes and front/back matter (toc, foreword, index, ...) start a
            # fresh top-level block -- they must never be buried under the last
            # open clause. A following no-clause content section nests under an
            # annex (normative), but not under excluded matter.
            stack.clear()
            entry = _Entry(node, None)
            forest.append(entry)
            if is_annex:
                stack.append(entry)
        else:
            # compliance-relevant, no clause_id (sub-heading, guidance note,
            # stray content): attach to the currently-open clause if any, else
            # top level.
            _place(_Entry(node, None))

    return [e.assemble() for e in forest]
