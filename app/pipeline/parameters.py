"""parameters -- extract compliance-grade `Parameter` objects (Decimal value,
comparator, tolerance, condition, quantity_kind) from prose and table cells
(canonical-model.md §Parameter). The highest-value extraction in the system:
most consequential changes in EMC/safety standards are a number moving.

Design commitments straight from the spec:
  * `Decimal`, never float -- a compliance limit is not a place for binary
    rounding.
  * A missing comparator is left None (the unit/tolerance gate quarantines it);
    NEVER defaulted to `eq`. "shall be 10 V/m" and "at least 10 V/m" differ.
  * `condition` (the frequency band) is extracted alongside value -- a limit
    without its band is meaningless.

Conservative: a Parameter is emitted only for an explicit value+unit; an
incidental number in prose is not turned into a limit. Reuses canon_units'
unit vocabulary so prose and table cells canonicalize identically.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from canonical_schema import Cell, Comparator, Node, Parameter, Tolerance
from app.pipeline.canon_units import _UNIT_ALT, _normalize_unit, parse_quantity

# unit -> controlled quantity_kind vocabulary (canonical-model.md example
# "electric_field"). Keyed on the canonical unit string canon_units emits.
_QUANTITY_KIND: dict[str, str] = {
    "V/m": "electric_field", "kV/m": "electric_field", "dBµV/m": "field_strength",
    "dBµA/m": "magnetic_field_strength", "dBµV": "voltage", "dBm": "power_level",
    "dB": "gain", "V": "voltage", "kV": "voltage", "mV": "voltage", "µV": "voltage",
    "A": "current", "mA": "current", "µA": "current",
    "Ω": "resistance", "kΩ": "resistance", "MΩ": "resistance",
    "W": "power", "kW": "power", "mW": "power",
    "Hz": "frequency", "kHz": "frequency", "MHz": "frequency", "GHz": "frequency",
    "°C": "temperature", "K": "temperature", "%": "ratio",
    "s": "time", "ms": "time", "µs": "time", "ns": "time",
    "m": "length", "mm": "length", "cm": "length", "km": "length",
    "m/s^2": "acceleration",
}

_NUM = r"\d+(?:[.,]\d+)?"
# A tolerance immediately after the value: symmetric "± n", relative "± n %",
# or asymmetric "+a/-b" (canonical-model.md tolerance types).
_TOL = (
    rf"(?:±\s*(?P<tol>{_NUM})\s*(?P<tolpct>%)?"
    rf"|\+\s*(?P<tolp>{_NUM})\s*/\s*[-−]\s*(?P<tolm>{_NUM}))"
)
# a value + unit occurrence anywhere in prose, with an optional leading symbol
# comparator, an optional tolerance, and an optional range upper bound
# ("10 - 15 V/m", "10 to 15 V/m"). The range upper bound is captured only when
# it precedes the unit (so a frequency band "80 MHz - 1 GHz", unit after each
# number, is NOT read as a range -- it stays a condition, handled by _BAND).
# A leading standalone "± N unit" (no base value before it) is also captured via
# the `lead` group and emitted as a range-shaped parameter (see parse_parameters).
# Word boundaries: (?<!\w) blocks values glued to preceding letters ("item14 Hz"),
# (?!\w) blocks units matching as prefixes of unrelated words ("DNVGL-CP-0203 may").
_PARAM = re.compile(
    rf"(?:(?P<lead>±)\s*)?"
    rf"(?P<sym>[<>≤≥]|<=|>=)?\s*(?<!\w)(?P<value>{_NUM})"
    rf"(?:\s*{_TOL})?"
    rf"(?:\s*(?:[-–—]|to|bis)\s*(?P<hi>{_NUM}))?"
    rf"\s*(?P<unit>{_UNIT_ALT})(?!\w)",
    re.IGNORECASE,
)
# a frequency band condition: "80 MHz - 1 GHz", "80 MHz to 1 GHz", "3-100 Hz".
# The unit on the first number is now optional so "3-100 Hz" (unit stated once,
# at the end, covering the whole range) matches same as "80 MHz to 1 GHz".
_BAND = re.compile(
    rf"{_NUM}\s*(?:Hz|kHz|MHz|GHz)?\s*(?:[-–—]|to|bis|à)\s*{_NUM}\s*(?:Hz|kHz|MHz|GHz)",
    re.IGNORECASE)

_SYM_COMPARATOR: dict[str, Comparator] = {
    "≤": "lte", "<=": "lte", "<": "lte", "≥": "gte", ">=": "gte", ">": "gte"}

# A table cell is promoted to a Parameter only in a LIMIT context -- otherwise a
# plain test-conditions table (frequency, forward power, modulation) turns every
# numeric cell into a comparator-less Parameter that the units gate then
# quarantines, flooding the review queue with non-limits (the single largest
# quarantine driver in the eval). Limit context = a comparator symbol in the
# cell itself, OR a limit-keyword in the governing column header. Multilingual,
# conservative: a genuine limit column ("Grenzwert", "Limit", "max.") parses; a
# conditions column ("Frequenz", "P vor") does not. Under-extraction here is
# safe -- the cell text still lives on the node for text-level diffing; it's
# over-extraction that manufactures noise.
_LIMIT_KEYWORD = re.compile(
    r"limit|grenzwert|grenz\b|grenze|max\.?|maximum|min\.?|minimum|"
    r"höchst|mindest|toleranz|tolerance|threshold|schwelle|"
    r"[≤≥]|<=|>=", re.IGNORECASE)


def _is_limit_cell(cell_text: str, header_path: list[str] | None) -> bool:
    """Whether a table cell sits in a compliance-limit context (see
    _LIMIT_KEYWORD). A comparator symbol in the cell is limit context on its
    own; otherwise a limit keyword must appear in the column header lineage."""
    if re.search(r"[<>≤≥]|<=|>=", cell_text):
        return True
    return any(_LIMIT_KEYWORD.search(h) for h in (header_path or []))
# phrase comparators in a window before the value (per language, lowercased)
_PHRASE_COMPARATOR = [
    (re.compile(r"(?:at least|mindestens|au moins|minimum|not less than)\s*$", re.I), "gte"),
    (re.compile(r"(?:at most|maximum|up to|not exceed(?:ing)?|no more than|"
                r"höchstens|maximal|au plus|ne (?:doit|doivent) pas dépasser)\s*$", re.I), "lte"),
    (re.compile(r"(?:exactly|equal to|genau|exactement)\s*$", re.I), "eq"),
]


# Locales where "," is the decimal separator and "." groups thousands
# (verification-rules.md decimal-comma trap: "3,5" is 3.5 in DE, a list in EN).
_COMMA_DECIMAL_LANGS = frozenset({
    "de", "fr", "es", "it", "nl", "pt", "ru", "pl", "cs", "sv", "da", "fi", "nb", "tr"})
_THOUSANDS = re.compile(r"^\d{1,3}(,\d{3})+$")


def _lang_comma_decimal(lang: str | None) -> bool:
    return bool(lang) and lang.split("-")[0].lower() in _COMMA_DECIMAL_LANGS


def _decimal_ambiguous(surface: str, lang: str | None) -> bool:
    """A bare comma-number in a point-decimal / unknown locale is ambiguous
    ("3,5" could be 3.5 or a "3, 5" list). Not ambiguous when the locale uses a
    decimal comma, or when the grouping is unmistakably thousands (1,234)."""
    s = surface.replace(" ", "")
    return ("," in s and "." not in s
            and not _lang_comma_decimal(lang)
            and not _THOUSANDS.match(s))


def _to_decimal(surface: str, lang: str | None = None) -> Decimal | None:
    """Locale-aware surface -> Decimal. Resolves the decimal separator from the
    document language rather than unconditionally mapping ","->"." (which
    silently turns the EN thousands value 1,500 into 1.5)."""
    s = surface.replace(" ", "")
    if "," in s and "." in s:
        # both separators present: the rightmost is the decimal one
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # 1.234,5 (de) -> 1234.5
        else:
            s = s.replace(",", "")                       # 1,234.5 (en) -> 1234.5
    elif "," in s:
        if _lang_comma_decimal(lang):
            s = s.replace(",", ".")
        elif _THOUSANDS.match(s):
            s = s.replace(",", "")                       # 1,500 -> 1500
        else:
            s = s.replace(",", ".")                       # ambiguous: best-effort; caller flags
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        return None


def _comparator(text: str, match: re.Match) -> Comparator | None:
    """Determine the comparator from an explicit symbol, else a phrase in the
    ~24 chars before the value. None when neither is present -- never default to
    eq (the gate quarantines a comparator-less parameter)."""
    sym = match.group("sym")
    if sym:
        return _SYM_COMPARATOR.get(sym.lower())
    window = text[max(0, match.start() - 24):match.start()]
    for pat, comp in _PHRASE_COMPARATOR:
        if pat.search(window):
            return comp  # type: ignore[return-value]
    return None


def _build_tolerance(m: re.Match, unit: str | None, lang: str | None) -> Tolerance | None:
    """Symmetric ("± n"), relative ("± n %"), or asymmetric ("+a/-b") tolerance
    from the match groups (canonical-model.md tolerance types)."""
    if m.group("tolp") and m.group("tolm"):
        lo, hi = _to_decimal(m.group("tolm"), lang), _to_decimal(m.group("tolp"), lang)
        if lo is not None and hi is not None:
            return Tolerance(type="asymmetric", value=hi, value_upper=lo, unit=unit)
    if m.group("tol"):
        tv = _to_decimal(m.group("tol"), lang)
        if tv is not None:
            if m.group("tolpct"):
                return Tolerance(type="relative", value=tv, unit="%")
            return Tolerance(type="symmetric", value=tv, unit=unit)
    return None


def parse_parameters(text: str, lang: str | None = None,
                     source_object_id: str | None = None) -> list[Parameter]:
    """Every explicit value+unit limit in a prose string, as Parameters. The
    condition (frequency band) is shared across the string's parameters -- a
    clause states one band and then its limits."""
    # PDF hyphenation-break soft hyphens (U+00AD): between two digits, they stood
    # in for a literal range separator ("3\xad100 Hz" meant "3-100 Hz") -- restore
    # it as a real hyphen so _BAND/_PARAM's range groups see it. Elsewhere, drop it
    # (it's an invisible mid-word line-break artifact that offers no meaning to
    # parameters extraction). Same rationale as consensus._SOFT_HYPHEN handling.
    text = re.sub(r"(?<=\d)\xad(?=\d)", "-", text)
    text = text.replace("\xad", "")

    band_m = _BAND.search(text)
    condition = re.sub(r"\s+", " ", band_m.group(0)).strip() if band_m else None

    params: list[Parameter] = []
    for m in _PARAM.finditer(text):
        value = _to_decimal(m.group("value"), lang)
        if value is None:
            continue
        unit = _normalize_unit(m.group("unit"))
        # skip the band's own numbers being read as limits (they are the
        # condition). Use the value group's start, not the match start -- the
        # optional leading `\s*` can pull match.start() one char before the band.
        if band_m and band_m.start() <= m.start("value") < band_m.end():
            continue
        tol = _build_tolerance(m, unit, lang)
        hi = _to_decimal(m.group("hi"), lang) if m.group("hi") else None
        if m.group("lead") and hi is None:
            # A standalone leading "± N unit" with no base value before it
            # ("± 10%") -- a symmetric interval in its own right, not a bare
            # comparator-less value. Emit range-shaped like "10 - 15 unit",
            # AND populate `tolerance` so the units gate's ± symbol-survival
            # check (gates/units.py `_CRITICAL_SYMBOLS`) sees the ± accounted
            # for structurally, not just dropped.
            params.append(Parameter(
                name=_QUANTITY_KIND.get(unit or "", "value"),
                quantity_kind=_QUANTITY_KIND.get(unit or ""),
                value=None, unit=unit, raw_unit=m.group("unit"),
                comparator="range", range=(-value, value),
                tolerance=Tolerance(type="symmetric", value=value, unit=unit),
                condition=condition, source_object_id=source_object_id))
            continue
        # a range "10 - 15 unit" -> comparator=range, range=(lo, hi), value=None
        if hi is not None:
            params.append(Parameter(
                name=_QUANTITY_KIND.get(unit or "", "value"),
                quantity_kind=_QUANTITY_KIND.get(unit or ""),
                value=None, unit=unit, raw_unit=m.group("unit"),
                comparator="range", range=(value, hi), tolerance=tol,
                condition=condition, source_object_id=source_object_id))
            continue
        params.append(Parameter(
            name=(_QUANTITY_KIND.get(unit or "", "value")),
            quantity_kind=_QUANTITY_KIND.get(unit or ""),
            value=value, unit=unit, raw_unit=m.group("unit"),
            comparator=_comparator(text, m), tolerance=tol,
            condition=condition, source_object_id=source_object_id))
    return params


def _cell_parameter(cell: Cell, source_object_id: str | None,
                    lang: str | None = None) -> Parameter | None:
    """A LIMIT table cell -> Parameter, reusing canon_units' whole-cell quantity
    parse (which handles the header-unit fallback for a bare "40"). Only cells in
    a limit context with a resolved unit are promoted -- a conditions-table cell
    (no comparator, no limit-keyword header) or a unitless bare number is left as
    plain cell text, not manufactured into a comparator-less Parameter."""
    if not _is_limit_cell(cell.text, cell.header_path):
        return None
    q = cell.quantity or parse_quantity(cell.text, cell.header_path)
    if q is None or q.unit is None:
        return None
    value = _to_decimal(re.sub(r"^[<>≤≥]+", "", q.value), lang)
    if value is None:
        return None
    sym = re.match(r"\s*([<>≤≥]|<=|>=)", q.value)
    comparator = _SYM_COMPARATOR.get(sym.group(1).lower()) if sym else None
    return Parameter(
        name=_QUANTITY_KIND.get(q.unit or "", "value"),
        quantity_kind=_QUANTITY_KIND.get(q.unit or ""),
        value=value, unit=q.unit, raw_unit=q.unit,
        comparator=comparator, condition=q.condition,
        source_object_id=source_object_id, bbox=cell.bbox)


def annotate_node(node: Node) -> Node:
    """Depth-first: attach `parameters` to text bodies (from prose) and to table
    nodes (from their measurement cells). Rebuild-children-first `model_copy`,
    consistent with the other canon passes. Never overwrites parameters already
    present."""
    children = [annotate_node(c) for c in node.children]
    node = node.model_copy(update={"children": children})
    if node.parameters:
        return node

    params: list[Parameter] = []
    ambiguous_surfaces: list[str] = []
    if node.type in ("paragraph", "list_item", "note", "caption", "heading"):
        text = node.text or node.raw_text or ""
        params = parse_parameters(text, node.lang, source_object_id=node.id)
        ambiguous_surfaces = [w for w in re.findall(r"\d[\d.,]*\d", text)
                              if _decimal_ambiguous(w, node.lang)]
    elif node.type == "table" and node.cells:
        params = [p for c in node.cells
                  if (p := _cell_parameter(c, node.id, node.lang)) is not None]
        ambiguous_surfaces = [c.text for c in node.cells
                              if _decimal_ambiguous(c.text.strip(), node.lang)
                              and _is_limit_cell(c.text, c.header_path)]

    if params:
        update = {"parameters": params}
        # A limit whose decimal separator can't be resolved from the document
        # language is a review item, not a silent guess (verification-rules.md):
        # flag it so a human confirms 3,5 == 3.5 vs a list/thousands.
        if ambiguous_surfaces:
            update["review_required"] = True
            update["review_reasons"] = node.review_reasons + ["ambiguous_decimal_locale"]
        node = node.model_copy(update=update)
    return node
