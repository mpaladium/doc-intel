"""classify_type -- assign the closed normative-role CDM type on top of the
structural NodeType (canonical-model.md closed type set).

A "paragraph" carrying a `shall` becomes cdm_type "Requirement"; `should` ->
Recommendation; `may` -> Permission; a WARNING/CAUTION admonition -> Warning.
The modal lexicon is per-language and never inferred by translating to English
first (modality.py) -- the modal-verb gate depends on this being right, because
`shall`->`should` is the corruption the whole system exists to catch.

The type set is closed: content that doesn't match a role stays a Paragraph
(cdm_type None here; downstream treats an unassigned text node as informative
Paragraph), never a new ad-hoc type. Assignment is precedence-ordered:
Warning > Requirement > Recommendation > Permission, because an admonition
outranks the modal inside it and a `shall` outranks a `may` in the same clause.
"""

from __future__ import annotations

import re

from canonical_schema import CDMType, Node, preferred_text
from app.pipeline.modality import ADMONITIONS, _lang_key, find_modals

# Precedence: strongest normative force first. A clause with both `shall` and
# `may` is a Requirement; an admonition wrapper outranks either.
_ROLE_PRECEDENCE = ["Warning", "Requirement", "Recommendation", "Permission"]

# Section-level roles keyed on a normalized heading, per language. These are the
# informative/structural roles the modal path can't see (a Scope clause rarely
# contains a modal). Matched on the heading text of a section node.
_SECTION_HEADINGS: dict[str, dict[str, CDMType]] = {
    "en": {"scope": "Scope", "normative references": "NormativeReference",
           "terms and definitions": "Definition", "definitions": "Definition"},
    "de": {"anwendungsbereich": "Scope", "normative verweisungen": "NormativeReference",
           "begriffe": "Definition", "begriffe und definitionen": "Definition"},
    "fr": {"domaine d'application": "Scope", "références normatives": "NormativeReference",
           "termes et définitions": "Definition", "définitions": "Definition"},
}


def _admonition_role(text: str, lang: str | None) -> CDMType | None:
    lex = ADMONITIONS.get(_lang_key(lang), ADMONITIONS["en"])
    head = text.strip().lower()[:40]
    for word, role in lex.items():
        # admonitions lead the block ("WARNING — ...", "Vorsicht:")
        if re.match(rf"\b{re.escape(word)}\b", head):
            return role  # type: ignore[return-value]
    return None


def _modal_role(text: str, lang: str | None) -> CDMType | None:
    from app.pipeline.modality import modal_lexicon
    lex = modal_lexicon(lang)
    roles = {lex[m.lower()] for m in find_modals(text, lang) if m.lower() in lex}
    for role in _ROLE_PRECEDENCE:
        if role in roles:
            return role  # type: ignore[return-value]
    return None


def _section_role(node: Node) -> CDMType | None:
    if node.type not in ("section", "heading"):
        return None
    text = (node.text or "").strip().lower()
    # strip a leading clause number ("1 Scope" -> "scope")
    text = re.sub(r"^\s*\d+(?:\.\d+)*\s+", "", text)
    table = _SECTION_HEADINGS.get(_lang_key(node.lang), _SECTION_HEADINGS["en"])
    return table.get(text)


def classify_node(node: Node) -> CDMType | None:
    """The CDM type for one node, or None to leave it an informative Paragraph.
    Section headings resolve to structural roles; text bodies to modal roles;
    an admonition outranks the modal inside it."""
    # runs authority when it corroborates `text`, same rule as parameters.py
    text = preferred_text(node.raw_text, node.text)
    if not text:
        return None
    if (role := _section_role(node)) is not None:
        return role
    if (role := _admonition_role(text, node.lang)) is not None:
        return role
    return _modal_role(text, node.lang)


def annotate_node(node: Node) -> Node:
    """Depth-first, rebuild-children-first (same pattern as canon/topology):
    assign cdm_type where a role is detected, leave it None otherwise. Never
    overwrites an already-assigned cdm_type (a reviewer or upstream stage wins)."""
    children = [annotate_node(c) for c in node.children]
    node = node.model_copy(update={"children": children})
    if node.cdm_type is None:
        role = classify_node(node)
        if role is not None:
            node = node.model_copy(update={"cdm_type": role})
    return node
