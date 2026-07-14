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


def canonicalize_node(node: Node) -> Node:
    """Depth-first: canonicalize `latex` (and mirror into `text`) on every
    equation node. Same rebuild-children-first `model_copy` pattern as
    `topology.assign_clause_ids` / `continuity.stitch`."""
    children = [canonicalize_node(c) for c in node.children]
    node = node.model_copy(update={"children": children})
    if node.type == "equation" and node.latex:
        canon = canonicalize_latex(node.latex)
        return node.model_copy(update={"latex": canon, "text": canon})
    return node
