"""page.rasterize -- renders page images for the verification UI (TECHSTACK.md:
PyMuPDF), written via artifact.put. Pure function of the PDF bytes, no state
retained here. Also returns each page's PDF-point size (from the source, not
the raster) so the UI can convert `Provenance.bbox` (PDF points, bottom-left
origin) into on-screen overlay coordinates."""

from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

RASTER_DPI = 150


@dataclass(frozen=True)
class RasterResult:
    images: dict[int, bytes]              # 1-indexed page_no -> PNG bytes
    page_sizes: dict[int, tuple[float, float]]  # 1-indexed page_no -> (width, height) in PDF points
    dpi: int


def rasterize(pdf_bytes: bytes, dpi: int = RASTER_DPI) -> RasterResult:
    images: dict[int, bytes] = {}
    page_sizes: dict[int, tuple[float, float]] = {}
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for i in range(doc.page_count):
            page = doc[i]
            pix = page.get_pixmap(matrix=matrix)
            images[i + 1] = pix.tobytes("png")
            page_sizes[i + 1] = (page.rect.width, page.rect.height)
    finally:
        doc.close()
    return RasterResult(images=images, page_sizes=page_sizes, dpi=dpi)
