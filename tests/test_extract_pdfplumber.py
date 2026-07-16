"""pdfplumber grid extractor (app/pipeline/extract_pdfplumber.py) -- the
independent third table opinion. Draws a ruled grid with PyMuPDF so the ruling
lines pdfplumber keys on actually exist, then checks the recovered shape."""

import fitz

from app.pipeline.extract_pdfplumber import GridShape, page_table_grids


def _ruled_grid_pdf(n_rows: int, n_cols: int, x0=72, y0=100, w=60, h=24) -> bytes:
    """A PDF with one page holding a single fully-ruled n_rows x n_cols grid."""
    doc = fitz.open()
    page = doc.new_page()
    x1, y1 = x0 + n_cols * w, y0 + n_rows * h
    for r in range(n_rows + 1):  # horizontal rules
        page.draw_line((x0, y0 + r * h), (x1, y0 + r * h))
    for c in range(n_cols + 1):  # vertical rules
        page.draw_line((x0 + c * w, y0), (x0 + c * w, y1))
    return doc.tobytes()


def test_finds_ruled_grid_shape():
    grids = page_table_grids(_ruled_grid_pdf(3, 2), page_index=0)
    assert len(grids) == 1
    g = grids[0]
    assert (g.n_rows, g.n_cols) == (3, 2)
    assert g.spans == ()


def test_no_ruled_table_returns_empty():
    doc = fitz.open()
    doc.new_page().insert_text((72, 100), "just prose, no rules")
    assert page_table_grids(doc.tobytes(), page_index=0) == []


def test_out_of_range_page_is_empty_not_error():
    assert page_table_grids(_ruled_grid_pdf(2, 2), page_index=9) == []


def test_gridshape_is_hashable_and_comparable():
    # consensus compares shapes by value; identical shapes must be equal
    assert GridShape(3, 2) == GridShape(3, 2)
    assert GridShape(3, 2, ((0, 0, 1, 2),)) != GridShape(3, 2)
