"""table_geometry -- the Docling and PyMuPDF geometry opinions that join
pdfplumber's (extract_pdfplumber.py) to make the three-parser table-grid
consensus (parser-consensus.md "Why tables get three parsers").

Three genuinely independent methods of deriving a table's grid:
  * Docling -- a layout model over the page image (`table_geometry.docling_grid`,
    read off the already-extracted `Node.cells`).
  * pdfplumber -- ruling lines (`extract_pdfplumber.page_table_grids`).
  * PyMuPDF -- word bounding boxes clustered into rows/columns
    (`pymupdf_grid` here).
They fail on *different* documents, which is the whole premise: a merged-cell
collapse that fools the layout model won't move the ruling lines, and vice
versa. The consensus (consensus.reconcile_table_grid) admits a grid only when
all three agree on `n_rows`/`n_cols`/span-map, quarantining otherwise.

All coordinates are top-left origin (PyMuPDF rawdict / pdfplumber convention);
Docling provenance bboxes are bottom-left and are bridged by
`runs.docling_bbox_to_topleft` before use.
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

from canonical_schema import Node
from app.pipeline.extract_pdfplumber import GridShape
from app.pipeline.runs import docling_bbox_to_topleft


def docling_grid(node: Node) -> GridShape | None:
    """Docling's opinion on a table's shape, read off the cells it produced.
    n_rows/n_cols are the covered extent; spans are the cells covering more than
    one row/col -- the same (row, col, rowspan, colspan) tuples the other two
    producers report, so the three are directly comparable."""
    cells = node.cells or []
    if not cells:
        return None
    n_rows = max(c.row + c.rowspan for c in cells)
    n_cols = max(c.col + c.colspan for c in cells)
    spans = tuple(sorted((c.row, c.col, c.rowspan, c.colspan)
                         for c in cells if c.rowspan > 1 or c.colspan > 1))
    return GridShape(n_rows=n_rows, n_cols=n_cols, spans=spans)


def _cluster(centers: list[float], tol: float) -> int:
    """Number of distinct clusters among 1-D centers, merging any within `tol`.
    A table row/column is a band of aligned word centers; `tol` (a fraction of
    the median line height / column width) absorbs sub-pixel jitter without
    merging genuinely separate rows/columns."""
    if not centers:
        return 0
    ordered = sorted(centers)
    clusters = 1
    last = ordered[0]
    for c in ordered[1:]:
        if c - last > tol:
            clusters += 1
        last = c
    return clusters


def pymupdf_grid(page: "fitz.Page", region_topleft: tuple[float, float, float, float],
                 pad: float = 2.0) -> GridShape | None:
    """PyMuPDF's opinion: cluster the word bounding boxes inside the table region
    into rows (by y-center) and columns (by x-center). Independent of both the
    layout model and the ruling lines -- it sees only where ink actually sits.
    Reports n_rows/n_cols; it does not attempt span detection (an empty spans
    tuple), so it corroborates the row/col count, the dimension a merged-cell
    collapse changes. None when the region holds no words."""
    rx0, ry0, rx1, ry1 = region_topleft
    words = [w for w in page.get_text("words")
             if rx0 - pad <= (w[0] + w[2]) / 2 <= rx1 + pad
             and ry0 - pad <= (w[1] + w[3]) / 2 <= ry1 + pad]
    if not words:
        return None
    heights = sorted(w[3] - w[1] for w in words)
    widths = sorted(w[2] - w[0] for w in words)
    line_h = heights[len(heights) // 2] or 1.0
    char_w = widths[len(widths) // 2] or 1.0
    n_rows = _cluster([(w[1] + w[3]) / 2 for w in words], tol=line_h * 0.6)
    n_cols = _cluster([(w[0] + w[2]) / 2 for w in words], tol=char_w * 1.5)
    return GridShape(n_rows=n_rows, n_cols=n_cols, spans=())


def pymupdf_grid_for_node(page: "fitz.Page", node: Node) -> GridShape | None:
    """Convenience: the PyMuPDF grid for a table node, converting its Docling
    bottom-left bbox to the top-left region PyMuPDF words live in."""
    region = docling_bbox_to_topleft(node.provenance.bbox, page.rect.height)
    return pymupdf_grid(page, region)


from dataclasses import dataclass


@dataclass(frozen=True)
class GeometryConsensus:
    state: str            # "unanimous" | "majority" | "quarantined"
    reason: str | None
    candidates: dict[str, str]  # parser -> "RxC" for the audit trail


def _rc(g: GridShape | None) -> str:
    return f"{g.n_rows}x{g.n_cols}" if g else "none"


def reconcile(docling: GridShape | None, pdfplumber: GridShape | None,
              pymupdf: GridShape | None) -> GeometryConsensus:
    """Table-grid consensus over the two GENUINE independent geometry parsers --
    Docling (layout model) and pdfplumber (ruling lines) -- with PyMuPDF word
    clustering as an approximate corroborator.

    Measured rationale (see CHANGELOG): Docling and pdfplumber agree on
    n_rows/n_cols on every clean ruled table and disagree only on genuinely
    ambiguous ones, so their disagreement is the real merged-cell-collapse
    guard. PyMuPDF word clustering, by contrast, counts each wrapped line of a
    multi-line cell as a row (14-row tables read as 52 rows), so it is far too
    approximate to be a hard voter -- it corroborates when it happens to agree
    and is recorded either way, but never forces a quarantine on its own. A
    genuine third table-structure parser (Camelot / a second layout model) is
    the deferred swap-in to reach true three-way agreement.

    Quarantines when: pdfplumber sees no ruled grid (a borderless table whose
    geometry can't be corroborated -- exactly the case the rule protects), or
    Docling and pdfplumber disagree on n_rows/n_cols."""
    cand = {"docling": _rc(docling), "pdfplumber": _rc(pdfplumber), "pymupdf": _rc(pymupdf)}
    if docling is None:
        return GeometryConsensus("quarantined", "no docling geometry", cand)
    if pdfplumber is None:
        return GeometryConsensus(
            "quarantined",
            "pdfplumber saw no ruled grid (borderless table -- geometry unverifiable)", cand)
    d_rc, p_rc = (docling.n_rows, docling.n_cols), (pdfplumber.n_rows, pdfplumber.n_cols)
    if d_rc != p_rc:
        return GeometryConsensus(
            "quarantined",
            f"table geometry disagreement docling={_rc(docling)} vs pdfplumber={_rc(pdfplumber)}", cand)
    # the two genuine voters agree; PyMuPDF corroborates or is noted as differing
    if pymupdf is not None and (pymupdf.n_rows, pymupdf.n_cols) == d_rc:
        return GeometryConsensus("unanimous", None, cand)
    return GeometryConsensus("majority", "pymupdf word-grid corroborator differs (approximate)", cand)
