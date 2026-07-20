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


# A node whose text is *exactly* a clause number (nothing else) -- the
# left-gutter number of a two-column clause layout, split from its title.
_LONE_CLAUSE_NUMBER = re.compile(
    r"^\s*(?:\d{1,3}(?:\.\d{1,3})*|(?:annex|anhang|annexe)\s+[A-Z]{1,3})\s*$",
    re.IGNORECASE,
)
# A list_item / paragraph carrying a clause number then a non-numeric title,
# e.g. "16.1 Flame-retardant test." -- Docling joined number+title but typed it
# a list_item, so assign_clause_ids (section/heading only) skips it.
_NUMBERED_TITLE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3})+)\s+\D")
_TITLE_MAX_WORDS = 12       # a clause title is short (list_item / merge target)
_PARA_TITLE_MAX_WORDS = 6   # a bare paragraph is stricter: a definition term, not prose

# A trailing parenthetical annotation on a definition entry -- "(en: effective
# frequency range)", "(siehe auch Bild 1)". Standards definition lists write
# terms as "N.N term (gloss)"; the gloss shouldn't count against the short-title
# bar, or a genuine definition term gets left unlabeled (DIN 3.28 "Anstieg des
# Spektrums (siehe auch Bild 1)" is 7 words with the gloss, 4 without).
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")


def _title_word_count(text: str) -> int:
    stripped = text
    while (nxt := _TRAILING_PAREN.sub("", stripped)) != stripped:
        stripped = nxt
    return len(stripped.split())


def _iter_reading_order(nodes: list[Node]):
    for n in nodes:
        yield n
        yield from _iter_reading_order(n.children)


def merge_split_clause_numbers(top_sections: list[Node]) -> list[Node]:
    """Reunite a two-column clause layout where Docling emitted the clause
    number and its title/term as *separate* nodes (common in ISO/DIN
    definition lists: "3.2" in the left gutter, "tatsächliche Bewegung"
    beside it). In reading order a lone-number node immediately followed by a
    short section/heading on the same page has its number prepended to that
    heading and is dropped, so `assign_clause_ids` then labels it normally and
    `nest_by_clause` nests it under its parent clause.

    Fail-safe: only merges when the number is a node's *entire* text, the next
    node is a short title-shaped section/heading with no clause number of its
    own, and both sit on the same page -- never guesses across unrelated
    content."""
    order = list(_iter_reading_order(top_sections))
    drop_ids: set[int] = set()
    new_text: dict[int, str] = {}

    for a, b in zip(order, order[1:]):
        if id(a) in drop_ids or not a.text or not b.text:
            continue
        # The title/term may be a section, heading, or (Docling's typing
        # varies for definition terms) a short paragraph / list_item. Tables,
        # figures, captions, equations are never a clause title.
        if b.type not in ("section", "heading", "paragraph", "list_item"):
            continue
        if not _LONE_CLAUSE_NUMBER.match(a.text.strip()):
            continue
        if a.provenance.page != b.provenance.page:
            continue
        if len(b.text.split()) > _TITLE_MAX_WORDS:
            continue
        if _extract_clause_id(b.text) is not None:
            continue  # title already carries a number -- unrelated adjacency
        drop_ids.add(id(a))
        new_text[id(b)] = f"{a.text.strip()} {b.text.strip()}"

    if not drop_ids:
        return top_sections

    def _rebuild(nodes: list[Node]) -> list[Node]:
        out: list[Node] = []
        for n in nodes:
            if id(n) in drop_ids:
                continue
            update: dict = {"children": _rebuild(n.children)}
            if id(n) in new_text:
                update["text"] = new_text[id(n)]
            out.append(n.model_copy(update=update))
        return out

    return _rebuild(top_sections)


def assign_clause_ids(node: Node) -> Node:
    """Depth-first: a node whose text carries a numbered or Annex clause label
    gets a normalized `clause_id`. `section`/`heading` nodes match a leading OR
    trailing number; `list_item` nodes (and title-shaped paragraphs) match only
    a LEADING number + non-numeric title ("16.1 Flame-retardant test."), so an
    ordinary prose paragraph that merely mentions a clause is never mislabeled.
    Returns a new tree (children rebuilt bottom-up)."""
    new_children = [assign_clause_ids(child) for child in node.children]
    update: dict = {"children": new_children}

    if node.clause_id is None and node.text:
        clause_id = None
        if node.type in ("section", "heading"):
            clause_id = _extract_clause_id(node.text)
        elif node.type in ("list_item", "paragraph"):
            # stricter: leading numbered title only, and title-shaped (short),
            # so "3.2 m/s applies in ..." (prose) is not treated as clause 3.2.
            # A list_item is already a title by Docling's typing; a bare
            # paragraph is held to a much shorter length (a definition term).
            max_words = _TITLE_MAX_WORDS if node.type == "list_item" else _PARA_TITLE_MAX_WORDS
            if _NUMBERED_TITLE.match(node.text) and _title_word_count(node.text) <= max_words:
                clause_id = _LEADING_NUMERIC.match(node.text.strip())
                clause_id = clause_id.group(1) if clause_id else None
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


def hoist_misnested_clauses(top_sections: list[Node]) -> list[Node]:
    """Un-bury clause nodes Docling nested under the WRONG clause: a node with
    numeric clause parts P sitting under an ancestor with numeric parts A where
    A is not a proper prefix of P (DIN's definition paragraph 3.24 inside
    section 3.23) is cut out -- subtree and all -- and re-emitted at top level
    in document order, so `nest_by_clause` places it where its NUMBER says it
    belongs. The compliance tree must reflect clause numbering, not Docling's
    incidental reading-order nesting; the numbering gate's inventory guard
    already stopped misreading these as drops, this fixes the TREE itself.

    Recursive: a hoisted node's own subtree is re-examined against ITS parts
    (3.26 buried inside 3.24 hoists too). Correctly-nested sub-clauses
    (5.3.5.1 under 5.3.5) and nodes under non-clause ancestors are untouched."""
    result: list[Node] = []

    def extract(node: Node, ancestor_parts: list[int] | None) -> tuple[Node, list[Node]]:
        """Returns (node with misnested descendants removed, hoisted nodes)."""
        own_parts = _numeric_parts(node.clause_id)
        # the clause context children are judged against: this node's parts if
        # it is clause-numbered, else the inherited ancestor context
        ctx = own_parts if own_parts is not None else ancestor_parts
        kept: list[Node] = []
        hoisted: list[Node] = []
        for child in node.children:
            c_parts = _numeric_parts(child.clause_id)
            if c_parts is not None and ctx is not None and not _is_proper_prefix(ctx, c_parts):
                # misnested: hoist the child (recursively cleaning ITS subtree
                # against its own parts first)
                cleaned, sub_hoisted = extract(child, c_parts)
                hoisted.append(cleaned)
                hoisted.extend(sub_hoisted)
            else:
                cleaned, sub_hoisted = extract(child, ctx)
                kept.append(cleaned)
                hoisted.extend(sub_hoisted)
        return node.model_copy(update={"children": kept}), hoisted

    # Running clause context across the FLAT list: a top-level container with
    # no clause number of its own (Docling emits stray "(en: gloss)" sections
    # mid-definition-list) will be buried by nest_by_clause under the currently
    # open clause -- so its clause-numbered descendants must be judged against
    # that same context or they get buried with it (the DIN 3.26 case). Items
    # BEFORE any clause opens (title page, TOC -- whose entries legitimately
    # carry clause numbers) have no running context and stay protected.
    running: list[int] | None = None
    for top in top_sections:
        own = _numeric_parts(top.clause_id)
        cleaned, hoisted = extract(top, own if own is not None else running)
        result.append(cleaned)
        result.extend(hoisted)  # document order: right after their old container
        if own is not None:
            running = own
    return result


def nest_by_clause(top_sections: list[Node]) -> list[Node]:
    """Rebuild a flat top-level section list into the clause hierarchy implied
    by each section's `clause_id`. Non-section nodes pass through at top level."""
    forest: list[_Entry] = []
    stack: list[_Entry] = []  # currently-open numeric-clause ancestors

    def _place(entry: _Entry) -> None:
        (stack[-1].subs if stack else forest).append(entry)

    for node in top_sections:
        # Any clause-labeled node participates in the numeric hierarchy, not just
        # `section` -- a Terms-&-Definitions entry is often a `paragraph`/
        # `list_item` ("3.24 Angleichung"), and if it doesn't get placed as a
        # numeric sibling it gets buried under the previous clause (3.23),
        # creating a phantom gap the numbering gate then flags. `assign_clause_ids`
        # already labels these node types, so honor the label here too.
        parts = _numeric_parts(node.clause_id)
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
