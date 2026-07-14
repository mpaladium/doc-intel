"""Factual-accuracy primitives: compare an extracted CanonicalEdition against
its source PDF's own text layer (ground truth for born-digital pages), per
page and per component. Pure-ish helpers (PyMuPDF in, plain dataclasses out)
so they're unit-testable; the CLI wrapper is `app/cli/accuracy_check.py`.

The hard part of a faithful comparison is not "did we get the text" but "is a
non-match a real miss or an expected exclusion". Three exclusion classes,
each measured rather than assumed:

  * furniture   -- running headers/footers, page numbers, and side/diagonal
                   watermarks. Detected by position (top/bottom band),
                   ROTATION (body text is always horizontal; the Beuth
                   licensing watermark on these DIN standards runs vertically
                   up the left margin), and repetition across pages.
  * front-matter-- title page / TOC cover text, on sections the pipeline
                   flagged compliance_relevant=False.
  * fragment    -- a source *physical line* can be a hyphenation/wrap fragment
                   ("ische" from "spezif-\nische"); extraction correctly
                   rejoins it. Token-subset coverage over the whole page
                   (not per-line-exact) makes these non-misses by construction.

What remains after removing those is the GENUINE miss set -- the real signal.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# fitz (PyMuPDF) is imported lazily by the CLI; these helpers take already
# extracted line dicts so they can be tested without a PDF.

_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)  # content words, >=3 letters
_TOKEN = re.compile(r"\S+")


def norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def content_words(text: str) -> set[str]:
    return set(_WORD.findall(norm(text)))


def numeric_tokens(text: str) -> set[str]:
    """Tokens containing a digit -- the compliance-critical values (limits,
    frequencies, tolerances). Kept whole (e.g. '0,0144', '30-230')."""
    return {t for t in _TOKEN.findall(norm(text)) if any(c.isdigit() for c in t)}


@dataclass
class SourceLine:
    text: str
    y0: float
    y1: float
    x0: float
    horizontal: bool  # False => rotated (watermark/side text)


def is_furniture(line: SourceLine, page_height: float,
                 repeated_texts: set[str], band_frac: float = 0.07) -> bool:
    if not line.horizontal:
        return True  # rotated text is never body content
    top = page_height * band_frac
    bottom = page_height * (1 - band_frac)
    y_center = (line.y0 + line.y1) / 2
    if y_center <= top or y_center >= bottom:
        return True
    if norm(line.text) in repeated_texts:
        return True
    return False


def find_repeated_lines(pages_lines: list[list[SourceLine]], min_pages: int) -> set[str]:
    """Normalized line texts that appear (in a header/footer band) on at least
    `min_pages` pages -- running headers/footers vary the page number but the
    rest repeats, so we match on the whole normalized line."""
    from collections import Counter
    counts: Counter[str] = Counter()
    for lines in pages_lines:
        seen = {norm(l.text) for l in lines if norm(l.text)}
        counts.update(seen)
    return {t for t, c in counts.items() if c >= min_pages and t}


def token_coverage(source_tokens: set[str], extracted_tokens: set[str]) -> tuple[float, set[str]]:
    if not source_tokens:
        return 1.0, set()
    missed = source_tokens - extracted_tokens
    return 1 - len(missed) / len(source_tokens), missed


def kendall_tau(order: list[int]) -> float:
    """Concordance of a permutation vs the identity (source y-order) -- +1 is
    perfect reading order, -1 fully reversed. O(n^2), fine for a page."""
    n = len(order)
    if n < 2:
        return 1.0
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if order[i] < order[j]:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 1.0


def reading_order_tau(tree_order_y: list[float]) -> float:
    """Kendall tau of node tree(reading) order vs true page reading order.

    Docling uses a BOTTOM-LEFT origin, so "higher on the page" == LARGER y and
    reading order is DESCENDING y. Ranks each node by its position in that
    descending-y order (stable on ties), then compares to tree order: +1 means
    the extracted reading order matches top-to-bottom on the page."""
    if len(tree_order_y) < 2:
        return 1.0
    reading = sorted(range(len(tree_order_y)), key=lambda i: -tree_order_y[i])
    rank_of = {idx: pos for pos, idx in enumerate(reading)}
    return kendall_tau([rank_of[i] for i in range(len(tree_order_y))])


@dataclass
class PageAccuracy:
    page: int
    source_content_words: int = 0
    coverage: float = 1.0
    genuine_misses: list[str] = field(default_factory=list)
    numeric_total: int = 0
    numeric_found: int = 0
    reading_order_tau: float = 1.0
    furniture_lines: int = 0


@dataclass
class DocAccuracy:
    filename: str
    pages: int = 0
    mean_coverage: float = 1.0
    min_coverage: float = 1.0
    numeric_fidelity: float = 1.0
    mean_reading_order_tau: float = 1.0
    total_genuine_misses: int = 0
    worst_pages: list[dict] = field(default_factory=list)
    per_page: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)
