"""runs -- PyMuPDF per-character font/baseline extraction (the superscript / ±
authority, parser-consensus.md). PyMuPDF `runs` are the sole source of the
signal that catches the corruption no `raw_text` check can see: `10⁻³ V/m`
flattens to `10-3` *before the string exists*, and only font+baseline metadata
can detect it. Docling gives region geometry; PyMuPDF gives the runs inside
that geometry -- this module produces the runs and intersects them with a
node's bbox.

Coordinate systems: PyMuPDF `rawdict` is TOP-LEFT origin; Docling/Provenance
bboxes are BOTTOM-LEFT. `docling_bbox_to_topleft` bridges them (same
conversion the rasterizer/accuracy-checker use).
"""

from __future__ import annotations

from dataclasses import dataclass

import fitz  # PyMuPDF

from canonical_schema import Run, VerticalAlign

# PyMuPDF span flags (bit field): 1=superscript, 2=italic, 16=bold.
_FLAG_SUPERSCRIPT = 1
_FLAG_ITALIC = 2
_FLAG_BOLD = 16

# A baseline shift beyond this fraction of the line's dominant font size, on a
# smaller-than-line span, is a super/subscript even when PyMuPDF didn't flag it
# (it flags superscript but not subscript).
_BASELINE_FRAC = 0.15


@dataclass(frozen=True)
class PlacedRun:
    """A Run plus its top-left-origin bbox and text baseline, so it can be
    intersected with a node's region and ordered into reading order (a
    sub/superscript's bbox is shifted off the line, so ordering must use the
    baseline, not the bbox top)."""
    run: Run
    bbox: tuple[float, float, float, float]  # top-left origin
    baseline: float                          # span origin y (line position)


def _span_text(span: dict) -> str:
    return span.get("text") or "".join(c["c"] for c in span.get("chars", []))


def _classify_vertical(span: dict, line_baseline: float, line_size: float) -> tuple[VerticalAlign, float]:
    """Return (vertical_align, baseline_offset). baseline_offset > 0 == raised
    (superscript), < 0 == lowered (subscript); rawdict is top-left so a raised
    glyph has a SMALLER y, hence `line_baseline - span_baseline`."""
    span_baseline = span["origin"][1]
    offset = line_baseline - span_baseline
    if span.get("flags", 0) & _FLAG_SUPERSCRIPT:
        return "superscript", offset
    if line_size > 0 and span["size"] < line_size:
        thresh = _BASELINE_FRAC * line_size
        if offset > thresh:
            return "superscript", offset
        if offset < -thresh:
            return "subscript", offset
    return "normal", offset


def _line_baseline_and_size(line: dict) -> tuple[float, float]:
    """The line's dominant baseline + font size = those of its largest span
    (body text), against which raised/lowered spans are measured."""
    spans = [s for s in line["spans"] if _span_text(s).strip()]
    if not spans:
        return 0.0, 0.0
    dominant = max(spans, key=lambda s: s["size"])
    return dominant["origin"][1], dominant["size"]


def page_runs(page: "fitz.Page") -> list[PlacedRun]:
    """Every text run on the page, in reading order, with vertical-align
    classified from baseline/flags. One Run per span (a span is already a
    maximal same-font/size run -- never merge across spans, that's the merge
    that destroys the signal)."""
    placed: list[PlacedRun] = []
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            line_baseline, line_size = _line_baseline_and_size(line)
            for span in line["spans"]:
                text = _span_text(span)
                if not text:
                    continue
                valign, offset = _classify_vertical(span, line_baseline, line_size)
                flags = span.get("flags", 0)
                placed.append(PlacedRun(
                    run=Run(
                        text=text, font=span.get("font", ""), size=span["size"],
                        baseline_offset=round(offset, 2),
                        bold=bool(flags & _FLAG_BOLD), italic=bool(flags & _FLAG_ITALIC),
                        vertical_align=valign, bbox=tuple(round(x, 2) for x in span["bbox"]),
                    ),
                    bbox=tuple(span["bbox"]),
                    # A sub/superscript sits on the SAME line as its base; use
                    # the base line's baseline (not the shifted glyph's) so it
                    # orders within the line, not after it.
                    baseline=(line_baseline if valign != "normal" else span["origin"][1]),
                ))
    return placed


def docling_bbox_to_topleft(bbox: tuple[float, float, float, float],
                            page_height: float) -> tuple[float, float, float, float]:
    """Docling/Provenance bbox (bottom-left origin, t>b) -> PyMuPDF top-left
    rect. Handles either convention (already top-left if t<b)."""
    l, t, r, b = bbox
    if t > b:  # bottom-left origin
        return (l, page_height - t, r, page_height - b)
    return (l, t, r, b)


def _center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


_LINE_TOL = 3.0  # baselines within this many points are the same line


def _reading_order(placed: list[PlacedRun]) -> list[PlacedRun]:
    """Cluster runs into lines by baseline (a sub/superscript shares its base's
    line), order lines top-to-bottom, and order within a line left-to-right --
    so 'H' + subscript '2' + 'O' come out H,2,O not H,O,2."""
    if not placed:
        return []
    by_base = sorted(placed, key=lambda p: p.baseline)
    lines: list[list[PlacedRun]] = []
    for p in by_base:
        if lines and abs(p.baseline - lines[-1][0].baseline) <= _LINE_TOL:
            lines[-1].append(p)
        else:
            lines.append([p])
    out: list[PlacedRun] = []
    for line in lines:
        out.extend(sorted(line, key=lambda p: p.bbox[0]))
    return out


def runs_in_region(placed: list[PlacedRun], region_topleft: tuple[float, float, float, float],
                   pad: float = 1.0) -> list[Run]:
    """Runs whose center falls inside the region (top-left coords), in reading
    order (line by baseline, then left-to-right)."""
    rx0, ry0, rx1, ry1 = region_topleft
    inside = [p for p in placed
              if rx0 - pad <= _center(p.bbox)[0] <= rx1 + pad
              and ry0 - pad <= _center(p.bbox)[1] <= ry1 + pad]
    return [p.run for p in _reading_order(inside)]
