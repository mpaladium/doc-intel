"""Pytest port of section_role_classifier.py's own __main__ smoke test, plus
the pseudo-line-splitting regression this repo's Docling integration needed
(see app/pipeline/section_role_classifier.py's _split_pseudo_lines docstring)."""

from pathlib import Path

from canonical_schema import Node, Provenance
from app.pipeline.section_role_classifier import RolePack, classify_document

RULEPACK_PATH = Path(__file__).parents[1] / "rulepacks" / "section_roles.yaml"


def _para(text: str, page: int = 1) -> Node:
    return Node(id=f"p-{hash(text) & 0xffff}", type="paragraph", text=text,
                provenance=Provenance(page=page, bbox=(0, 0, 1, 1), parser="docling",
                                      model_version="v1", confidence=0.95))


def _section(id_: str, heading: str, clause_id, children: list[Node], page: int = 1) -> Node:
    return Node(id=id_, type="section", text=heading, clause_id=clause_id,
                children=children,
                provenance=Provenance(page=page, bbox=(0, 0, 1, 1), parser="docling",
                                      model_version="v1", confidence=0.95))


def test_toc_and_foreword_excluded_body_zone_untouched():
    toc_lines = [_para(f"{i} Some Clause Title .......... {i + 3}") for i in range(1, 12)]
    foreword_lines = [_para("This document was prepared by Technical Committee ...")]
    scope_lines = [_para("This standard specifies limits for radiated emissions ...")]

    sections = [
        _section("s0", "Table of Contents", None, toc_lines, page=2),
        _section("s1", "Foreword", None, foreword_lines, page=3),
        _section("s2", "1", "1", scope_lines, page=5),
        _section("s3", "2", "2", [_para("Normative references ...")], page=6),
    ]

    rulepack = RolePack.load(RULEPACK_PATH)
    classified = classify_document(sections, rulepack)

    assert classified[0].section_role == "toc" and not classified[0].compliance_relevant
    assert classified[1].section_role == "foreword" and not classified[1].compliance_relevant
    assert classified[2].section_role == "normative" and classified[2].compliance_relevant
    assert classified[3].section_role == "normative" and classified[3].compliance_relevant


def test_merged_multiline_block_still_detected_as_toc():
    """Regression: Docling can merge several visually-adjacent short lines into
    one TextItem, space-joined with no line-break marker. The classifier must
    still recognize the shape from the merged block."""
    merged_text = " ".join(f"Clause {i} " + ("." * 20) + f" {i + 3}" for i in range(1, 12))
    toc_section = _section("s0", "Table of Contents", None, [_para(merged_text)], page=2)
    body_section = _section("s1", "1", "1", [_para("Scope text.")], page=3)

    rulepack = RolePack.load(RULEPACK_PATH)
    classified = classify_document([toc_section, body_section], rulepack)

    assert classified[0].section_role == "toc"
    assert not classified[0].compliance_relevant


def test_ambiguous_section_fails_toward_inclusion():
    """A front-zone section that doesn't clear any role's exclude_at threshold
    must stay normative and compliance_relevant -- never silently dropped."""
    ambiguous = _section("s0", "Random Preamble Heading", None,
                          [_para("Some unrelated short blurb.")], page=2)
    body = _section("s1", "1", "1", [_para("Scope text.")], page=3)

    rulepack = RolePack.load(RULEPACK_PATH)
    classified = classify_document([ambiguous, body], rulepack)

    assert classified[0].section_role == "normative"
    assert classified[0].compliance_relevant is True
