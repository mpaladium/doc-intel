"""extract_pdfplumber -- the independent THIRD opinion on table geometry
(parser-consensus.md "Why tables get three parsers").

pdfplumber derives a table's grid from *ruling lines* (the drawn rules of the
table), where Docling derives it from a layout model and PyMuPDF from word
bboxes. Those are genuinely independent methods -- they fail on different
documents, which is the whole premise of N-version programming: two parsers
using the same approach agreeing tells you nothing. This module reports only
the *shape* (`n_rows`, `n_cols`, and the span map) -- the geometry consensus
compares -- not the cell text (that authority is PyMuPDF).

Coordinates: pdfplumber, like PyMuPDF `rawdict`, is TOP-LEFT origin, so its
bboxes line up with `runs.py` and the raster/accuracy path without conversion.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pdfplumber

# pdfplumber finds tables from ruling lines by default ("lines" strategy); we
# keep that -- a text/whitespace strategy would collapse the independence that
# is the entire reason pdfplumber is the third parser (it would just re-derive
# Docling's layout-model guess by another name).
_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
}


@dataclass(frozen=True)
class GridShape:
    """A table's shape as one parser sees it -- the object table consensus
    compares. `spans` is the sorted set of (row, col, rowspan, colspan) for
    cells that span more than one row/col; a merged-cell collapse changes this
    set, which is exactly the silent error the three-parser rule exists to catch."""
    n_rows: int
    n_cols: int
    spans: tuple[tuple[int, int, int, int], ...] = field(default_factory=tuple)
    bbox: tuple[float, float, float, float] | None = None  # top-left origin


def _grid_from_table(table) -> GridShape:
    """pdfplumber's `Table.rows` gives one row per horizontal band and each
    row's `cells` a bbox-or-None per column slot. A None slot is a cell merged
    into a neighbour, which is how pdfplumber expresses a span -- we recover the
    span map from the None runs rather than trusting a separate report."""
    rows = table.rows
    n_rows = len(rows)
    n_cols = max((len(r.cells) for r in rows), default=0)
    spans: list[tuple[int, int, int, int]] = []
    for ri, row in enumerate(rows):
        cells = row.cells
        ci = 0
        while ci < len(cells):
            if cells[ci] is None:
                ci += 1
                continue
            colspan = 1
            while ci + colspan < len(cells) and cells[ci + colspan] is None:
                colspan += 1
            if colspan > 1:
                spans.append((ri, ci, 1, colspan))
            ci += colspan
    return GridShape(n_rows=n_rows, n_cols=n_cols,
                     spans=tuple(sorted(spans)),
                     bbox=tuple(round(v, 2) for v in table.bbox))


def page_table_grids(pdf_bytes: bytes, page_index: int) -> list[GridShape]:
    """Every ruling-line table pdfplumber finds on one page (0-based), as
    GridShapes. Empty list == pdfplumber saw no ruled table there, which is
    itself a datum for consensus (a borderless table is exactly where the three
    parsers legitimately disagree and the table should quarantine)."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        if not (0 <= page_index < len(pdf.pages)):
            return []
        page = pdf.pages[page_index]
        return [_grid_from_table(t) for t in page.find_tables(_TABLE_SETTINGS)]
