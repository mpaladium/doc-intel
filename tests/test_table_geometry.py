"""Table-grid geometry consensus (app/pipeline/table_geometry.py): the Docling +
pdfplumber genuine-voter agreement with PyMuPDF as an approximate corroborator.
Docling grid is read off cells; the reconcile logic is pure and PDF-free."""

import fitz

import canonical_schema as cs
from app.pipeline.extract_pdfplumber import GridShape
from app.pipeline import table_geometry as tg


def _prov(page=1):
    return cs.Provenance(page=page, bbox=(0, 0, 10, 10), parser="docling",
                         model_version="v", confidence=0.9)


def _table(cells):
    return cs.Node(id="t", type="table", provenance=_prov(), cells=cells)


# --- docling_grid from cells --------------------------------------------------

def test_docling_grid_from_cells():
    cells = [cs.Cell(row=r, col=c, text="x") for r in range(3) for c in range(2)]
    g = tg.docling_grid(_table(cells))
    assert (g.n_rows, g.n_cols) == (3, 2)
    assert g.spans == ()


def test_docling_grid_captures_span():
    cells = [cs.Cell(row=0, col=0, text="H", colspan=2),
             cs.Cell(row=1, col=0, text="a"), cs.Cell(row=1, col=1, text="b")]
    g = tg.docling_grid(_table(cells))
    assert (g.n_rows, g.n_cols) == (2, 2)
    assert g.spans == ((0, 0, 1, 2),)


# --- reconcile: the genuine-voter logic ---------------------------------------

def _g(r, c):
    return GridShape(n_rows=r, n_cols=c)


def test_reconcile_unanimous_when_all_three_agree():
    r = tg.reconcile(_g(5, 3), _g(5, 3), _g(5, 3))
    assert r.state == "unanimous"


def test_reconcile_admits_when_docling_pdfplumber_agree_pymupdf_noisy():
    # PyMuPDF over-counts rows (multi-line cells) -- must NOT force a quarantine
    r = tg.reconcile(_g(14, 5), _g(14, 5), _g(52, 5))
    assert r.state == "majority"  # admitted, corroborator differs
    assert r.reason and "pymupdf" in r.reason


def test_reconcile_quarantines_docling_pdfplumber_disagreement():
    r = tg.reconcile(_g(5, 3), _g(4, 3), _g(5, 3))
    assert r.state == "quarantined"
    assert "disagreement" in r.reason


def test_reconcile_quarantines_borderless_table():
    # pdfplumber saw no ruled grid -> geometry unverifiable
    r = tg.reconcile(_g(3, 3), None, _g(3, 6))
    assert r.state == "quarantined"
    assert "borderless" in r.reason


# --- pymupdf_grid producer (real PDF) -----------------------------------------

def test_pymupdf_grid_counts_rows_and_cols():
    doc = fitz.open()
    page = doc.new_page()
    # 3 rows x 2 cols of words on a grid
    for r in range(3):
        for c in range(2):
            page.insert_text((72 + c * 120, 100 + r * 30), f"w{r}{c}", fontsize=11)
    g = tg.pymupdf_grid(page, (60, 85, 260, 175))
    assert g is not None
    assert g.n_rows == 3
    assert g.n_cols == 2
