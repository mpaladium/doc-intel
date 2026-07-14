"""Unit coverage for xref -- cross-reference detection + within-edition
resolution. Conservative: only explicit lead-word / bracketed references."""

from canonical_schema import Node, Provenance
from app.pipeline import xref


def _prov():
    return Provenance(page=1, bbox=(0, 0, 1, 1), parser="docling", model_version="v1", confidence=0.9)


def _para(text):
    return Node(id=f"p{id(text)}", type="paragraph", text=text, provenance=_prov())


def _clause(cid, text, children=None):
    return Node(id=f"s{id(text)}", type="section", clause_id=cid, text=text,
                children=children or [], provenance=_prov())


def test_resolves_clause_reference_that_exists():
    root = Node(id="r", type="section", provenance=_prov(), children=[
        _clause("4.2.3", "4.2.3 Limits"),
        _para("The device shall comply, see 4.2.3 for details."),
    ])
    out = xref.annotate_tree(root)
    ref = out.children[1].xrefs[0]
    assert ref.kind == "clause" and ref.text == "4.2.3"
    assert ref.target_clause_id == "4.2.3"  # resolved -- clause exists


def test_unresolved_clause_reference_recorded_without_target():
    root = Node(id="r", type="section", provenance=_prov(), children=[
        _para("As stated in clause 9.9.9 (not present)."),
    ])
    out = xref.annotate_tree(root)
    ref = out.children[0].xrefs[0]
    assert ref.kind == "clause" and ref.target_clause_id is None


def test_german_lead_words():
    root = Node(id="r", type="section", provenance=_prov(), children=[
        _clause("5.3.5", "Netznachbildung 5.3.5"),
        _para("Die Prüfung erfolgt siehe 5.3.5."),
    ])
    out = xref.annotate_tree(root)
    assert out.children[1].xrefs[0].target_clause_id == "5.3.5"


def test_table_and_figure_and_annex_refs():
    root = Node(id="r", type="section", provenance=_prov(), children=[
        _clause("Annex A", "Annex A Methods"),
        _para("See Table 22 and Bild 15; details in Anhang A."),
    ])
    out = xref.annotate_tree(root)
    kinds = {(x.kind, x.text): x for x in out.children[1].xrefs}
    assert ("table", "Table 22") in kinds
    assert ("figure", "Figure 15") in kinds
    assert kinds[("annex", "Annex A")].target_clause_id == "Annex A"  # resolved


def test_plain_numbers_are_not_references():
    root = Node(id="r", type="section", provenance=_prov(), children=[
        _para("The limit is 40 dBuV/m at 30-230 MHz."),
    ])
    out = xref.annotate_tree(root)
    assert out.children[0].xrefs == []


def test_duplicate_references_deduped():
    root = Node(id="r", type="section", provenance=_prov(), children=[
        _para("see 4.2 and again see 4.2."),
    ])
    out = xref.annotate_tree(root)
    assert len(out.children[0].xrefs) == 1
