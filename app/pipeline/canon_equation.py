"""canon.equation -- deterministic LaTeX canonicalization (SKILLS.md).

Two equations that render identically should compare identically downstream
(ARCHITECTURE.md §2.1: "equal-rendering => equal-comparing"). This normalizes
the surface LaTeX Docling's formula enrichment emits so trivial formatting
differences (spacing macros, `\\left`/`\\right` sizing, redundant braces,
whitespace) don't look like content changes to `comparison-engine`.

Chemistry: equations that look like chemical formulas/reactions (element
symbols, state annotations, reaction arrows) are wrapped/normalized as mhchem
`\\ce{...}` when not already, so they canonicalize on the same footing as math
rather than through a separate parser -- a lightweight first pass, not a full
chemistry model (deferred, see plan).

Deterministic and offline: pure string transforms, no model, same input =>
same output every time (AGENTS.md §1.8).
"""

from __future__ import annotations

import re

from canonical_schema import Node

# Spacing / sizing macros that change rendering by a hair but never meaning.
# Symbol macros (\, \; \: \!) can't use \b (the char after them is non-word),
# so they're matched separately from the word macros (\quad etc.).
_SPACING_SYMBOLS = re.compile(r"\\[,;:!]")
_SPACING_WORDS = re.compile(r"\\(?:quad|qquad|thinspace|medspace|thickspace)\b")
_LEFT_RIGHT = re.compile(r"\\(?:left|right|bigl|bigr|Bigl|Bigr|biggl|biggr)\b")
_WS = re.compile(r"[ \t\n]+")
_BRACE_WS = re.compile(r"\{\s+|\s+\}")

# Chemistry detection: reaction arrows, or a run of element-symbol+count tokens
# with a state annotation like (aq)/(s)/(g)/(l). Intentionally conservative --
# a false negative just leaves it as plain LaTeX (safe), a false positive would
# wrap prose in \ce{} (avoided by requiring strong chemical cues).
_CE_ALREADY = re.compile(r"\\ce\s*\{")
_REACTION_ARROW = re.compile(r"(->|<-|<=>|\\rightarrow|\\leftrightarrow|\\rightleftharpoons)")
_STATE_ANNOT = re.compile(r"\((?:aq|s|g|l)\)")
_ELEMENT_TOKEN = re.compile(r"[A-Z][a-z]?\d*")


def _looks_chemical(latex: str) -> bool:
    if _CE_ALREADY.search(latex):
        return False  # already mhchem
    if _STATE_ANNOT.search(latex):
        return True
    # A reaction arrow plus at least two element-symbol tokens (e.g. "2H2 +
    # O2 -> 2H2O") is a strong chemistry cue; plain math with an arrow (e.g.
    # "x -> 0") won't have two element tokens.
    if _REACTION_ARROW.search(latex) and len(_ELEMENT_TOKEN.findall(latex)) >= 2:
        return True
    return False


def canonicalize_latex(latex: str) -> str:
    """Normalize a LaTeX string to a comparison-stable form. Idempotent."""
    s = latex.strip()
    # strip inline/display math delimiters if present
    s = re.sub(r"^\$+|\$+$", "", s).strip()
    s = re.sub(r"^\\\[|\\\]$", "", s).strip()
    if _looks_chemical(s):
        s = f"\\ce{{{s}}}"
    s = _LEFT_RIGHT.sub("", s)
    s = _SPACING_SYMBOLS.sub(" ", s)
    s = _SPACING_WORDS.sub(" ", s)
    s = _BRACE_WS.sub(lambda m: "{" if "{" in m.group(0) else "}", s)
    s = _WS.sub(" ", s).strip()
    return s


_LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+")
# a math variable: a single latin/greek-macro-free letter that is not part of a
# longer word (so "V/m" gives V and m, but the "text" of \text is dropped with
# the command). Deliberately light -- a full parse needs MinerU; this is an
# inventory for search and the equation object's own metadata.
_VARIABLE = re.compile(r"(?<![A-Za-z])[A-Za-z](?![A-Za-z])")


def extract_defines(latex: str) -> str | None:
    """The symbol an equation defines: the LHS of its top-level `=`
    (`N_{\\text{d}} = ...` -> `N_{\\text{d}}`). None for a relation with no
    single definiendum."""
    if "=" not in latex:
        return None
    lhs = re.sub(r"\s+", " ", latex.split("=", 1)[0]).strip()
    return lhs or None


# Comparison-only folding for cross-engine LaTeX agreement. Two formula
# recognizers transcribe the same glyphs with different-but-equivalent commands:
# `\text{d}` vs `\mathrm{d}` (both upright roman), `$$` display wrappers,
# `\tag{2}` equation numbers, spaced subscript letters ("h i g h"). Folding
# those for COMPARISON (never in the stored candidates) keeps agreement honest;
# `\mathfrak` etc. are deliberately NOT folded -- a fraktur-vs-roman glyph is a
# genuine visual difference a human should adjudicate (measured on the DIN
# corpus: it is exactly the one real disagreement between Docling CodeFormula
# and GLM-OCR).
_EQ_FOLD_CMDS = re.compile(r"\\(?:text|mathrm|mathit|operatorname)\s*\{([^{}]*)\}")
_EQ_TAG = re.compile(r"\\tag\s*\{[^{}]*\}")
_EQ_TRAILING_BREAK = re.compile(r"(?:\\\\|\s)+$")


def eq_compare_form(latex: str) -> str:
    """The equation-lane comparison normal form (comparison only -- stored
    LaTeX candidates keep their original commands)."""
    s = canonicalize_latex(latex)
    s = _EQ_TAG.sub("", s)
    s = _EQ_FOLD_CMDS.sub(lambda m: m.group(1).replace(" ", ""), s)
    s = s.replace("\\ ", " ")
    s = _EQ_TRAILING_BREAK.sub("", s)
    return re.sub(r"\s+", "", s)


def latex_to_mathml(latex: str) -> str | None:
    """The additive, renderable form of the equation (canonical-model.md
    equation fields): MathML renders natively in modern browsers, so compliance
    evidence can SHOW the formula as it appears in the source instead of a
    LaTeX string. Best-effort and failure-tolerant -- LaTeX stays the source of
    truth; an unconvertible string leaves mathml None, never crashes ingestion.
    Deterministic, offline (latex2mathml is pure Python, MIT)."""
    try:
        import latex2mathml.converter as _conv
        return _conv.convert(latex)
    except Exception:
        return None


def extract_symbol_table(latex: str) -> dict:
    """A best-effort inventory of the variable symbols in the LaTeX, as
    `{sym: {}}` (quantity_kind/unit are unknown from LaTeX alone). NOTE: because
    these are derived FROM the LaTeX, the equation gate's symbol_table<->latex
    cross-check (a symbol defined-but-absent means a cropped region) only becomes
    a real cropping guard when symbol_table comes from an INDEPENDENT source
    (MinerU); here it is metadata / a search inventory, not a self-check."""
    no_cmd = _LATEX_COMMAND.sub(" ", latex)
    return {s: {} for s in sorted(set(_VARIABLE.findall(no_cmd)))}


def _defines_surface(defines: str) -> str | None:
    """The prose surface of a defined symbol: LaTeX decorations stripped,
    `N _ {\\text {d}}` -> "Nd". Returns None for a single bare letter -- matching
    "T" or "a" against prose is noise, and computes_limit must stay
    conservative (a false dependency edge is worse than a missing one)."""
    s = _LATEX_COMMAND.sub("", defines)
    s = re.sub(r"[{}_^\\\s]+", "", s)
    return s if len(s) >= 2 and s.isalnum() else None


def annotate_computes_limit(root: Node) -> Node:
    """Set `computes_limit=True` on an equation whose defined symbol appears in
    a NORMATIVE node (Requirement / Parameter-bearing) within the same section
    subtree (verification-rules.md: "does a Requirement's parameter depend on
    this equation"). Deterministic and deliberately conservative: same-subtree
    only, multi-character symbol surfaces only, no cross-section inference -- a
    manufactured dependency edge would misclassify an equation edit as an
    acceptance-criteria change. Runs post-assembly (needs the nested tree)."""
    from canonical_schema import is_normative

    def visit(section: Node) -> Node:
        children = [visit(c) for c in section.children]
        section = section.model_copy(update={"children": children})
        equations = [c for c in children if c.type == "equation" and c.defines]
        if not equations:
            return section
        normative_text = " ".join(
            (n.raw_text or n.text or "")
            for n in children if n.type != "equation" and is_normative(n))
        if not normative_text:
            return section
        new_children = []
        for c in children:
            if c.type == "equation" and c.defines and not c.computes_limit:
                surface = _defines_surface(c.defines)
                if surface and re.search(
                        r"(?<![A-Za-z])" + r"[\s_]?".join(map(re.escape, surface)) + r"(?![A-Za-z])",
                        normative_text):
                    c = c.model_copy(update={"computes_limit": True})
            new_children.append(c)
        return section.model_copy(update={"children": new_children})

    return visit(root)


def canonicalize_node(node: Node) -> Node:
    """Depth-first: canonicalize `latex` (and mirror into `text`) on every
    equation node, and enrich it with `defines` + a `symbol_table` inventory and
    its producing engine in `parsers` (so equation consensus activates when a
    MinerU lane is registered). Docling's CodeFormula LaTeX is kept as-is -- it
    is valid structured LaTeX, not the flattened text the reference's
    'never accept Docling' note assumed. Same rebuild-children-first pattern."""
    children = [canonicalize_node(c) for c in node.children]
    node = node.model_copy(update={"children": children})
    if node.type == "equation" and node.latex:
        canon = canonicalize_latex(node.latex)
        return node.model_copy(update={
            "latex": canon, "text": canon,
            "mathml": node.mathml or latex_to_mathml(canon),
            "defines": node.defines or extract_defines(canon),
            "symbol_table": node.symbol_table or extract_symbol_table(canon),
            "parsers": {**node.parsers, "docling_formula": canon},
        })
    return node
