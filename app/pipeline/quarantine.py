"""input.quarantine — reject encrypted/XFA/malformed PDFs before triage.

Contract (SKILLS.md): PDF bytes -> OK | QUARANTINED(cause). Deterministic,
no model involved. Runs first so nothing downstream has to handle a PDF
that can't be safely opened.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF


@dataclass(frozen=True)
class QuarantineResult:
    ok: bool
    cause: str | None = None  # e.g. "encrypted", "malformed", "xfa_form"


def check(pdf_bytes: bytes) -> QuarantineResult:
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises on malformed/corrupt input
        return QuarantineResult(ok=False, cause=f"malformed: {exc}")

    try:
        if doc.is_encrypted:
            return QuarantineResult(ok=False, cause="encrypted")
        if doc.is_form_pdf and hasattr(doc, "get_xfa") and doc.get_xfa():
            return QuarantineResult(ok=False, cause="xfa_form")
        if doc.page_count == 0:
            return QuarantineResult(ok=False, cause="empty")
        return QuarantineResult(ok=True)
    finally:
        doc.close()
