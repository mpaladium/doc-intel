"""PyMuPDF runs extractor (app/pipeline/runs.py): super/subscript detection
from baseline + flags, region intersection, and the reconstruction that makes
the run-integrity gate meaningful. Uses tiny in-memory PDFs (deterministic)."""

import fitz
import pytest

from app.pipeline.runs import (docling_bbox_to_topleft, page_runs, runs_in_region)
from canonical_schema import reconstruct_raw_text


def _pdf(draw) -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    draw(page)
    return doc.tobytes()


def _first_page(pdf_bytes: bytes):
    return fitz.open(stream=pdf_bytes, filetype="pdf")[0]


def test_superscript_detected_and_reconstructed():
    def draw(p):
        p.insert_text((72, 100), "m", fontsize=12)
        p.insert_text((80, 96), "2", fontsize=8)   # raised + smaller = superscript
    runs = runs_in_region(page_runs(_first_page(_pdf(draw))), (70, 85, 92, 106))
    assert reconstruct_raw_text(runs) == "m²"


def test_subscript_detected_and_reconstructed():
    def draw(p):
        p.insert_text((72, 100), "H", fontsize=12)
        p.insert_text((80, 103), "2", fontsize=8)   # lowered + smaller = subscript
        p.insert_text((86, 100), "O", fontsize=12)
    runs = runs_in_region(page_runs(_first_page(_pdf(draw))), (70, 85, 95, 110))
    assert reconstruct_raw_text(runs) == "H₂O"


def test_normal_text_reconstructs_verbatim():
    def draw(p):
        p.insert_text((72, 100), "shall be 10 V/m", fontsize=11)
    placed = page_runs(_first_page(_pdf(draw)))
    assert reconstruct_raw_text([p.run for p in placed]) == "shall be 10 V/m"
    assert all(p.run.vertical_align == "normal" for p in placed)


def test_runs_in_region_filters_by_bbox():
    def draw(p):
        p.insert_text((72, 100), "inside", fontsize=11)
        p.insert_text((72, 400), "outside", fontsize=11)
    placed = page_runs(_first_page(_pdf(draw)))
    inside = runs_in_region(placed, (60, 88, 200, 110))
    assert "".join(r.text for r in inside) == "inside"


def test_docling_bbox_to_topleft_bottom_left_origin():
    # bottom-left bbox (t>b) on an 842-high page -> top-left
    assert docling_bbox_to_topleft((10, 800, 50, 780), 842.0) == (10, 42.0, 50, 62.0)
    # already top-left (t<b) passes through
    assert docling_bbox_to_topleft((10, 40, 50, 60), 842.0) == (10, 40, 50, 60)
