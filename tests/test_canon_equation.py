"""Unit coverage for canon.equation (app/pipeline/canon_equation.py) --
deterministic LaTeX normalization, no model involved."""

from canonical_schema import Node, Provenance
from app.pipeline.canon_equation import canonicalize_latex, canonicalize_node


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
