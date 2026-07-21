"""Normative typing (classify_type) + Parameter extraction (parameters) --
Phase 5. Modal-driven closed-type assignment, the multilingual modal set, and
Decimal/comparator/tolerance/condition parameter parsing."""

from decimal import Decimal

import pytest

import canonical_schema as cs
from app.pipeline import classify_type, parameters


def _prov():
    return cs.Provenance(page=1, bbox=(0, 0, 10, 10), parser="pymupdf",
                         model_version="v", confidence=0.9)


def _node(**kw):
    kw.setdefault("id", "n")
    kw.setdefault("type", "paragraph")
    kw.setdefault("provenance", _prov())
    return cs.Node(**kw)


# --- classify_type ------------------------------------------------------------

def test_shall_becomes_requirement():
    assert classify_type.classify_node(_node(text="the unit shall comply", lang="en")) == "Requirement"


def test_should_becomes_recommendation():
    assert classify_type.classify_node(_node(text="the unit should comply", lang="en")) == "Recommendation"


def test_may_becomes_permission():
    assert classify_type.classify_node(_node(text="the unit may be earthed", lang="en")) == "Permission"


def test_warning_admonition_outranks_modal():
    n = _node(text="WARNING — the unit shall not be opened while live", lang="en")
    assert classify_type.classify_node(n) == "Warning"


def test_requirement_outranks_permission_in_same_clause():
    n = _node(text="the unit may be portable but shall be earthed", lang="en")
    assert classify_type.classify_node(n) == "Requirement"


def test_german_muss_is_requirement_not_translated():
    assert classify_type.classify_node(_node(text="das Gerät muss geerdet sein", lang="de")) == "Requirement"


def test_french_il_convient_de_is_recommendation():
    # il convient de == should, NOT must -- the trap the spec warns about
    assert classify_type.classify_node(_node(text="il convient de vérifier", lang="fr")) == "Recommendation"


def test_scope_heading_role():
    assert classify_type.classify_node(_node(type="section", text="1 Scope", lang="en")) == "Scope"


def test_plain_prose_stays_unassigned():
    assert classify_type.classify_node(_node(text="this figure illustrates the setup", lang="en")) is None


def test_annotate_assigns_and_preserves_existing():
    tree = _node(id="r", type="section", children=[
        _node(id="a", text="the unit shall comply", lang="en"),
        _node(id="b", text="already typed", lang="en", cdm_type="Note"),
    ])
    out = classify_type.annotate_node(tree)
    by_id = {c.id: c for c in out.children}
    assert by_id["a"].cdm_type == "Requirement"
    assert by_id["b"].cdm_type == "Note"  # not overwritten


# --- parameters ---------------------------------------------------------------

def test_parse_upper_bound_with_band():
    ps = parameters.parse_parameters(
        "The field strength shall be ≤ 40 dBµV/m at 80 MHz - 1 GHz", lang="en")
    assert len(ps) == 1
    p = ps[0]
    assert p.value == Decimal("40")
    assert isinstance(p.value, Decimal)
    assert p.unit == "dBµV/m"
    assert p.comparator == "lte"
    assert p.condition == "80 MHz - 1 GHz"
    assert p.quantity_kind == "field_strength"


def test_parse_tolerance():
    ps = parameters.parse_parameters("the field shall be 10 ± 0.5 V/m", lang="en")
    assert len(ps) == 1
    assert ps[0].value == Decimal("10")
    assert ps[0].tolerance is not None
    assert ps[0].tolerance.value == Decimal("0.5")


def test_phrase_comparator_at_least():
    ps = parameters.parse_parameters("shall be at least 10 V/m", lang="en")
    assert ps[0].comparator == "gte"


def test_missing_comparator_stays_none_not_eq():
    ps = parameters.parse_parameters("the field shall be 10 V/m", lang="en")
    assert ps and ps[0].comparator is None  # gate quarantines; never defaults eq


def test_band_numbers_not_read_as_limits():
    ps = parameters.parse_parameters("limit 40 dBµV/m over 80 MHz - 1 GHz", lang="en")
    # the two frequency numbers are the condition, not limits
    assert [p.unit for p in ps] == ["dBµV/m"]


def test_no_parameter_from_incidental_number():
    assert parameters.parse_parameters("see Test Section 3 item 14", lang="en") == []


def test_annotate_attaches_to_paragraph():
    n = _node(id="p", text="shall be ≤ 40 dBµV/m at 80 MHz - 1 GHz", lang="en")
    out = parameters.annotate_node(n)
    assert out.parameters and out.parameters[0].comparator == "lte"


# --- table-cell Parameter precision (T1) --------------------------------------

def _table(cells):
    return cs.Node(id="t", type="table", provenance=_prov(), cells=cells)


def test_conditions_table_cells_not_promoted_to_parameters():
    # a test-conditions table (frequency / forward power) -- numeric cells are
    # NOT limits and must not become comparator-less Parameters (the eval flood)
    t = _table([
        cs.Cell(row=0, col=0, text="Frequenz in MHz", is_column_header=True),
        cs.Cell(row=0, col=1, text="P vor in W", is_column_header=True),
        cs.Cell(row=1, col=0, text="100", header_path=["Frequenz in MHz"]),
        cs.Cell(row=1, col=1, text="500", header_path=["P vor in W"]),
    ])
    out = parameters.annotate_node(t)
    assert out.parameters == []


def test_limit_column_bare_value_is_promoted():
    # a genuine limit column ("Grenzwert in dBµV/m") with a bare value: the unit
    # comes from the prose header, and the limit keyword makes it a Parameter
    t = _table([
        cs.Cell(row=0, col=0, text="Grenzwert in dBµV/m", is_column_header=True),
        cs.Cell(row=1, col=0, text="40", header_path=["Grenzwert in dBµV/m"]),
    ])
    out = parameters.annotate_node(t)
    assert len(out.parameters) == 1
    assert out.parameters[0].value == Decimal("40")
    assert out.parameters[0].unit == "dBµV/m"
    # bare value -> comparator stays None (units gate will flag for human ≤ check)
    assert out.parameters[0].comparator is None


def test_cell_with_comparator_symbol_is_a_limit_regardless_of_header():
    t = _table([
        cs.Cell(row=0, col=0, text="Level", is_column_header=True),
        cs.Cell(row=1, col=0, text="≤ 40 dBµV/m", header_path=["Level"]),
    ])
    out = parameters.annotate_node(t)
    assert len(out.parameters) == 1
    assert out.parameters[0].comparator == "lte"


def test_band_name_cell_not_promoted_to_length_parameter():
    # "70 cm" here is a radio band name in a conditions column, not a length limit
    t = _table([
        cs.Cell(row=0, col=0, text="Frequenzband", is_column_header=True),
        cs.Cell(row=1, col=0, text="70 cm", header_path=["Frequenzband"]),
    ])
    out = parameters.annotate_node(t)
    assert out.parameters == []


# --- Parameter richness (T8) --------------------------------------------------

def test_asymmetric_tolerance():
    ps = parameters.parse_parameters("the level shall be 10 +0.5/-0.2 V/m", lang="en")
    assert len(ps) == 1
    t = ps[0].tolerance
    assert t.type == "asymmetric"
    assert t.value == Decimal("0.5") and t.value_upper == Decimal("0.2")


def test_relative_tolerance_percent():
    ps = parameters.parse_parameters("shall be 10 ± 5 % V/m", lang="en")
    assert ps[0].tolerance.type == "relative"
    assert ps[0].tolerance.value == Decimal("5")
    assert ps[0].tolerance.unit == "%"


def test_range_parameter():
    ps = parameters.parse_parameters("field strength 10 - 15 V/m", lang="en")
    assert len(ps) == 1
    p = ps[0]
    assert p.comparator == "range"
    assert p.range == (Decimal("10"), Decimal("15"))
    assert p.value is None


def test_unit_prefix_word_boundary_not_matched():
    # "DNVGL-CP-0203 may" must not fabricate value=203 unit=mA ("ma" of "may")
    ps = parameters.parse_parameters(
        "The Society's document DNVGL-CP-0203 may be used as a guideline.", lang="en")
    assert ps == []


def test_value_glued_to_preceding_letters_not_matched():
    # a designator number directly glued to a preceding letter is not a value
    assert parameters.parse_parameters("Model X100 Hz", lang="en") == []
    assert parameters.parse_parameters("item14 Hz", lang="en") == []


def test_soft_hyphen_range_becomes_band_condition_not_bare_value():
    # U+00AD between digits is a PDF hyphenation-break artifact standing in
    # for a literal range separator -- must not fabricate a bare frequency
    # parameter (previously: value=100, unit=Hz, comparator=None)
    ps = parameters.parse_parameters(
        "the frequency range 3­100 Hz.", lang="en")
    assert ps == []


def test_band_with_unit_only_on_right_number():
    # "3-100 Hz" (unit stated once, covering the whole range) is a condition,
    # same as "80 MHz to 1 GHz" -- previously required a unit on BOTH sides
    ps = parameters.parse_parameters("limit 40 dBµV/m in the range 3-100 Hz", lang="en")
    assert len(ps) == 1
    assert ps[0].value == Decimal("40") and ps[0].condition == "3-100 Hz"


def test_leading_standalone_tolerance_becomes_range_not_bare_value():
    ps = parameters.parse_parameters(
        "tolerances at the control point: ± 10%, at the attachment point: ± 15%.",
        lang="en")
    assert len(ps) == 2
    assert ps[0].value is None and ps[0].range == (Decimal("-10"), Decimal("10"))
    assert ps[0].comparator == "range" and ps[0].unit == "%"
    assert ps[0].tolerance is not None and ps[0].tolerance.value == Decimal("10")
    assert ps[1].range == (Decimal("-15"), Decimal("15"))


def test_leading_tolerance_does_not_regress_trailing_tolerance():
    # "10 ± 1 %" must still parse as ONE value+tolerance parameter, not be
    # double-matched by the new leading-± branch
    ps = parameters.parse_parameters("shall be 10 ± 1 %", lang="en")
    assert len(ps) == 1
    assert ps[0].value == Decimal("10")
    assert ps[0].tolerance is not None and ps[0].tolerance.value == Decimal("1")
    assert ps[0].range is None


def test_german_decimal_comma_is_decimal():
    ps = parameters.parse_parameters("die Feldstärke muss ≤ 0,5 V/m sein", lang="de")
    assert ps[0].value == Decimal("0.5")


def test_english_thousands_comma_not_decimal():
    ps = parameters.parse_parameters("shall be 1,500 V/m", lang="en")
    assert ps[0].value == Decimal("1500")  # thousands, not 1.5


def test_ambiguous_decimal_flags_node_for_review():
    # "3,5" in an English doc is ambiguous -> parameter emitted but node flagged
    n = _node(id="p", text="the level shall be ≤ 3,5 V/m", lang="en")
    out = parameters.annotate_node(n)
    assert out.parameters
    assert out.review_required
    assert "ambiguous_decimal_locale" in out.review_reasons


def test_german_comma_not_flagged_ambiguous():
    n = _node(id="p", text="≤ 3,5 V/m", lang="de")
    out = parameters.annotate_node(n)
    assert out.parameters and not out.review_required
