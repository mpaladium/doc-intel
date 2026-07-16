"""consensus -- the N-version disagreement engine (parser-consensus.md).

Four parsers with explicit per-concern authority; when they disagree the
disagreement is *recorded and quarantined*, never averaged, never resolved by
picking the longest string or asking an LLM. This module is the pure,
deterministic core of that: given each parser's candidate for an object, it
returns a consensus state (`unanimous` / `majority` / `quarantined`), a reason,
and the full candidate set (quarantine keeps every candidate -- nothing is
discarded).

Two rules here are stricter than plain majority and are the point of the whole
design:
  * **Normative objects require unanimity.** A wording dissent on an
    informative note is tolerable; a dissent on a limit value is stop-the-line.
  * **Tables require all THREE geometry parsers to agree** on n_rows/n_cols/
    span-map, or the table quarantines -- a merged-cell collapse in a limit
    table is the single most expensive silent error available.

Normalization before comparison is NFKC + whitespace + hyphenation ONLY. Case,
`±`, `≤`, `°`, `µ`, digit/letter confusables and super/subscripts are NEVER
normalized away -- those ARE the content, and erasing them to force agreement
is how `10 V/m` silently becomes `1O V/m`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from app.pipeline.extract_pdfplumber import GridShape

# OCR output cannot be trusted above this no matter what the OCR engine reports
# (parser-consensus.md "Surya confidence ceiling"); a scanned limit table is the
# single highest-risk artifact in the corpus.
OCR_CONFIDENCE_CEILING = 0.95

# Parsers that produce a GENUINE per-region text transcription and therefore get
# a vote in text consensus. Docling is deliberately NOT here: its authority is
# structure/layout (parser-consensus.md), and the `node.text` it emits is
# reflow-derived (it prepends clause numbers, flattens the super/subscripts
# PyMuPDF is the sole authority for, and follows its own reading order), so
# treating it as a co-equal text transcriber manufactures disagreements that are
# artifacts, not value conflicts -- re-introducing exactly the false-quarantine
# flood the gates exist to prevent. Docling's candidate is still recorded in
# `Node.parsers` for the audit trail; it just doesn't force a quarantine. When a
# genuine alternate transcriber (MinerU for equations, Surya for OCR) populates
# `parsers`, it votes here and text consensus activates per the spec.
GENUINE_TEXT_PARSERS = frozenset({"pymupdf", "mineru", "surya"})

_WS_RUN = re.compile(r"\s+")
# line-break hyphenation: "over-\nload" -> "overload". Only join when a hyphen
# is immediately followed by a newline (a real hyphenated compound like
# "class-A" has no newline and is left intact).
_HYPHEN_BREAK = re.compile(r"-\n")
_SOFT_HYPHEN = "­"
# en-dash / em-dash / horizontal bar -> hyphen-minus for COMPARISON only. These
# are typographic variants of the same separator ("80–1000 MHz" vs "80-1000
# MHz") and folding them prevents a parser's dash transcription from reading as
# a content disagreement. The mathematical MINUS SIGN (U+2212) is deliberately
# NOT folded -- it carries value meaning ("10−3") and stays distinct.
_DASH_FOLD = {ord("–"): "-", ord("—"): "-", ord("―"): "-"}

# NFKC decomposes super/subscript codepoints back to plain digits (⁻³ -> -3) --
# exactly the flattening parser-consensus.md forbids. So before NFKC we swap
# every super/subscript codepoint for a private-use placeholder and restore it
# after: the rest of NFKC's benefits (full-width, ligatures) apply, this one
# lossy step doesn't.
_SUB_SUPER_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎"
_PROTECT = {c: chr(0xE000 + i) for i, c in enumerate(_SUB_SUPER_CHARS)}
_UNPROTECT = {v: k for k, v in _PROTECT.items()}
_PROTECT_TABLE = str.maketrans(_PROTECT)
_UNPROTECT_TABLE = str.maketrans(_UNPROTECT)


def normalize_for_compare(text: str) -> str:
    """The ONLY normalization allowed before comparing two parsers' text
    (parser-consensus.md order): NFKC, join line-break hyphenation, drop soft
    hyphens, collapse whitespace, strip. Deliberately does NOT touch case, `±`,
    `≤`, confusables, or super/subscript -- if a normalization makes two parsers
    agree, it probably erased a real difference."""
    t = text.translate(_PROTECT_TABLE)
    t = unicodedata.normalize("NFKC", t)
    t = t.translate(_UNPROTECT_TABLE)
    t = _HYPHEN_BREAK.sub("", t)
    t = t.replace(_SOFT_HYPHEN, "")
    t = t.translate(_DASH_FOLD)
    t = _WS_RUN.sub(" ", t)
    return t.strip()


@dataclass(frozen=True)
class ConsensusResult:
    state: str  # "unanimous" | "majority" | "quarantined" (ConsensusState)
    candidates: dict[str, str]           # parser -> its raw candidate (all kept)
    reason: str | None = None            # set when majority (dissent) or quarantined
    dissenters: tuple[str, ...] = field(default_factory=tuple)


def reconcile_text(candidates: dict[str, str | None], authority: str,
                   normative: bool) -> ConsensusResult:
    """The exact disagreement branch from parser-consensus.md, over parser->text
    candidates (a None candidate = that parser had no opinion and is dropped
    from the vote, not counted as a dissent). `authority` names the parser that
    wins on this concern; comparison is on normalized text but the returned
    candidates keep the originals."""
    opinions = {p: t for p, t in candidates.items() if t is not None}
    if authority not in opinions:
        # The authority parser produced nothing where it is supposed to be the
        # source of truth -- can't average our way out of that.
        return ConsensusResult("quarantined", dict(candidates),
                               reason=f"authority '{authority}' produced no candidate")

    auth_norm = normalize_for_compare(opinions[authority])
    corroborators = {p: normalize_for_compare(t) for p, t in opinions.items() if p != authority}

    if all(v == auth_norm for v in corroborators.values()):
        return ConsensusResult("unanimous", dict(candidates))

    dissenters = tuple(sorted(p for p, v in corroborators.items() if v != auth_norm))

    # Normative objects get no majority mercy: any dissent quarantines.
    if normative:
        return ConsensusResult("quarantined", dict(candidates), dissenters=dissenters,
                               reason=f"normative object, non-unanimous: dissent from {list(dissenters)}")

    # Non-normative: authority admitted at reduced confidence iff authority +
    # the corroborators that agree with it form a strict majority of ALL parsers
    # (the authority counts toward its own block -- "authority and at least one
    # corroborator agree; a minority dissents"). Otherwise the authority is
    # isolated and we quarantine rather than trust a lone parser against the field.
    agree_block = 1 + sum(1 for v in corroborators.values() if v == auth_norm)
    if agree_block * 2 > len(opinions):
        return ConsensusResult("majority", dict(candidates), dissenters=dissenters,
                               reason=f"majority: dissent from {list(dissenters)}")
    return ConsensusResult("quarantined", dict(candidates), dissenters=dissenters,
                           reason=f"authority '{authority}' isolated: {list(dissenters)}")


def reconcile_table_grid(candidates: dict[str, GridShape]) -> ConsensusResult:
    """Tables are stricter than everything else: the grid requires ALL THREE
    geometry parsers (docling, pdfplumber, pymupdf) to agree on n_rows, n_cols
    and the span map, or the table quarantines (parser-consensus.md). Fewer than
    three opinions is itself a quarantine -- a borderless table pdfplumber can't
    see is exactly the case this rule protects."""
    present = {p: g for p, g in candidates.items() if g is not None}
    shapes = {p: (g.n_rows, g.n_cols, g.spans) for p, g in present.items()}
    cand_repr = {p: f"{g.n_rows}x{g.n_cols} spans={list(g.spans)}" for p, g in present.items()}

    if len(present) < 3:
        return ConsensusResult("quarantined", cand_repr,
                               reason=f"table geometry needs 3 opinions, got {sorted(present)}")

    if len(set(shapes.values())) == 1:
        return ConsensusResult("unanimous", cand_repr)
    # Which parsers disagree with the plurality shape (for the audit trail).
    plurality, _ = Counter(shapes.values()).most_common(1)[0]
    dissenters = tuple(sorted(p for p, s in shapes.items() if s != plurality))
    return ConsensusResult("quarantined", cand_repr, dissenters=dissenters,
                           reason=f"table geometry disagreement: {dict(cand_repr)}")


def apply_ocr_ceiling(confidence: float, is_ocr: bool) -> float:
    """OCR confidence is capped (parser-consensus.md); a parser can't claim more
    certainty about scanned text than the ceiling allows."""
    return min(confidence, OCR_CONFIDENCE_CEILING) if is_ocr else confidence
