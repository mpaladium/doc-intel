"""CDM v2 schema contract (docs/references/canonical-model.md): runs
reconstruction, Parameter Decimal/comparator, normative detection, identity."""

from decimal import Decimal

import canonical_schema as cs


def _prov():
    return cs.Provenance(page=1, bbox=(0, 0, 1, 1), parser="pymupdf",
                         model_version="v1", confidence=0.9)


def test_runs_reconstruct_superscript_codepoint():
    # "10⁻³ V/m": base 10, superscript "-3", then " V/m".
    runs = [
        cs.Run(text="10", font="Arial", size=10),
        cs.Run(text="-3", font="Arial", size=6, vertical_align="superscript", baseline_offset=3.0),
        cs.Run(text=" V/m", font="Arial", size=10),
    ]
    assert cs.reconstruct_raw_text(runs) == "10⁻³ V/m"
    # and NOT the flattened "10-3 V/m" that would parse as 7
    assert cs.reconstruct_raw_text(runs) != "10-3 V/m"


def test_runs_reconstruct_subscript():
    runs = [cs.Run(text="H", font="A", size=10),
            cs.Run(text="2", font="A", size=6, vertical_align="subscript", baseline_offset=-2.0),
            cs.Run(text="O", font="A", size=10)]
    assert cs.reconstruct_raw_text(runs) == "H₂O"


def test_parameter_decimal_and_required_comparator():
    p = cs.Parameter(name="field_strength", quantity_kind="electric_field",
                     value=Decimal("10"), unit="V/m", comparator="gte", condition="80 MHz - 1 GHz")
    assert p.value == Decimal("10")
    assert isinstance(p.value, Decimal)
    assert p.comparator == "gte"
    # a parameter with no comparator is representable (so the gate can quarantine
    # it) but comparator is the field the unit gate checks for.
    assert cs.Parameter(name="x", value=Decimal("1")).comparator is None


def test_is_normative_by_cdm_type_or_parameter():
    n = cs.Node(id="x", type="paragraph", text="prose", provenance=_prov())
    assert not cs.is_normative(n)
    assert cs.is_normative(n.model_copy(update={"cdm_type": "Requirement"}))
    assert cs.is_normative(n.model_copy(update={"cdm_type": "Warning"}))
    # a plain paragraph carrying a Parameter is normative (limit-bearing)
    withparam = n.model_copy(update={"parameters": [cs.Parameter(name="v", value=Decimal("40"),
                                                                 unit="dBuV/m", comparator="lte")]})
    assert cs.is_normative(withparam)
    # a Note is not normative even if non-unanimous
    assert not cs.is_normative(n.model_copy(update={"cdm_type": "Note"}))


def test_identity_scheme():
    assert cs.make_object_id("doc1", ["4", "2", "3"], "txt") == "doc1#4.2.3"
    assert cs.make_object_id("doc1", ["1"], "t", standard_id="IEC61000-4-3") == "IEC61000-4-3#1"
    # unnumbered content -> content hash
    uid = cs.make_object_id("doc1", None, "some text")
    assert uid.startswith("doc1#") and len(uid.split("#")[1]) == 12


def test_node_defaults_are_backward_compatible():
    # An existing single-parser node with no consensus fields set is trivially
    # unanimous and not quarantined.
    n = cs.Node(id="x", type="paragraph", text="t", provenance=_prov())
    assert n.consensus == "unanimous"
    assert n.quarantine_reason is None
    assert n.runs == [] and n.parsers == {} and n.parameters == []
    assert n.cdm_type is None
