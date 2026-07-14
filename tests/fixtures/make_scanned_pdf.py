"""Builds a text-layer-free "scanned" PDF from a born-digital one: rasterizes
each page (`app.store.rasterize.rasterize` -- the same function the API uses
for the verification UI) and re-wraps the images into a fresh PDF with no
text layer at all. Triage (`app.pipeline.triage`) correctly classifies a page
like this SCANNED, since `page.get_text()` returns nothing.

This gives OCR validation *free ground truth*: the ORIGINAL PDF's text layer
is exactly what should come back out of OCR on the rasterized copy, so
accuracy can be measured against real text -- not hand-labeled -- with
`app.cli.accuracy_check --gold-source <original.pdf>`.

Usage:
    uv run python -m tests.fixtures.make_scanned_pdf <source.pdf> <output.pdf>
"""

from __future__ import annotations

import sys
from pathlib import Path

import fitz  # PyMuPDF

from app.store.rasterize import rasterize


def build(source_pdf: bytes, dpi: int = 150) -> bytes:
    raster = rasterize(source_pdf, dpi=dpi)
    out = fitz.open()
    for page_no in sorted(raster.images):
        width, height = raster.page_sizes[page_no]
        page = out.new_page(width=width, height=height)
        page.insert_image(page.rect, stream=raster.images[page_no])
    pdf_bytes = out.tobytes()
    out.close()
    return pdf_bytes


def build_file(source_path: Path, dest_path: Path, dpi: int = 150) -> None:
    dest_path.write_bytes(build(source_path.read_bytes(), dpi=dpi))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    build_file(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"wrote {sys.argv[2]}")
