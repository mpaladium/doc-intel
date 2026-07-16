"""Consensus engine (app/pipeline/consensus.py) -- the disagreement branch from
parser-consensus.md: unanimity, normative-unanimity, majority, authority
isolation, the three-parser table rule, the normalization allow/deny list, and
the OCR ceiling. Pure functions, no PDF needed."""

import pytest

from app.pipeline.consensus import (
    OCR_CONFIDENCE_CEILING, apply_ocr_ceiling, normalize_for_compare,
    reconcile_table_grid, reconcile_text,
)
from app.pipeline.extract_pdfplumber import GridShape


# --- normalization: what it MAY collapse, and what it must NEVER --------------

def test_normalize_collapses_whitespace_and_hyphenation():
    assert normalize_for_compare("shall   be\n10 V/m") == "shall be 10 V/m"
    assert normalize_for_compare("over-\nload") == "overload"
    assert normalize_for_compare("  trimmed  ") == "trimmed"


def test_normalize_never_touches_the_content_that_is_the_point():
    # case, ±, ≤, superscript survive normalization -- these ARE the content
    assert normalize_for_compare("SHALL") != normalize_for_compare("shall")
    assert normalize_for_compare("±0.5") == "±0.5"
    assert normalize_for_compare("≤ 40") == "≤ 40"
    assert normalize_for_compare("10⁻³") == "10⁻³"  # not flattened to 10-3


# --- text consensus branch ----------------------------------------------------

def test_unanimous_when_all_agree_after_whitespace_norm():
    r = reconcile_text({"pymupdf": "shall be 10 V/m", "docling": "shall  be 10 V/m"},
                       authority="pymupdf", normative=False)
    assert r.state == "unanimous"


def test_normative_dissent_quarantines_even_if_majority():
    # authority + one corroborator agree, one dissents -> plain majority would
    # admit, but a normative object requires unanimity.
    r = reconcile_text({"pymupdf": "≤ 40 dBµV/m", "docling": "≤ 40 dBµV/m",
                        "surya": "< 40 dBuV/m"},
                       authority="pymupdf", normative=True)
    assert r.state == "quarantined"
    assert "surya" in r.dissenters


def test_majority_admits_non_normative_with_dissent_recorded():
    r = reconcile_text({"pymupdf": "informative note text", "docling": "informative note text",
                        "surya": "informative n0te text"},
                       authority="pymupdf", normative=False)
    assert r.state == "majority"
    assert r.dissenters == ("surya",)
    assert r.candidates["surya"] == "informative n0te text"  # loser kept, not discarded


def test_authority_isolated_quarantines():
    # authority disagrees with BOTH corroborators (who agree with each other)
    r = reconcile_text({"pymupdf": "value A", "docling": "value B", "pdfplumber": "value B"},
                       authority="pymupdf", normative=False)
    assert r.state == "quarantined"
    assert "isolated" in r.reason


def test_missing_authority_quarantines():
    r = reconcile_text({"docling": "text"}, authority="pymupdf", normative=False)
    assert r.state == "quarantined"


# --- table geometry: all three or quarantine ----------------------------------

def _grid(r, c, spans=()):
    return GridShape(n_rows=r, n_cols=c, spans=tuple(spans))


def test_table_unanimous_when_all_three_agree():
    r = reconcile_table_grid({"docling": _grid(5, 3), "pdfplumber": _grid(5, 3),
                              "pymupdf": _grid(5, 3)})
    assert r.state == "unanimous"


def test_table_quarantines_on_any_geometry_disagreement():
    # a merged-cell collapse: one parser sees 4 rows where the others see 5
    r = reconcile_table_grid({"docling": _grid(5, 3), "pdfplumber": _grid(4, 3),
                              "pymupdf": _grid(5, 3)})
    assert r.state == "quarantined"
    assert r.dissenters == ("pdfplumber",)


def test_table_quarantines_on_span_map_disagreement():
    r = reconcile_table_grid({"docling": _grid(5, 3, [(0, 0, 1, 2)]),
                              "pdfplumber": _grid(5, 3),
                              "pymupdf": _grid(5, 3)})
    assert r.state == "quarantined"


def test_table_with_fewer_than_three_opinions_quarantines():
    r = reconcile_table_grid({"docling": _grid(5, 3), "pymupdf": _grid(5, 3)})
    assert r.state == "quarantined"
    assert "3 opinions" in r.reason


# --- OCR ceiling --------------------------------------------------------------

def test_ocr_confidence_is_capped():
    assert apply_ocr_ceiling(0.99, is_ocr=True) == OCR_CONFIDENCE_CEILING
    assert apply_ocr_ceiling(0.80, is_ocr=True) == 0.80
    assert apply_ocr_ceiling(0.99, is_ocr=False) == 0.99
