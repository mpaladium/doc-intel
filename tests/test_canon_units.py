"""Unit coverage for canon.units (app/pipeline/canon_units.py) -- structured
{value, unit, condition} extraction from table cells, conservative so prose is
never parsed as a measurement."""

from canonical_schema import Cell, Node, Provenance
from app.pipeline import canon_units
from app.pipeline.canon_units import parse_quantity


def test_value_with_unit():
    q = parse_quantity("0,5 m/s²")
    assert q.value == "0,5" and q.unit == "m/s^2"


def test_range_value():
    q = parse_quantity("230 - 1000")
    assert q.value == "230-1000"


def test_comparator_preserved():
    q = parse_quantity("≤ 40 dBµV/m")
    assert q.value == "≤40" and q.unit == "dBµV/m"


def test_unit_normalization_variants():
    assert parse_quantity("66 dBuV/m").unit == "dBµV/m"
    assert parse_quantity("100 MHz").unit == "MHz"


def test_condition_captured():
    q = parse_quantity("40 dBµV/m at 3 m")
    assert q.condition == "at 3 m"
    q2 = parse_quantity("66 dBµV/m (peak)")
    assert q2.condition == "(peak)"


def test_bare_value_takes_unit_from_header():
    q = parse_quantity("40", header_path=["Limit (dBµV/m)"])
    assert q.value == "40" and q.unit == "dBµV/m"


def test_bare_value_without_unit_header_has_no_unit():
    q = parse_quantity("40", header_path=["Class"])
    assert q.value == "40" and q.unit is None


def test_prose_is_not_a_quantity():
    assert parse_quantity("Test Sec.3 [14.6]") is None
    assert parse_quantity("Electrical slow transient") is None
    assert parse_quantity("Class A") is None
    assert parse_quantity("") is None


def _prov():
    return Provenance(page=1, bbox=(0, 0, 1, 1), parser="docling", model_version="v1", confidence=0.9)


def test_annotate_node_sets_quantity_on_data_cells_only():
    table = Node(id="t", type="table", provenance=_prov(), cells=[
        Cell(row=0, col=0, text="Limit (dBµV/m)", is_column_header=True),
        Cell(row=1, col=0, text="40", header_path=["Limit (dBµV/m)"]),
        Cell(row=2, col=0, text="see clause 5", header_path=["Limit (dBµV/m)"]),
    ])
    out = canon_units.annotate_node(table)
    by_row = {c.row: c for c in out.cells}
    assert by_row[0].quantity is None            # header cell untouched
    assert by_row[1].quantity.value == "40"      # measurement parsed
    assert by_row[1].quantity.unit == "dBµV/m"
    assert by_row[2].quantity is None            # prose cell not parsed
