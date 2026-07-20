"""identity -- content-addressed, deterministic node ids (canonical-model.md
§Identity). Replaces the random `uuid.uuid4().hex[:12]` ids the extractor
assigns with `standard_id#section_path` for clause-numbered objects and
`doc_id#sha256(raw_text)[:12]` for unnumbered content, via `make_object_id`.

Why it matters (Goal-2-facing, produced in Goal-1): section-path ids are stable
across editions when numbering is stable, which is what makes ID-first
alignment cheap. Random uuids are stable within neither an edition nor across
runs, so alignment would have nothing to key on.

Runs as the FINAL assembly pass so every clause_id is assigned; it rewrites the
two fields that reference a node id -- `Parameter.source_object_id` and
`continues_from`/`continues_to` -- through the same old->new map, so no
reference is left dangling. Deterministic: the same PDF yields the same ids
every run (collisions are broken by tree order, which is itself deterministic).
"""

from __future__ import annotations

import re

from canonical_schema import Node, make_object_id

# Common standard designations on a title page: "DIN EN 60068-2-64",
# "DNVGL-CG-0339", "IEC 61000-4-3", "TL 81000", "CISPR 25". Anchored to the
# START of the text so a prose *reference* to another standard ("...tested per
# IEC 60068...") does not hijack the document's own designation.
_STANDARD_ID = re.compile(
    r"^\s*((?:DIN|EN|IEC|ISO|CISPR|DNVGL|TL|FCC|VW|VG|MIL|SAE|UL|ETSI)"
    r"[\s/-]?(?:EN\s)?[0-9A-Z][\w.\-/]*\d)")
_NUMERIC_CLAUSE = re.compile(r"\d{1,3}(?:\.\d{1,3})*")
# Node types where a designation is likely the document's OWN (a title/heading
# or a running header), not a body-prose cross-reference.
_TITLE_TYPES = {"section", "heading", "paragraph", "caption"}


def _iter(node: Node):
    yield node
    for c in node.children:
        yield from _iter(c)


def derive_standard_id(root: Node) -> str | None:
    """The document's own standard designation, best-effort: a title-page-shaped
    match (designation at the START of a page-1 heading/title node), so a body
    reference to a different standard doesn't hijack it. None on a title-page-
    less fragment -- ids then fall back to the doc hash (still stable per run,
    just not cross-edition). Full documents carry the designation on page 1."""
    for n in _iter(root):
        if n.type not in _TITLE_TYPES or n.provenance.page != 1:
            continue
        for t in (n.text, n.raw_text):
            if t and (m := _STANDARD_ID.match(t)):
                return re.sub(r"\s+", " ", m.group(1)).strip()
    return None


def _section_path(node: Node) -> list[str] | None:
    if not node.clause_id:
        return None
    if _NUMERIC_CLAUSE.fullmatch(node.clause_id):
        return node.clause_id.split(".")
    return [node.clause_id]  # annex / lettered clause -> single path segment


def restamp_ids(root: Node, doc_id: str, standard_id: str | None = None) -> Node:
    """Return a copy of the tree with content-addressed ids and every
    id-reference (`source_object_id`, `continues_from`/`to`) remapped."""
    old_to_new: dict[str, str] = {}
    seen: dict[str, int] = {}

    def assign(node: Node) -> None:
        base = make_object_id(doc_id, _section_path(node), node.raw_text or node.text, standard_id)
        n = seen.get(base, 0)
        seen[base] = n + 1
        # deterministic disambiguation for genuine id collisions (duplicate
        # clause numbers, or several unnumbered nodes with identical text)
        old_to_new[node.id] = base if n == 0 else f"{base}~{n}"
        for c in node.children:
            assign(c)

    assign(root)

    def rewrite(node: Node) -> Node:
        update: dict = {"id": old_to_new[node.id],
                        "children": [rewrite(c) for c in node.children]}
        if node.continues_from in old_to_new:
            update["continues_from"] = old_to_new[node.continues_from]
        if node.continues_to in old_to_new:
            update["continues_to"] = old_to_new[node.continues_to]
        if node.parameters:
            update["parameters"] = [
                p.model_copy(update={"source_object_id": old_to_new[p.source_object_id]})
                if p.source_object_id in old_to_new else p
                for p in node.parameters]
        return node.model_copy(update=update)

    return rewrite(root)
