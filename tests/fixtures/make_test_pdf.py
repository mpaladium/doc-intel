"""Generates a synthetic born-digital PDF shaped like an IEC/CISPR-style
standard: title page, a real (>=8 line) table of contents, a foreword, an
"Introduction" preface, numbered normative clauses (including a nested
sub-clause, a captioned table, and a captioned figure), and a trailing
alphabetical index. Enough to exercise triage, section-role front/back-zone
exclusion, clause_id assignment, table extraction, and caption handling in
one document -- no real standards PDFs are available in this repo yet (see
plan's deferred-work note).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image

styles = getSampleStyleSheet()
h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16, spaceAfter=10)
h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, spaceAfter=8)
body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=10, spaceAfter=6)
caption = ParagraphStyle("Caption", parent=styles["Normal"], fontName="Helvetica-Oblique",
                          fontSize=8, spaceAfter=6)


def _tiny_image_path() -> str:
    """A small PNG written to a temp file (reportlab's Image flowable wants a
    path/file-like it can re-open, not a bare ImageReader in this version) to
    stand in for a real figure -- just enough for Docling's layout model to
    have something picture-shaped to detect. Not written into the repo since
    it's regenerated on every fixture build."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 160, 100))
    pix.set_rect(pix.irect, (180, 60, 60))
    fd, path = tempfile.mkstemp(suffix=".png")
    with open(fd, "wb") as f:
        f.write(pix.tobytes("png"))
    return path


def build(path: str | Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    story = []

    # -- Title page --
    story.append(Spacer(1, 200))
    story.append(Paragraph("IEC 61000-6-3", h1))
    story.append(Paragraph("Electromagnetic compatibility (EMC) -- Test Standard", body))
    story.append(PageBreak())

    # -- Table of contents (>=8 lines, dot-leader + page number shape) --
    story.append(Paragraph("Table of Contents", h1))
    toc_entries = [
        "Foreword", "Introduction", "1 Scope", "2 Normative references",
        "3 Terms and definitions", "4 Test methods", "4.1 General",
        "4.2 Radiated emissions", "4.2.3 Limits", "5 Compliance criteria", "Index",
    ]
    for i, entry in enumerate(toc_entries, start=1):
        story.append(Paragraph(f"{entry} " + ("." * 40) + f" {i + 2}", body))
    story.append(PageBreak())

    # -- Foreword --
    story.append(Paragraph("Foreword", h1))
    story.append(Paragraph(
        "This document was prepared by Technical Committee 77 and is a standards "
        "document of an international electrotechnical body.", body))
    story.append(PageBreak())

    # -- Introduction / preface --
    story.append(Paragraph("Introduction", h1))
    story.append(Paragraph(
        "This standard establishes limits intended to provide protection against "
        "electromagnetic interference in residential environments.", body))
    story.append(PageBreak())

    # -- Normative body --
    story.append(Paragraph("1 Scope", h1))
    story.append(Paragraph(
        "This standard specifies limits for radiated emissions from equipment.", body))
    story.append(PageBreak())

    story.append(Paragraph("2 Normative references", h1))
    story.append(Paragraph("The following documents are referred to in the text.", body))
    story.append(PageBreak())

    story.append(Paragraph("3 Terms and definitions", h1))
    story.append(Paragraph("For the purposes of this document, the following terms apply.", body))
    story.append(PageBreak())

    story.append(Paragraph("4 Test methods", h1))
    story.append(Paragraph("General test method requirements are given below.", body))
    story.append(Paragraph("4.1 General", h2))
    story.append(Paragraph("Tests shall be performed as specified in this clause.", body))
    story.append(Paragraph("4.2 Radiated emissions", h2))
    story.append(Paragraph("The test setup is shown below.", body))
    story.append(Image(_tiny_image_path(), width=160, height=100))
    story.append(Paragraph("Figure 1 -- Test setup diagram", caption))
    story.append(Paragraph("4.2.3 Limits", h2))
    story.append(Paragraph("Table 1 gives the radiated emission limits.", body))

    table_data = [
        ["Frequency range (MHz)", "Limit (dBuV/m)", "Distance (m)"],
        ["30 - 230", "40", "10"],
        ["230 - 1000", "47", "10"],
        ["1000 - 3000", "50", "3"],
    ]
    tbl = Table(table_data, colWidths=[160, 120, 100])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.75, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story.append(tbl)
    story.append(Paragraph("Table 1 -- Radiated emission limits by frequency", caption))
    story.append(PageBreak())

    story.append(Paragraph("5 Compliance criteria", h1))
    story.append(Paragraph("A device under test complies if all limits in Table 1 are met.", body))
    story.append(PageBreak())

    # -- Back matter: alphabetical index --
    story.append(Paragraph("Index", h1))
    index_terms = [
        "Compliance", "Definitions", "Emissions", "Frequency",
        "General", "Limits", "Normative references", "Scope",
        "Terms", "Test methods",
    ]
    for i, term in enumerate(index_terms, start=3):
        story.append(Paragraph(f"{term} " + ("." * 30) + f" {i}", body))

    doc.build(story)


if __name__ == "__main__":
    build(Path(__file__).parent / "standard_sample.pdf")
    print("wrote", Path(__file__).parent / "standard_sample.pdf")
