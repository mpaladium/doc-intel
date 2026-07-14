"""canon.units -- extract structured {value, unit, condition} from table cells
(SKILLS.md). This is the value-level signal comparison-engine uses to detect a
limit change (40 -> 30 dBµV/m), instead of diffing raw cell strings.

Deterministic, offline: a curated EMC/standards unit vocabulary + anchored
regex, no model. The full pint unit *algebra* (TECHSTACK.md) remains the
downstream swap-in for canonical arithmetic; this stage only parses the surface
value/unit/condition out of the cell so it's structured and comparable.

Conservative by construction: a cell is parsed only if its whole text is a
value (+optional unit/condition) -- a prose cell with an incidental number
("Test Sec.3 [14.6]") does not match, so quantities are never invented.
"""

from __future__ import annotations

import re

from canonical_schema import Cell, Node, Quantity

# Unit vocabulary. Keys are matched case-sensitively-ish via the regex; values
# are the normalized form. Ordered longest-first in the alternation so
# "dBµV/m" wins over "dB". Extend per standards family as needed.
_UNIT_NORMALIZE = {
    "dbµv/m": "dBµV/m", "dbuv/m": "dBµV/m", "db(µv/m)": "dBµV/m", "db(uv/m)": "dBµV/m",
    "dbµa/m": "dBµA/m", "dbua/m": "dBµA/m",
    "dbm": "dBm", "dbµv": "dBµV", "dbuv": "dBµV", "db": "dB",
    "v/m": "V/m", "kv/m": "kV/m",
    "m/s²": "m/s^2", "m/s2": "m/s^2", "m/s^2": "m/s^2",
    "ghz": "GHz", "mhz": "MHz", "khz": "kHz", "hz": "Hz",
    "kv": "kV", "mv": "mV", "µv": "µV", "uv": "µV", "v": "V",
    "ma": "mA", "µa": "µA", "ua": "µA", "a": "A",
    "kω": "kΩ", "mω": "MΩ", "ω": "Ω", "ohm": "Ω",
    "kw": "kW", "mw": "mW", "w": "W",
    "°c": "°C", "k": "K",
    "ms": "ms", "µs": "µs", "us": "µs", "ns": "ns", "s": "s",
    "mm": "mm", "cm": "cm", "km": "km", "m": "m",
    "%": "%",
}
# Longest-first so multi-char units match before their prefixes.
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(_UNIT_NORMALIZE, key=len, reverse=True))

# A numeric value: optional comparator, a number (German comma or dot),
# optionally a range "a - b". Kept as surface text.
_NUM = r"\d+(?:[.,]\d+)?"
_VALUE = rf"(?:[<>≤≥±]|<=|>=)?\s*{_NUM}(?:\s*[-–—]\s*{_NUM})?"

# Whole cell = value (+unit) (+condition). Anchored so prose doesn't match.
_QUANTITY = re.compile(
    rf"^\s*(?P<value>{_VALUE})\s*(?P<unit>{_UNIT_ALT})?\s*"
    rf"(?P<cond>\([^)]+\)|(?:at|bei|@)\s+\S.*)?\s*$",
    re.IGNORECASE,
)
# A unit sitting in a column header, e.g. "Limit (dBµV/m)" -> the column's unit.
_HEADER_UNIT = re.compile(rf"\((?P<unit>{_UNIT_ALT})\)", re.IGNORECASE)


def _normalize_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    return _UNIT_NORMALIZE.get(raw.strip().lower())


def _unit_from_header_path(header_path: list[str]) -> str | None:
    for h in reversed(header_path):  # nearest (deepest) header first
        if m := _HEADER_UNIT.search(h):
            return _normalize_unit(m.group("unit"))
    return None


def parse_quantity(text: str, header_path: list[str] | None = None) -> Quantity | None:
    """Parse a cell's whole text into a Quantity, or None if it isn't a
    measurement. Falls back to the column header for the unit when the cell
    itself has only a bare value ("40" under a "Limit (dBµV/m)" header)."""
    m = _QUANTITY.match(text.strip())
    if not m:
        return None
    value = re.sub(r"\s+", "", m.group("value"))
    if not value or not any(c.isdigit() for c in value):
        return None
    unit = _normalize_unit(m.group("unit")) or (
        _unit_from_header_path(header_path or []) if header_path else None)
    cond = m.group("cond")
    condition = cond.strip() if cond else None
    return Quantity(value=value, unit=unit, condition=condition)


def _annotate_cell(cell: Cell) -> Cell:
    if cell.is_column_header:
        return cell
    q = parse_quantity(cell.text, cell.header_path)
    if q is None:
        return cell
    return cell.model_copy(update={"quantity": q})


def annotate_node(node: Node) -> Node:
    """Depth-first: attach a parsed `Quantity` to every measurement cell of
    every table. Same rebuild-children-first `model_copy` pattern as the other
    canon/topology passes. Runs after `continuity.assign_header_paths` so the
    header-unit fallback has lineage to read."""
    children = [annotate_node(c) for c in node.children]
    node = node.model_copy(update={"children": children})
    if node.type == "table" and node.cells:
        node = node.model_copy(update={"cells": [_annotate_cell(c) for c in node.cells]})
    return node
