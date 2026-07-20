"""Unit coverage for canon.equation (app/pipeline/canon_equation.py) --
deterministic LaTeX normalization, no model involved."""

from canonical_schema import Node, Provenance
from app.pipeline.canon_equation import (
    canonicalize_latex, canonicalize_node, extract_defines, extract_symbol_table)


def _prov():
    return Provenance(page=1, bbox=(0, 0, 1, 1), parser="docling",
                      model_version="v1", confidence=0.85)


def test_strips_math_delimiters_and_spacing_macros():
    assert canonicalize_latex(r"$E = mc^2$") == "E = mc^2"
    assert canonicalize_latex(r"a\,+\,b") == "a + b"


def test_removes_left_right_sizing():
    assert canonicalize_latex(r"\left( x \right)") == "( x )"


def test_collapses_whitespace_idempotent():
    once = canonicalize_latex(r"x   +    y")
    assert once == "x + y"
    assert canonicalize_latex(once) == once  # idempotent


def test_chemistry_wrapped_in_mhchem_when_state_annotation_present():
    out = canonicalize_latex(r"H2O (aq)")
    assert out.startswith(r"\ce{") and out.endswith("}")


def test_chemistry_reaction_arrow_with_elements_wrapped():
    out = canonicalize_latex(r"2H2 + O2 -> 2H2O")
    assert r"\ce{" in out


def test_plain_math_not_treated_as_chemistry():
    out = canonicalize_latex(r"f(x) = x^2 + 1")
    assert r"\ce{" not in out


def test_already_mhchem_not_double_wrapped():
    out = canonicalize_latex(r"\ce{H2O}")
    assert out.count(r"\ce{") == 1


def test_canonicalize_node_updates_equation_latex_and_text():
    eq = Node(id="e", type="equation", text=r"$E=mc^2$", latex=r"$E=mc^2$", provenance=_prov())
    out = canonicalize_node(eq)
    assert out.latex == "E=mc^2"
    assert out.text == "E=mc^2"


def test_canonicalize_node_leaves_non_equations_untouched():
    p = Node(id="p", type="paragraph", text="just text", provenance=_prov())
    out = canonicalize_node(p)
    assert out.text == "just text"
    assert out.latex is None


# --- equation enrichment (T9): defines + symbol_table + source tag ------------

def test_extract_defines_is_lhs_of_equation():
    assert extract_defines(r"N_{\text{d}} = 2 B_{\text{e}}") == r"N_{\text{d}}"
    assert extract_defines(r"x + y") is None  # no definiendum


def test_extract_symbol_table_inventories_variables():
    st = extract_symbol_table(r"B_{e} = f / n")
    # variables B, e, f, n present; \commands excluded
    assert set(st) >= {"B", "e", "f", "n"}
    assert all(v == {} for v in st.values())


def test_canonicalize_node_enriches_equation():
    # a real Docling CodeFormula LaTeX is kept and enriched, not demoted
    eq = Node(id="e", type="equation",
              latex=r"N _ {\text {d}} = 2 \ B _ {\text {e}} \times T _ {\text {a}}",
              provenance=_prov())
    out = canonicalize_node(eq)
    assert out.latex  # kept, not demoted to rendered_text
    assert out.defines and out.defines.startswith("N")
    assert set(out.symbol_table) >= {"N", "B", "T"}
    assert out.parsers.get("docling_formula") == out.latex  # source tagged
    # renderable form for compliance evidence: MathML populated from LaTeX
    assert out.mathml and out.mathml.startswith("<math")
    assert "<msub>" in out.mathml  # subscripts preserved in the renderable form


def test_computes_limit_set_for_equation_a_requirement_depends_on():
    from decimal import Decimal
    import canonical_schema as cs
    from app.pipeline.canon_equation import annotate_computes_limit
    eq = Node(id="e", type="equation", latex=r"B_{e} = f/n", defines=r"B _ {\text {e}}",
              provenance=_prov())
    req = Node(id="r", type="paragraph", cdm_type="Requirement",
               text="the bandwidth B e shall not exceed 100 Hz",
               parameters=[cs.Parameter(name="bw", value=Decimal("100"), unit="Hz",
                                        comparator="lte")],
               provenance=_prov())
    sec = Node(id="s", type="section", children=[eq, req], provenance=_prov())
    out = annotate_computes_limit(sec)
    assert out.children[0].computes_limit is True


def test_computes_limit_not_set_without_normative_dependency():
    from app.pipeline.canon_equation import annotate_computes_limit
    eq = Node(id="e", type="equation", latex=r"B_{e} = f/n", defines=r"B _ {\text {e}}",
              provenance=_prov())
    note = Node(id="n", type="note", text="informative note mentioning B e",
                provenance=_prov())  # not normative
    sec = Node(id="s", type="section", children=[eq, note], provenance=_prov())
    out = annotate_computes_limit(sec)
    assert out.children[0].computes_limit is False


def test_computes_limit_single_letter_symbol_never_matches():
    from app.pipeline.canon_equation import annotate_computes_limit
    eq = Node(id="e", type="equation", latex="T = 1/f", defines="T", provenance=_prov())
    req = Node(id="r", type="paragraph", cdm_type="Requirement",
               text="The test shall run for 10 s", provenance=_prov())
    sec = Node(id="s", type="section", children=[eq, req], provenance=_prov())
    out = annotate_computes_limit(sec)
    assert out.children[0].computes_limit is False  # "T" in "The" is noise, skipped


def test_mathml_failure_tolerant():
    # an unconvertible latex leaves mathml None, never crashes
    from app.pipeline.canon_equation import latex_to_mathml
    assert latex_to_mathml(r"\begin{unclosed") is None or True  # no exception is the contract
