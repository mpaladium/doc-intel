"""Verification gates (app/pipeline/gates) -- each gate's pass / repair /
quarantine paths, and the run_all ordering (verification-rules.md). Nodes are
built directly, no PDF needed; these are the deterministic admission checks."""

from decimal import Decimal

import pytest

import canonical_schema as cs
from app.pipeline import gates


def _prov(page=1, bbox=(0, 0, 10, 10)):
    return cs.Provenance(page=page, bbox=bbox, parser="pymupdf",
                         model_version="v1", confidence=0.9)


def _node(**kw):
    kw.setdefault("id", "n")
    kw.setdefault("type", "paragraph")
    kw.setdefault("provenance", _prov())
    return cs.Node(**kw)


def _root(children):
    return cs.Node(id="root", type="section", provenance=_prov(), children=children)


# --- gate 2: run integrity ----------------------------------------------------

def test_run_integrity_quarantines_flattened_superscript():
    # runs say "10⁻³ V/m" but raw_text was flattened to "10-3 V/m"
    runs = [cs.Run(text="10", font="A", size=10),
            cs.Run(text="-3", font="A", size=6, vertical_align="superscript"),
            cs.Run(text=" V/m", font="A", size=10)]
    n = _node(id="p1", raw_text="10-3 V/m", runs=runs)
    rep = gates.run_integrity.check(_root([n]))
    q = rep.root.children[0]
    assert q.consensus == "quarantined"
    assert "run_integrity" in (q.quarantine_reason or "")


def test_run_integrity_passes_when_reconstruction_matches():
    runs = [cs.Run(text="10", font="A", size=10),
            cs.Run(text="-3", font="A", size=6, vertical_align="superscript"),
            cs.Run(text=" V/m", font="A", size=10)]
    n = _node(id="p1", raw_text="10⁻³ V/m", runs=runs)
    rep = gates.run_integrity.check(_root([n]))
    assert rep.root.children[0].consensus == "unanimous"
    assert rep.ok


def test_run_integrity_quarantines_digits_without_runs():
    n = _node(id="p1", raw_text="limit is 40 dB", runs=[])
    rep = gates.run_integrity.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"


def test_run_integrity_ignores_prose_without_digits():
    n = _node(id="p1", raw_text="the apparatus shall be earthed", runs=[])
    rep = gates.run_integrity.check(_root([n]))
    assert rep.root.children[0].consensus == "unanimous"


# --- gate 3: numbering monotonicity -------------------------------------------

def test_numbering_quarantines_gap():
    kids = [_node(id="a", type="section", clause_id="5.2"),
            _node(id="b", type="section", clause_id="5.4")]
    rep = gates.numbering.check(_root(kids))
    late = [c for c in rep.root.children if c.id == "b"][0]
    assert late.consensus == "quarantined"
    assert "5.3" in (late.quarantine_reason or "")


def test_numbering_passes_consecutive():
    kids = [_node(id="a", type="section", clause_id="5.2"),
            _node(id="b", type="section", clause_id="5.3")]
    rep = gates.numbering.check(_root(kids))
    assert rep.ok


def test_numbering_ignores_annexes():
    kids = [_node(id="a", type="section", clause_id="Annex A"),
            _node(id="b", type="section", clause_id="Annex C")]
    rep = gates.numbering.check(_root(kids))
    assert rep.ok  # lettered annexes are legitimately non-monotonic


def test_numbering_ignores_present_but_misnested_clause():
    # 3.24 exists (buried as a child of 3.23, as Docling nests definition
    # paragraphs), so 3.23 -> 3.27 is NOT a drop of 3.24. Only 3.25/3.26, if
    # absent from the whole document, are real gaps.
    c323 = _node(id="a", type="section", clause_id="3.23",
                 children=[_node(id="buried", type="paragraph", clause_id="3.24")])
    c326 = _node(id="c326", type="paragraph", clause_id="3.26")  # also present, elsewhere
    c327 = _node(id="b", type="section", clause_id="3.27", children=[c326])
    rep = gates.numbering.check(_root([c323, c327]))
    # 3.24 and 3.26 are present -> only 3.25 is a genuine drop
    q = [o for o in rep.quarantined if o.gate == "numbering"]
    assert len(q) == 1
    assert "3.25" in q[0].reason
    assert "3.24" not in q[0].reason and "3.26" not in q[0].reason


def test_numbering_all_present_no_quarantine():
    # gap fully filled by mis-nested-but-present clauses -> no drop at all
    c1 = _node(id="a", type="section", clause_id="5.1",
               children=[_node(id="x", type="paragraph", clause_id="5.2")])
    c3 = _node(id="b", type="section", clause_id="5.3")
    rep = gates.numbering.check(_root([c1, c3]))
    assert rep.ok  # 5.2 present (buried) -> 5.1 -> 5.3 is not a drop


# --- gate 4: table rectangularity ---------------------------------------------

def _cell(r, c, text="x", rowspan=1, colspan=1, is_column_header=False):
    return cs.Cell(row=r, col=c, text=text, rowspan=rowspan, colspan=colspan,
                   is_column_header=is_column_header)


def test_table_rectangularity_passes_full_grid():
    cells = [_cell(r, c) for r in range(2) for c in range(2)]
    n = _node(id="t", type="table", cells=cells)
    rep = gates.table_rectangularity.check(_root([n]))
    assert rep.ok


def test_table_rectangularity_quarantines_hole():
    cells = [_cell(0, 0), _cell(0, 1), _cell(1, 0)]  # missing (1,1)
    n = _node(id="t", type="table", cells=cells)
    rep = gates.table_rectangularity.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"


def test_table_rectangularity_passes_valid_span():
    # a 2x2 grid where row 0 is one cell spanning both columns
    cells = [_cell(0, 0, colspan=2), _cell(1, 0), _cell(1, 1)]
    n = _node(id="t", type="table", cells=cells)
    rep = gates.table_rectangularity.check(_root([n]))
    assert rep.ok


def test_table_rectangularity_quarantines_empty_limit_column_cell():
    # rectangular grid, but a limit column ("Grenzwert") has a blank data cell
    cells = [
        cs.Cell(row=0, col=0, text="Prüfung", is_column_header=True),
        cs.Cell(row=0, col=1, text="Grenzwert dBµV/m", is_column_header=True),
        cs.Cell(row=1, col=0, text="RE", header_path=["Prüfung"]),
        cs.Cell(row=1, col=1, text="", header_path=["Grenzwert dBµV/m"]),  # blank limit
    ]
    n = _node(id="t", type="table", cells=cells)
    rep = gates.table_rectangularity.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"
    assert "limit column" in (rep.root.children[0].quarantine_reason or "")


def test_table_rectangularity_ok_when_nonlimit_column_blank():
    # a blank in a non-limit column ("Notes") is fine
    cells = [
        cs.Cell(row=0, col=0, text="Grenzwert", is_column_header=True),
        cs.Cell(row=0, col=1, text="Notes", is_column_header=True),
        cs.Cell(row=1, col=0, text="40", header_path=["Grenzwert"]),
        cs.Cell(row=1, col=1, text="", header_path=["Notes"]),
    ]
    n = _node(id="t", type="table", cells=cells)
    rep = gates.table_rectangularity.check(_root([n]))
    assert rep.ok


# --- gate 5: modal verb preservation ------------------------------------------

def test_modal_verbs_quarantine_shall_to_should():
    n = _node(id="p", raw_text="the unit shall comply",
              normalized_text="the unit should comply", lang="en")
    rep = gates.modal_verbs.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"


def test_modal_verbs_pass_when_preserved():
    n = _node(id="p", raw_text="the unit shall comply",
              normalized_text="the unit shall comply", lang="en")
    rep = gates.modal_verbs.check(_root([n]))
    assert rep.ok


def test_modal_verbs_french_convient_not_confused_with_must():
    # il convient de == should; must not be flagged against a doit(=must) form
    n = _node(id="p", raw_text="il convient de vérifier",
              normalized_text="il convient de vérifier", lang="fr")
    rep = gates.modal_verbs.check(_root([n]))
    assert rep.ok


# --- gate 6: unit and tolerance integrity -------------------------------------

def test_units_quarantine_missing_comparator():
    p = cs.Parameter(name="E", value=Decimal("10"), unit="V/m")  # no comparator
    n = _node(id="p", parameters=[p])
    rep = gates.units.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"
    assert "comparator" in (rep.root.children[0].quarantine_reason or "")


def test_units_pass_complete_parameter():
    p = cs.Parameter(name="E", value=Decimal("10"), unit="V/m", comparator="gte")
    n = _node(id="p", parameters=[p])
    rep = gates.units.check(_root([n]))
    assert rep.ok


def test_units_quarantine_dropped_plusminus():
    runs = [cs.Run(text="10 ± 0.5 V/m", font="A", size=10)]
    p = cs.Parameter(name="E", value=Decimal("10"), unit="V/m", comparator="eq")  # no tolerance
    n = _node(id="p", runs=runs, parameters=[p])
    rep = gates.units.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"
    assert "±" in (rep.root.children[0].quarantine_reason or "")


def test_units_passes_leading_tolerance_range_parameter():
    # parameters.py's fix for a standalone leading "± 10%" emits
    # comparator="range" + tolerance populated -- confirm the units gate
    # accepts this shape and does NOT flag a dropped "±" (it's structurally
    # encoded via `tolerance`, not lost).
    runs = [cs.Run(text="± 10%", font="A", size=10)]
    p = cs.Parameter(name="ratio", unit="%", comparator="range",
                      range=(Decimal("-10"), Decimal("10")),
                      tolerance=cs.Tolerance(type="symmetric", value=Decimal("10"), unit="%"))
    n = _node(id="p", runs=runs, parameters=[p])
    rep = gates.units.check(_root([n]))
    assert rep.ok


# --- gate 7: equation integrity -----------------------------------------------

def test_equation_quarantine_without_latex():
    n = _node(id="e", type="equation", rendered_text="I = V R")
    rep = gates.equations.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"


def test_equation_quarantine_unbalanced_latex():
    n = _node(id="e", type="equation", latex="\\frac{V}{R")
    rep = gates.equations.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"


def test_equation_pass_balanced_latex():
    n = _node(id="e", type="equation", latex="I = \\frac{V}{R}")
    rep = gates.equations.check(_root([n]))
    assert rep.ok


# --- gate 1: header/footer suppression ----------------------------------------

def test_header_footer_repairs_injected_running_header():
    # "ACME STD 42" recurs as its own node on pages 1-3 (the running header),
    # and is also injected mid-body into a paragraph on page 4.
    hdrs = [_node(id=f"h{p}", type="paragraph", text="ACME STD 42", provenance=_prov(page=p))
            for p in (1, 2, 3)]
    body = _node(id="body", type="paragraph",
                 text="the apparatus shall be earthed\nACME STD 42\nand bonded",
                 provenance=_prov(page=4))
    rep = gates.header_footer.check(_root(hdrs + [body]))
    fixed = [c for c in rep.root.children if c.id == "body"][0]
    assert "ACME STD 42" not in fixed.text
    assert "shall be earthed" in fixed.text
    assert fixed.repairs and fixed.repairs[0]["gate"] == "header_footer"


# --- gate 4b: continuation stitching ------------------------------------------

def _table(id, header, page=1):
    """A 2-row table: a flagged header row + one data row."""
    cells = [_cell(0, c, text=h, is_column_header=True) for c, h in enumerate(header)]
    cells += [_cell(1, c, text=f"d{c}") for c in range(len(header))]
    return _node(id=id, type="table", cells=cells, provenance=_prov(page=page))


def test_continuation_links_identical_header_after_casing_whitespace():
    # headers differ only by casing + spacing -> normalized-identical -> linked,
    # not quarantined (the gate must not be stricter than the stitcher)
    a = _table("a", ["Level", "Frequency  MHz"], page=1)
    b = _table("b", ["level", "Frequency MHz"], page=2)
    rep = gates.continuation.check(_root([a, b]))
    linked = {c.id: c for c in rep.root.children}
    assert linked["a"].continues_to == "b"
    assert linked["b"].continues_from == "a"
    assert not rep.quarantined


def test_continuation_does_not_quarantine_partial_match_on_row0_fallback():
    # two fragments Docling split on one page, NO flagged headers -- the row-0
    # fallback data cells partially match ("up to 22.5°" vs "22.5°") but this is
    # not evidence of a broken continuation, so no quarantine
    a = _node(id="a", type="table", provenance=_prov(page=8), cells=[
        cs.Cell(row=0, col=0, text="Level"), cs.Cell(row=0, col=1, text="up to 22.5° in each direction")])
    b = _node(id="b", type="table", provenance=_prov(page=8), cells=[
        cs.Cell(row=0, col=0, text="Level"), cs.Cell(row=0, col=1, text="22.5° in each direction")])
    rep = gates.continuation.check(_root([a, b]))
    assert not rep.quarantined


def test_continuation_quarantines_partial_match_on_real_headers():
    # genuine flagged headers that partially match -> ambiguous -> quarantine both
    a = _table("a", ["Level", "Class A"], page=1)
    b = _table("b", ["Level", "Class B"], page=2)
    rep = gates.continuation.check(_root([a, b]))
    assert len(rep.quarantined) == 2


# --- gate 8: cross-reference resolution ---------------------------------------

def test_crossref_quarantines_dangling_internal_ref():
    n = _node(id="p", type="paragraph", text="see 5.4",
              xrefs=[cs.XRef(kind="clause", text="5.4")])  # 5.4 not in doc
    rep = gates.cross_reference.check(_root([n]))
    assert rep.root.children[0].consensus == "quarantined"


def test_crossref_resolves_present_clause():
    target = _node(id="t", type="section", clause_id="5.4")
    ref = _node(id="p", type="paragraph", text="see 5.4",
                xrefs=[cs.XRef(kind="clause", text="5.4", target_clause_id="5.4")])
    rep = gates.cross_reference.check(_root([target, ref]))
    assert rep.ok


def test_crossref_resolves_table_ref_against_caption():
    cap = _node(id="c", type="caption", text="Tabelle 46 - Mobilfunkprüfung")
    ref = _node(id="p", type="paragraph", text="siehe Tabelle 46",
                xrefs=[cs.XRef(kind="table", text="Table 46")])
    rep = gates.cross_reference.check(_root([cap, ref]))
    assert rep.ok  # "Table 46" xref resolves to the "Tabelle 46" caption


def test_crossref_resolves_figure_ref_against_german_caption():
    cap = _node(id="c", type="caption", text="Bild 1 - Grenzabweichungen")
    ref = _node(id="p", type="paragraph", text="see Figure 1",
                xrefs=[cs.XRef(kind="figure", text="Figure 1")])
    rep = gates.cross_reference.check(_root([cap, ref]))
    assert rep.ok


def test_crossref_quarantines_in_range_dropped_table():
    # captions 11 and 13 present, "Table 12" referenced but missing -> in-range
    # drop (the parser lost Table 12)
    caps = [_node(id="c1", type="caption", text="Table 11 Upper test levels"),
            _node(id="c2", type="caption", text="Table 13 Cold test")]
    ref = _node(id="p", type="paragraph", text="see Table 12",
                xrefs=[cs.XRef(kind="table", text="Table 12")])
    rep = gates.cross_reference.check(_root(caps + [ref]))
    assert any(o.object_id == "p" for o in rep.quarantined)


def test_crossref_forward_table_ref_out_of_range_not_flagged():
    # captions up to 48; a ref to Table 49 is a forward ref (out of this slice),
    # not a drop -> not flagged (fragment-boundary robustness)
    cap = _node(id="c", type="caption", text="Tabelle 48 - Übersicht")
    ref = _node(id="p", type="paragraph", text="siehe Tabelle 49",
                xrefs=[cs.XRef(kind="table", text="Table 49")])
    rep = gates.cross_reference.check(_root([cap, ref]))
    assert rep.ok


# --- runner -------------------------------------------------------------------

def test_run_all_threads_and_accumulates():
    good = _node(id="ok", raw_text="apparatus shall be earthed",
                 normalized_text="apparatus shall be earthed", lang="en", runs=[])
    bad = _node(id="bad", type="equation", rendered_text="I = V R")
    rep = gates.run_all(_root([good, bad]))
    assert not rep.ok
    assert any(o.object_id == "bad" for o in rep.quarantined)
