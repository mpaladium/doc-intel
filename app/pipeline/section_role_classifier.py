"""
section_role_classifier.py
===========================
Classifies each top-level section of a CanonicalEdition as normative or non-normative
front/back matter (TOC, list of figures/tables, foreword, preface/introduction, index),
so comparison-engine can skip non-normative content when chunking for compliance
comparison — WITHOUT ever deleting it from the CanonicalEdition.

Design principle (read this before changing thresholds): the two error types are not
symmetric. A missed TOC just adds noise to the comparison — low cost. A wrongly-excluded
normative clause silently drops a real compliance change from the diff — the worst
possible failure mode for this system. Every default here is chosen to fail toward
"include and flag for review", never toward "silently exclude".

Layered signal design (why "irrespective of language" is achievable):
  1. POSITION  (language-independent by construction) — front zone = before the first
     bare-integer top-level clause_id (the start of the numbered normative body, e.g.
     "1 Scope"); back zone = trailing sections that are shape-flagged as index-like.
     A section inside the numbered normative body is NEVER auto-excluded, regardless
     of what its heading text says, in any language.
  2. SHAPE     (language-independent) — TOC/index/caption-lists have a distinctive line
     shape (short line + trailing page number; alphabetical ordering; caption-number
     cross-references matching real captions elsewhere in the document) that doesn't
     depend on what language the surrounding text is in.
  3. DICTIONARY (language-limited, therefore SECONDARY) — a multilingual heading-text
     rulepack (rulepacks/section_roles.yaml) that boosts confidence when it matches and
     is silent (not penalizing) when it doesn't, because it can never have full coverage.

Only sections in the front/back zone are candidates at all. Within a candidate, a role
is assigned only when the combined signal clears that role's threshold; the "preface"
role (which can carry substantive rationale) has a stricter threshold than the others
and always keeps review_required=True even when excluded.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# canonical_schema defines Node / SectionRole; imported, not duplicated.
from canonical_schema import Node, SectionRole


# --------------------------------------------------------------------------- #
# Rulepack loading
# --------------------------------------------------------------------------- #
def _normalize(text: str) -> str:
    """Casefold + NFC + strip diacritics, for tolerant heading-text matching."""
    text = unicodedata.normalize("NFKD", text.strip().casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


@dataclass(frozen=True)
class RolePack:
    version: str
    # role -> set of normalized heading strings, pooled across all languages.
    # (We don't need the language tag at match time — any language matching is
    # equally valid confirmation. The language breakdown in the YAML is for humans
    # maintaining the file, not for the matcher.)
    headings_by_role: dict[str, frozenset[str]]

    @classmethod
    def load(cls, path: str | Path) -> "RolePack":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        headings_by_role = {
            role: frozenset(_normalize(s) for variants in langs.values() for s in variants)
            for role, langs in data["roles"].items()
        }
        return cls(version=data["version"], headings_by_role=headings_by_role)

    def matches(self, heading_text: str, role: str) -> bool:
        if not heading_text:
            return False
        norm = _normalize(heading_text)
        return norm in self.headings_by_role.get(role, frozenset())


# --------------------------------------------------------------------------- #
# Structural shape detectors — the primary, language-independent signal.
# Each returns a confidence in [0, 1]; 0 means "shape doesn't match at all".
# --------------------------------------------------------------------------- #
_TRAILING_NUMBER = re.compile(r"[.\s]{2,}\d{1,4}\s*$")   # dot-leader or gap + page number
_LEADING_NUMBER = re.compile(r"^\s*\d+([.\-]\d+)?\s")     # "3.2 ..." or "3 ..." caption prefix

TOP_LEVEL_CLAUSE = re.compile(r"^\d+$")                   # bare integer clause_id: "1", "2"...

# Docling's layout clustering sometimes merges several visually-adjacent
# TOC/index lines into a single TextItem, space-joined with no line-break
# marker (observed empirically -- see ingestion-engine's e2e fixture). Each
# original line still ends in a dot-leader/gap + page number ("...  4") or a
# ", <page>" index suffix, so that trailing-number boundary is used to split
# a merged block back into pseudo-lines before shape detection runs. A true
# single-entry block just yields one "line", so this is a no-op in the
# common case where Docling kept lines separate.
_ENTRY_END = re.compile(r".*?(?:[.\s]{2,}\d{1,4}|,\s*\d{1,4})(?=\s+\S|\s*$)")


def _split_pseudo_lines(text: str) -> list[str]:
    entries = [m.group(0).strip() for m in _ENTRY_END.finditer(text)]
    return entries if entries else [text]


def _leaf_lines(node: Node) -> list[str]:
    """Flatten a section's paragraph/list_item descendants into text lines,
    splitting any Docling-merged multi-entry block back into pseudo-lines."""
    lines: list[str] = []
    if node.type in ("paragraph", "list_item") and node.text:
        lines.extend(_split_pseudo_lines(node.text))
    for child in node.children:
        lines.extend(_leaf_lines(child))
    return lines


def looks_like_toc(node: Node) -> float:
    lines = _leaf_lines(node)
    if len(lines) < 8:
        return 0.0
    hits = sum(1 for ln in lines if _TRAILING_NUMBER.search(ln))
    return hits / len(lines)


def looks_like_caption_list(node: Node, known_caption_numbers: set[str]) -> float:
    """List of Figures / List of Tables: lines whose leading number matches a real
    caption number seen elsewhere in the document (extracted from actual figure/table
    nodes) — a strong language-independent cross-check."""
    lines = _leaf_lines(node)
    if len(lines) < 3 or not known_caption_numbers:
        return 0.0
    numbered = [m.group(0).strip() for ln in lines if (m := _LEADING_NUMBER.match(ln))]
    if not numbered:
        return 0.0
    overlap = sum(1 for n in numbered if n.rstrip(".") in known_caption_numbers)
    return overlap / len(numbered)


def looks_like_index(node: Node) -> float:
    """Alphabetical term->page index: many short lines, already in sorted order."""
    lines = [ln for ln in _leaf_lines(node) if ln.strip()]
    if len(lines) < 10:
        return 0.0
    short = sum(1 for ln in lines if len(ln) < 80 and _TRAILING_NUMBER.search(ln))
    shape_ratio = short / len(lines)
    # crude locale-agnostic sortedness check on the leading token of each line
    heads = [_normalize(re.split(r"[.\s]{2,}", ln)[0]) for ln in lines]
    sortedness = sum(1 for a, b in zip(heads, heads[1:]) if a <= b) / max(len(heads) - 1, 1)
    return min(shape_ratio, sortedness)


def looks_like_title_page(node: Node, page_no: int) -> float:
    lines = _leaf_lines(node)
    total_chars = sum(len(ln) for ln in lines)
    return 1.0 if (page_no <= 1 and total_chars < 400) else 0.0


# --------------------------------------------------------------------------- #
# Zone detection — the position signal
# --------------------------------------------------------------------------- #
def find_body_start_index(sections: list[Node]) -> int:
    """Index of the first section starting the numbered normative body (bare top-level
    integer clause_id, e.g. the "1 Scope" clause). Sections before this index are the
    only front-zone exclusion candidates. Returns len(sections) if never found — i.e.
    nothing is excluded if the body start can't be confidently located (fail safe)."""
    for i, s in enumerate(sections):
        if s.clause_id and TOP_LEVEL_CLAUSE.match(s.clause_id):
            return i
    return len(sections)


def find_back_zone_start(sections: list[Node], threshold: float = 0.5) -> int:
    """Walking backward from the end, the first index that ISN'T index-shaped ends the
    back zone. Sections from that point to the end are back-zone candidates."""
    i = len(sections)
    while i > 0 and looks_like_index(sections[i - 1]) >= threshold:
        i -= 1
    return i


# --------------------------------------------------------------------------- #
# Per-role thresholds. "preface" is deliberately stricter and always flagged.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RoleThreshold:
    shape_weight: float
    dict_weight: float
    exclude_at: float          # combined score >= this -> compliance_relevant=False
    always_review_if_excluded: bool = False


THRESHOLDS: dict[SectionRole, RoleThreshold] = {
    "toc":              RoleThreshold(shape_weight=0.8, dict_weight=0.2, exclude_at=0.55),
    "list_of_figures":  RoleThreshold(shape_weight=0.7, dict_weight=0.3, exclude_at=0.55),
    "list_of_tables":   RoleThreshold(shape_weight=0.7, dict_weight=0.3, exclude_at=0.55),
    "index":            RoleThreshold(shape_weight=0.8, dict_weight=0.2, exclude_at=0.55),
    "foreword":         RoleThreshold(shape_weight=0.2, dict_weight=0.8, exclude_at=0.65),
    "title_page":       RoleThreshold(shape_weight=1.0, dict_weight=0.0, exclude_at=0.9),
    "preface":          RoleThreshold(shape_weight=0.2, dict_weight=0.8, exclude_at=0.75,
                                       always_review_if_excluded=True),
}


@dataclass
class ClassificationContext:
    rulepack: RolePack
    known_caption_numbers: set[str] = field(default_factory=set)


def classify_section(
    node: Node, zone: str, ctx: ClassificationContext, page_no: int = 1,
) -> Node:
    """Returns a NEW Node (Pydantic models are copy-on-write here) with section_role /
    compliance_relevant / role_confidence / review_* populated. Body-zone sections are
    always left untouched — the strongest guardrail in this module."""
    if zone == "body":
        return node  # never a candidate; position alone protects the normative core

    heading = node.text or ""
    candidates: list[tuple[SectionRole, float]] = []

    if zone == "front":
        candidates.append(("toc", looks_like_toc(node)))
        candidates.append(("list_of_figures",
                            looks_like_caption_list(node, ctx.known_caption_numbers)))
        candidates.append(("list_of_tables",
                            looks_like_caption_list(node, ctx.known_caption_numbers)))
        candidates.append(("title_page", looks_like_title_page(node, page_no)))
        candidates.append(("foreword", 0.0))   # shape signal weak; dictionary carries it
        candidates.append(("preface", 0.0))
    else:  # back zone
        candidates.append(("index", looks_like_index(node)))

    best_role: Optional[SectionRole] = None
    best_score = 0.0
    for role, shape_score in candidates:
        th = THRESHOLDS[role]
        dict_score = 1.0 if ctx.rulepack.matches(heading, role) else 0.0
        combined = th.shape_weight * shape_score + th.dict_weight * dict_score
        if combined > best_score:
            best_role, best_score = role, combined

    if best_role is None or best_score < THRESHOLDS[best_role].exclude_at:
        # Fail-safe default: not confident enough -> stays normative, but note the
        # near-miss so a reviewer can see the pipeline considered and rejected it.
        reasons = (["ambiguous_section_role"] if best_role and best_score > 0.3 else [])
        return node.model_copy(update={
            "review_required": node.review_required or bool(reasons),
            "review_reasons": node.review_reasons + reasons,
        })

    th = THRESHOLDS[best_role]
    reasons = list(node.review_reasons)
    review = node.review_required
    if th.always_review_if_excluded:
        reasons.append(f"excluded_as_{best_role}_verify")
        review = True

    return node.model_copy(update={
        "section_role": best_role,
        "compliance_relevant": False,
        "role_confidence": round(best_score, 3),
        "review_required": review,
        "review_reasons": reasons,
    })


def classify_document(top_level_sections: list[Node], rulepack: RolePack) -> list[Node]:
    """Entry point: classify every top-level section of a CanonicalEdition. Exclusion
    never cascades implicitly here — chunk.leaf/rollup in comparison-engine skips a
    subtree only when its OWN top-level ancestor is compliance_relevant=False; nested
    normative content misfiled under a misclassified parent is still visible in the
    verification UI via review_reasons, so it's catchable, never silently lost."""
    known_captions = {
        m.group(0).strip().rstrip(".")
        for sec in top_level_sections
        for ln in _leaf_lines(sec)
        if sec.type == "figure" or sec.type == "table"
        for m in [_LEADING_NUMBER.match(ln)] if m
    }
    ctx = ClassificationContext(rulepack=rulepack, known_caption_numbers=known_captions)

    body_start = find_body_start_index(top_level_sections)
    back_start = find_back_zone_start(top_level_sections)
    back_start = max(back_start, body_start)   # zones never overlap

    out: list[Node] = []
    for i, sec in enumerate(top_level_sections):
        zone = "front" if i < body_start else ("back" if i >= back_start else "body")
        page_no = sec.provenance.page
        out.append(classify_section(sec, zone, ctx, page_no=page_no))
    return out


# --------------------------------------------------------------------------- #
# Smoke test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from canonical_schema import Provenance

    def para(text: str, page: int = 1) -> Node:
        return Node(id=f"p-{hash(text) & 0xffff}", type="paragraph", text=text,
                    provenance=Provenance(page=page, bbox=(0, 0, 1, 1), parser="docling",
                                          model_version="v1", confidence=0.95))

    def section(id_: str, heading: str, clause_id: Optional[str], children: list[Node],
                page: int = 1) -> Node:
        return Node(id=id_, type="section", text=heading, clause_id=clause_id,
                    children=children,
                    provenance=Provenance(page=page, bbox=(0, 0, 1, 1), parser="docling",
                                          model_version="v1", confidence=0.95))

    toc_lines = [para(f"{i} Some Clause Title .......... {i+3}") for i in range(1, 12)]
    foreword_lines = [para("This document was prepared by Technical Committee ...")]
    scope_lines = [para("This standard specifies limits for radiated emissions ...")]
    limit_table = section("t1", "Table 1", None, [], page=6)
    limit_table.type = "table"

    sections = [
        section("s0", "Table of Contents", None, toc_lines, page=2),
        section("s1", "Foreword", None, foreword_lines, page=3),
        section("s2", "1", "1", scope_lines, page=5),          # normative body start
        section("s3", "2", "2", [para("Normative references ...")], page=6),
    ]

    rulepack = RolePack.load(Path(__file__).parents[2] / "rulepacks" / "section_roles.yaml")
    classified = classify_document(sections, rulepack)

    for sec in classified:
        print(f"{sec.id:>4} role={sec.section_role:<10} relevant={sec.compliance_relevant} "
              f"conf={sec.role_confidence:.2f} review={sec.review_required} "
              f"reasons={sec.review_reasons}")

    assert classified[0].section_role == "toc" and not classified[0].compliance_relevant
    assert classified[1].section_role == "foreword" and not classified[1].compliance_relevant
    assert classified[2].section_role == "normative" and classified[2].compliance_relevant
    assert classified[3].section_role == "normative" and classified[3].compliance_relevant
    print("\nALL ASSERTIONS PASSED")
