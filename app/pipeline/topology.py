"""topology.clauses — clause/annex numbering assignment.

Deterministic regex pass over the heading tree, populating `Node.clause_id`
(ARCHITECTURE.md §2.1). This iteration implements a generic numbered-heading
parser ("4", "4.2", "4.2.3.1", "Annex ZA") rather than a per-standards-family
rulepack (IEC/CISPR) -- that needs real standards documents as gold-set input
to tune against, which aren't available yet (see plan's deferred-work note).

Cross-reference edges ("see 4.2.3") are NOT resolved here: `canonical_schema.Node`
has no field to hold them, and REFERENCES is defined as a comparison-engine graph
edge type derived from Chunk text (ARCHITECTURE.md §3.2) -- resolving it at the
Node level would be inventing schema outside the shared contract. Deferred.
"""

from __future__ import annotations

import re

from canonical_schema import Node

_NUMERIC_CLAUSE = re.compile(r"^(\d+(?:\.\d+)*)\b")
_ANNEX_CLAUSE = re.compile(r"^(Annex\s+[A-Z]+)\b", re.IGNORECASE)


def _extract_clause_id(heading_text: str) -> str | None:
    text = heading_text.strip()
    if m := _NUMERIC_CLAUSE.match(text):
        return m.group(1)
    if m := _ANNEX_CLAUSE.match(text):
        return m.group(1)
    return None


def assign_clause_ids(node: Node) -> Node:
    """Depth-first: any section/heading node whose text starts with a numbered or
    Annex prefix gets a normalized `clause_id`. Returns a new tree (Node is
    immutable-by-convention here; children rebuilt bottom-up)."""
    new_children = [assign_clause_ids(child) for child in node.children]
    update: dict = {"children": new_children}

    if node.type in ("section", "heading") and node.clause_id is None and node.text:
        clause_id = _extract_clause_id(node.text)
        if clause_id is not None:
            update["clause_id"] = clause_id

    return node.model_copy(update=update)
