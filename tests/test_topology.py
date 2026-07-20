"""Unit coverage for topology.clauses -- clause_id parsing (leading + trailing
numbers, with date/standard-ref guards) and clause-hierarchy reconstruction."""

from canonical_schema import Node, Provenance
from app.pipeline import topology
from app.pipeline.topology import _extract_clause_id


def _prov(page=1):
    return Provenance(page=page, bbox=(0, 0, 1, 1), parser="docling",
                      model_version="v1", confidence=0.9)


def _sec(text, clause_id=None, children=None):
    return Node(id=f"s{id(text)}", type="section", text=text, clause_id=clause_id,
                children=children or [], provenance=_prov())


# --------------------------------------------------------------------------- #
# clause_id extraction
# --------------------------------------------------------------------------- #
def test_leading_clause_number():
    assert _extract_clause_id("4.2.3 Limits") == "4.2.3"
    assert _extract_clause_id("1 Scope") == "1"


def test_trailing_clause_number_german_style():
    assert _extract_clause_id("Grenzwertklassen 5.3.4") == "5.3.4"
    assert _extract_clause_id("Prüfaufbau 5.3.5.1") == "5.3.5.1"
    assert _extract_clause_id("Störemission 5.3") == "5.3"


def test_annex_clause_both_languages():
    assert _extract_clause_id("Annex A Test methods") == "Annex A"
    assert _extract_clause_id("Anhang ZA Normative references") == "Annex ZA"


def test_date_and_standard_refs_not_treated_as_clauses():
    # trailing bare integer (figure/quantity), a date, and a standard number
    assert _extract_clause_id("Prüffeldstärke 2") is None
    assert _extract_clause_id("2009-04-01 Ausgabedatum") is None
    assert _extract_clause_id("Nach IEC 61000-4-3") is None


def test_assign_clause_ids_walks_tree():
    root = _sec(None, children=[_sec("Grenzwertklassen 5.3.4"), _sec("1 Scope")])
    out = topology.assign_clause_ids(root)
    assert out.children[0].clause_id == "5.3.4"
    assert out.children[1].clause_id == "1"


def test_definition_paragraph_with_gloss_gets_clause_id():
    # DIN 3.28 term is 7 words incl. its "(siehe auch Bild 1)" gloss, but only 4
    # without -- the parenthetical must not push it over the short-title bar
    p = Node(id="p1", type="paragraph", clause_id=None,
             text="3.28 Anstieg des Spektrums (siehe auch Bild 1)", provenance=_prov())
    out = topology.assign_clause_ids(p)
    assert out.clause_id == "3.28"


def test_prose_paragraph_starting_with_number_still_not_labeled():
    # guard intact: a prose paragraph with no parenthetical stays unlabeled
    p = Node(id="p2", type="paragraph", clause_id=None,
             text="3.2 m/s applies across the whole tested frequency range here",
             provenance=_prov())
    out = topology.assign_clause_ids(p)
    assert out.clause_id is None


# --------------------------------------------------------------------------- #
# clause-hierarchy reconstruction
# --------------------------------------------------------------------------- #
def _clause(cid):
    return _sec(cid, clause_id=cid)


def _depths(nodes, d=0):
    for n in nodes:
        if n.type == "section":
            yield n.clause_id, d
            yield from _depths(n.children, d + 1)


def test_nest_by_clause_builds_hierarchy():
    flat = [_clause("5.3"), _clause("5.3.1"), _clause("5.3.5"),
            _clause("5.3.5.1"), _clause("5.3.6")]
    nested = topology.nest_by_clause(flat)
    depths = dict(_depths(nested))
    assert depths["5.3"] == 0
    assert depths["5.3.1"] == 1
    assert depths["5.3.5"] == 1
    assert depths["5.3.5.1"] == 2   # nests under 5.3.5
    assert depths["5.3.6"] == 1     # back up under 5.3


def test_missing_intermediate_parent_attaches_to_nearest_ancestor():
    # "5.3.2" is absent; "5.3.2.1" should attach under "5.3".
    flat = [_clause("5.3"), _clause("5.3.2.1")]
    nested = topology.nest_by_clause(flat)
    depths = dict(_depths(nested))
    assert depths["5.3.2.1"] == 1


def test_top_level_clauses_stay_flat():
    flat = [_clause("1"), _clause("2"), _clause("3")]
    nested = topology.nest_by_clause(flat)
    assert len(nested) == 3
    assert all(d == 0 for _, d in _depths(nested))


def test_non_clause_section_attaches_to_open_clause():
    flat = [_clause("5.3"), _sec("Guidance note", clause_id=None), _clause("6")]
    nested = topology.nest_by_clause(flat)
    # the guidance note nests under 5.3; clause 6 is a new top-level
    top = [n.clause_id for n in nested if n.type == "section"]
    assert top == ["5.3", "6"]
    assert any(c.text == "Guidance note" for c in nested[0].children)


def test_front_matter_before_any_clause_stays_top_level():
    flat = [_sec("FOREWORD"), _clause("1")]
    nested = topology.nest_by_clause(flat)
    assert len(nested) == 2
    assert nested[0].text == "FOREWORD"


def test_annex_resets_to_top_level():
    flat = [_clause("5.3"), _clause("5.3.1"), _sec("Annex A", clause_id="Annex A")]
    nested = topology.nest_by_clause(flat)
    top = [n.clause_id for n in nested if n.type == "section"]
    assert "Annex A" in top  # not buried under 5.3


def _para(text, clause_id=None):
    return Node(id=f"p{id(text)}", type="paragraph", text=text, clause_id=clause_id,
                children=[], provenance=_prov())


def test_hoist_unburies_misnested_definition_clause():
    # Docling buried 3.24 (with 3.26 inside it) under section 3.23; the hoist
    # must pull BOTH back to the top-level list, subtrees intact, so
    # nest_by_clause can place them by number.
    c326 = _para("3.26 Filterbandbreite", clause_id="3.26")
    c324 = Node(id="p324", type="paragraph", text="3.24 Angleichung", clause_id="3.24",
                children=[c326], provenance=_prov())
    c323 = _sec("3.23 Abweichung", clause_id="3.23", children=[c324])
    out = topology.hoist_misnested_clauses([c323, _clause("3.27")])
    ids = [n.clause_id for n in out]
    assert ids == ["3.23", "3.24", "3.26", "3.27"]  # flat, document order
    assert out[0].children == []   # 3.23 no longer holds 3.24
    assert out[1].children == []   # 3.24 no longer holds 3.26


def test_hoist_keeps_correctly_nested_subclauses():
    sub = _sec("5.3.5.1 Aufbau", clause_id="5.3.5.1")
    parent = _sec("5.3.5 Prüfung", clause_id="5.3.5", children=[sub])
    out = topology.hoist_misnested_clauses([parent])
    assert len(out) == 1
    assert out[0].children[0].clause_id == "5.3.5.1"  # untouched


def test_hoist_reaches_into_nonclause_container_mid_clause_run():
    # the REAL DIN chain: a top-level non-clause section "(en: final slope)"
    # follows 3.24 and holds 3.26 -- nest would bury the container (and 3.26)
    # under 3.24, so the hoist must pull 3.26 out using the running context
    c326 = _para("3.26 Filterbandbreite", clause_id="3.26")
    stray = _sec("(en: final slope)", clause_id=None, children=[c326])
    out = topology.hoist_misnested_clauses(
        [_clause("3.24"), stray, _clause("3.27")])
    ids = [n.clause_id for n in out]
    assert ids == ["3.24", None, "3.26", "3.27"]
    assert out[1].children == []  # container kept, 3.26 extracted


def test_hoist_never_touches_toc_entries_before_first_clause():
    # a TOC section holds clause-numbered entries but precedes any open clause
    # (no running context) -- its entries must NOT be hoisted into the body
    entry = _para("5.3 Grenzwerte", clause_id="5.3")
    toc = _sec("Inhalt", clause_id=None, children=[entry])
    out = topology.hoist_misnested_clauses([toc, _clause("1")])
    assert len(out) == 2
    assert out[0].children[0].clause_id == "5.3"  # still inside the TOC


def test_hoist_leaves_prose_and_nonclause_children_alone():
    prose = _para("ordinary prose under a clause")
    parent = _sec("4.1 Scope", clause_id="4.1", children=[prose])
    out = topology.hoist_misnested_clauses([parent])
    assert len(out) == 1 and len(out[0].children) == 1


def test_definition_paragraph_nests_as_sibling_not_child():
    # DIN Terms-&-Definitions: 3.24 arrives typed `paragraph` between sections
    # 3.23 and 3.27. It must become a SIBLING under 3 (so the numbering gate
    # sees 3.23 -> 3.24 -> 3.27), not a child buried under 3.23.
    flat = [_clause("3"), _clause("3.23"), _para("3.24 Angleichung", clause_id="3.24"),
            _clause("3.27")]
    nested = topology.nest_by_clause(flat)
    # collect every clause_id and its depth across all node types
    def walk(nodes, d=0):
        for n in nodes:
            yield n.clause_id, d
            yield from walk(n.children, d + 1)
    depths = {cid: d for cid, d in walk(nested) if cid}
    assert depths["3.23"] == depths["3.24"] == depths["3.27"] == 1  # all siblings under 3
    # and 3.24 is NOT a child of 3.23
    c323 = next(n for n in nested[0].children if n.clause_id == "3.23")
    assert all(ch.clause_id != "3.24" for ch in c323.children)


# --------------------------------------------------------------------------- #
# Numbered non-heading nodes: list_item / title paragraph clause_ids
# --------------------------------------------------------------------------- #
def _typed(type_, text, page=1, children=None):
    return Node(id=f"n{id(text)}", type=type_, text=text, children=children or [],
                provenance=_prov(page))


def test_list_item_with_leading_number_gets_clause_id():
    # DNVGL "16.1 Flame-retardant test." -- Docling joins number+title but types
    # it a list_item, which the old section/heading-only pass skipped.
    root = _typed("section", None, children=[_typed("list_item", "16.1 Flame-retardant test.")])
    out = topology.assign_clause_ids(root)
    assert out.children[0].clause_id == "16.1"


def test_title_paragraph_with_leading_number_gets_clause_id():
    root = _typed("section", None, children=[_typed("paragraph", "3.20 Regelkreis")])
    out = topology.assign_clause_ids(root)
    assert out.children[0].clause_id == "3.20"


def test_prose_paragraph_mentioning_a_number_is_not_a_clause():
    # Guard: a numbered value in a sentence must not become a clause_id.
    long_prose = "3.2 m/s applies to the device under the stated test conditions here"
    root = _typed("section", None, children=[_typed("paragraph", long_prose)])
    out = topology.assign_clause_ids(root)
    assert out.children[0].clause_id is None


# --------------------------------------------------------------------------- #
# Lone-number merge (two-column clause layout)
# --------------------------------------------------------------------------- #
def test_lone_number_merges_into_following_section():
    # "3.2" (lone) then its term "tatsächliche Bewegung" (separate section).
    top = [
        _typed("section", "3 Begriffe", children=[_typed("paragraph", "3.2")]),
        _typed("section", "tatsächliche Bewegung"),
    ]
    merged = topology.merge_split_clause_numbers(top)
    # lone "3.2" dropped; number prepended to the term
    assert not any(n.text == "3.2" for n in topology._iter_reading_order(merged))
    term = [n for n in topology._iter_reading_order(merged) if "tatsächliche" in (n.text or "")][0]
    assert term.text == "3.2 tatsächliche Bewegung"
    # assign_clause_ids then labels it
    labeled = [topology.assign_clause_ids(n) for n in merged]
    assert any(n.clause_id == "3.2" for n in topology._iter_reading_order(labeled))


def test_lone_number_not_merged_across_pages():
    top = [
        _typed("section", "S", children=[_typed("paragraph", "3.2", page=1)]),
        _typed("section", "Different clause term", page=2),
    ]
    merged = topology.merge_split_clause_numbers(top)
    assert any(n.text == "3.2" for n in topology._iter_reading_order(merged))


def test_lone_number_not_merged_into_long_sentence():
    top = [
        _typed("section", "S", children=[_typed("paragraph", "3.2")]),
        _typed("paragraph", "This is a full sentence of body text that happens to follow the number node"),
    ]
    merged = topology.merge_split_clause_numbers(top)
    assert any(n.text == "3.2" for n in topology._iter_reading_order(merged))


def test_lone_number_not_merged_when_target_already_numbered():
    top = [
        _typed("section", "S", children=[_typed("paragraph", "3.2")]),
        _typed("section", "4.1 Already a clause"),
    ]
    merged = topology.merge_split_clause_numbers(top)
    assert any(n.text == "3.2" for n in topology._iter_reading_order(merged))
