"""triage.classify_page — per-page DIGITAL_CLEAN|DIGITAL_DIRTY|SCANNED|UNCERTAIN.

Measured from PyMuPDF text-layer stats, never assumed per document
(ARCHITECTURE.md §2.1): char count, garbage-char ratio (`\\ufffd`), `(cid:N)`
token count, and missing-space runs (abnormally long "words" that suggest
the text layer lost its inter-glyph spacing). `DIGITAL_CLEAN` pages skip the
GPU entirely downstream.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

import fitz  # PyMuPDF

PageClass = Literal["DIGITAL_CLEAN", "DIGITAL_DIRTY", "SCANNED", "UNCERTAIN"]

_CID_TOKEN = re.compile(r"\(cid:\d+\)")
_LONG_RUN = re.compile(r"\S{25,}")  # a "word" this long usually means missing spaces

# Thresholds (TRIAGE_THRESHOLDS in AGENTS.md §6) — tuned here for a first pass;
# revisit against a gold set once real scanned/dirty documents are available.
MIN_CHARS_FOR_DIGITAL = 40
MAX_GARBAGE_RATIO = 0.02
MAX_CID_RATIO = 0.02
MAX_LONG_RUN_RATIO = 0.05


@dataclass(frozen=True)
class TriageResult:
    page_class: PageClass
    char_count: int
    garbage_ratio: float
    cid_ratio: float
    long_run_ratio: float


def _stats(text: str) -> tuple[int, float, float, float]:
    char_count = len(text)
    if char_count == 0:
        return 0, 0.0, 0.0, 0.0
    garbage_ratio = text.count("�") / char_count
    words = text.split()
    cid_hits = len(_CID_TOKEN.findall(text))
    cid_ratio = cid_hits / max(len(words), 1)
    long_runs = sum(1 for w in words if _LONG_RUN.match(w))
    long_run_ratio = long_runs / max(len(words), 1)
    return char_count, garbage_ratio, cid_ratio, long_run_ratio


def classify_page(page: "fitz.Page") -> TriageResult:
    text = page.get_text("text")
    char_count, garbage_ratio, cid_ratio, long_run_ratio = _stats(text)

    if char_count < MIN_CHARS_FOR_DIGITAL:
        # No usable text layer -> likely a scanned/image page.
        page_class: PageClass = "SCANNED" if _has_images(page) else "UNCERTAIN"
    elif (
        garbage_ratio <= MAX_GARBAGE_RATIO
        and cid_ratio <= MAX_CID_RATIO
        and long_run_ratio <= MAX_LONG_RUN_RATIO
    ):
        page_class = "DIGITAL_CLEAN"
    else:
        page_class = "DIGITAL_DIRTY"

    return TriageResult(
        page_class=page_class,
        char_count=char_count,
        garbage_ratio=garbage_ratio,
        cid_ratio=cid_ratio,
        long_run_ratio=long_run_ratio,
    )


def _has_images(page: "fitz.Page") -> bool:
    return len(page.get_images(full=True)) > 0


def classify_document(doc: "fitz.Document") -> list[TriageResult]:
    return [classify_page(doc[i]) for i in range(doc.page_count)]
