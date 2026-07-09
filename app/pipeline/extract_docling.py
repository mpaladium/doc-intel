"""extract.layout / extract.table — Docling as sole geometry owner (AGENTS.md §1.10).

Runs Docling's converter over the PDF and maps its item tree onto
`canonical_schema.Node`. Every node gets a `Provenance` -- no exceptions
(AGENTS.md §1.9). OCR is disabled by default: this iteration primarily serves
the `DIGITAL_CLEAN`/`DIGITAL_DIRTY` born-digital path (ARCHITECTURE.md §7
build order step 1), and per §4 "skip work aggressively", pages that already
have a usable text layer should never touch an OCR model.

Tree construction: Docling's `iterate_items()` yields a flat, reading-order
sequence; hierarchy is reconstructed here from `SectionHeaderItem.level`
(typographic heading depth), which is the closest language-independent proxy
Docling gives us for clause nesting. `topology.clauses` fills in the actual
`clause_id` from heading text afterward -- this stage only builds the shape
of the tree.

Resource efficiency (runs on both a Linux+NVIDIA GPU box and a Mac dev
machine, per AGENTS.md §3's spirit -- the full Redis VRAM lease is still
deferred, but the two things that matter most for a single-process deployment
are covered here):
  - `_select_device()` lets Docling's own accelerator auto-detection pick
    CUDA on Linux / MPS on Apple Silicon / CPU as the fallback, overridable
    via `INGESTION_DEVICE` for a shared box where you don't want this process
    claiming the GPU. `INGESTION_NUM_THREADS` caps CPU threads similarly.
  - `get_converter()` caches the built `DocumentConverter` (and therefore its
    loaded model weights) per `ocr_enabled` flag for the life of the process,
    instead of reloading Docling's layout/table models from disk on every
    single request.
"""

from __future__ import annotations

import io
import os
import uuid
from functools import lru_cache
from importlib.metadata import version as pkg_version

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc import DocItemLabel
from docling_core.types.io import DocumentStream

from canonical_schema import Cell, Node, Provenance

DOCLING_VERSION = pkg_version("docling")

_DEVICE_NAMES = {d.value: d for d in AcceleratorDevice}

_HEADING_LABELS = {DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE}

_LABEL_TO_NODETYPE = {
    DocItemLabel.TEXT: "paragraph",
    DocItemLabel.PARAGRAPH: "paragraph",
    DocItemLabel.CAPTION: "paragraph",
    DocItemLabel.FOOTNOTE: "note",
    DocItemLabel.LIST_ITEM: "list_item",
    DocItemLabel.TABLE: "table",
    DocItemLabel.PICTURE: "figure",
    DocItemLabel.CHART: "figure",
    DocItemLabel.FORMULA: "equation",
}

# Running headers/footers/page furniture are not document content -- never
# promoted to a Node (nothing to flag, nothing to compare).
_SKIP_LABELS = {DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER}

# Docling doesn't expose one per-element confidence score for born-digital
# text (it's not an ML prediction on this path, it's the PDF's own text
# layer). Fixed high-confidence default, documented so it's the first thing
# replaced with a real signal (e.g. table-cell/TEDS-derived, OCR confidence)
# once the OCR/dirty path is built.
_DIGITAL_TEXT_CONFIDENCE = 0.95
_TABLE_CONFIDENCE = 0.9
_FIGURE_CONFIDENCE = 0.9  # layout-model detection, not text-layer -- no .text field to read


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _select_device() -> AcceleratorDevice:
    """`INGESTION_DEVICE` (auto|cpu|cuda|mps) overrides; default AUTO lets
    Docling probe torch itself -- CUDA on the Linux/NVIDIA deployment target,
    MPS on Apple Silicon dev machines, CPU everywhere else."""
    override = os.environ.get("INGESTION_DEVICE", "auto").lower()
    return _DEVICE_NAMES.get(override, AcceleratorDevice.AUTO)


def _select_num_threads() -> int:
    override = os.environ.get("INGESTION_NUM_THREADS")
    if override:
        return int(override)
    return min(os.cpu_count() or 4, 8)


def build_converter(ocr_enabled: bool = False) -> DocumentConverter:
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = ocr_enabled
    pipeline_options.do_table_structure = True
    pipeline_options.accelerator_options = AcceleratorOptions(
        device=_select_device(), num_threads=_select_num_threads(),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


@lru_cache(maxsize=4)
def get_converter(ocr_enabled: bool) -> DocumentConverter:
    """Cached per (ocr_enabled) for the life of the process -- Docling's
    layout/table model weights get loaded once and reused across requests
    instead of being rebuilt (and reloaded from disk) on every `extract()`
    call. Keyed narrowly since this iteration only varies `do_ocr`; add more
    key dimensions here if pipeline_options grows more per-request variance."""
    return build_converter(ocr_enabled=ocr_enabled)


def _provenance(item, confidence: float) -> Provenance | None:
    if not item.prov:
        return None
    p = item.prov[0]
    bbox = p.bbox
    return Provenance(
        page=p.page_no,
        bbox=(bbox.l, bbox.t, bbox.r, bbox.b),
        parser="docling",
        model_version=DOCLING_VERSION,
        confidence=confidence,
    )


def _table_cells(item) -> list[Cell] | None:
    data = getattr(item, "data", None)
    if data is None or not getattr(data, "table_cells", None):
        return None
    cells: list[Cell] = []
    for tc in data.table_cells:
        cells.append(
            Cell(
                row=tc.start_row_offset_idx,
                col=tc.start_col_offset_idx,
                rowspan=max(tc.end_row_offset_idx - tc.start_row_offset_idx, 1),
                colspan=max(tc.end_col_offset_idx - tc.start_col_offset_idx, 1),
                header_path=[],  # populated by continuity.header_path
                text=tc.text or "",
            )
        )
    return cells


class _Builder:
    """Mutable intermediate node used while reconstructing hierarchy; frozen
    into a `Node` (pydantic, immutable-by-convention) once the tree is complete."""

    __slots__ = ("type", "text", "latex", "cells", "provenance", "children")

    def __init__(self, type_: str, text: str | None, provenance: Provenance | None,
                 latex: str | None = None, cells: list[Cell] | None = None):
        self.type = type_
        self.text = text
        self.latex = latex
        self.cells = cells
        self.provenance = provenance
        self.children: list["_Builder"] = []

    def to_node(self) -> Node | None:
        if self.provenance is None:
            return None  # no provenance, no node (AGENTS.md §1.9)
        children = [n for c in self.children if (n := c.to_node()) is not None]
        return Node(
            id=_new_id(),
            type=self.type,
            text=self.text,
            latex=self.latex,
            cells=self.cells,
            children=children,
            provenance=self.provenance,
        )


def _content_builder(item, node_type: str) -> _Builder:
    """Builds a non-heading, non-skipped item into a `_Builder`. Split out
    from `extract()`'s loop so the "not every item type has `.text`" handling
    is independently testable without a full Docling conversion (see
    `tests/test_extract_docling.py`)."""
    if node_type == "table":
        return _Builder("table", None, _provenance(item, _TABLE_CONFIDENCE), cells=_table_cells(item))
    if node_type == "figure":
        return _Builder("figure", None, _provenance(item, _FIGURE_CONFIDENCE))
    return _Builder(node_type, getattr(item, "text", None), _provenance(item, _DIGITAL_TEXT_CONFIDENCE))


def extract(pdf_bytes: bytes, ocr_enabled: bool = False) -> list[Node]:
    """PDF bytes -> top-level list of `Node` (type="section"), each carrying its
    full descendant subtree. Headings (any typographic level) open a new
    "section" node; everything else attaches to the deepest currently-open
    section. Content preceding the first heading becomes an untitled leading
    section so nothing is dropped."""
    converter = get_converter(ocr_enabled)
    stream = DocumentStream(name="input.pdf", stream=io.BytesIO(pdf_bytes))
    result = converter.convert(stream)
    dldoc = result.document

    top_sections: list[_Builder] = []
    # stack of (heading_level, builder) currently open, outermost first.
    stack: list[tuple[int, _Builder]] = []

    def _attach(builder: _Builder) -> None:
        if stack:
            stack[-1][1].children.append(builder)
        else:
            top_sections.append(builder)

    for item, _tree_level in dldoc.iterate_items():
        label = item.label
        if label in _SKIP_LABELS:
            continue

        # Not every Docling item type has a `.text` field (TableItem and
        # PictureItem notably don't -- discovered the hard way via an
        # AttributeError on a real-world PDF containing an image; see
        # CHANGELOG). `getattr(..., None)` is the general fix so any other
        # text-less item type Docling adds later degrades to "no text"
        # instead of crashing the whole request.
        item_text = getattr(item, "text", None)

        if label in _HEADING_LABELS:
            heading_level = getattr(item, "level", 1) or 1
            while stack and stack[-1][0] >= heading_level:
                stack.pop()
            section = _Builder(
                "section", item_text,
                _provenance(item, _DIGITAL_TEXT_CONFIDENCE),
            )
            _attach(section)
            stack.append((heading_level, section))
            continue

        node_type = _LABEL_TO_NODETYPE.get(label)
        if node_type is None:
            continue  # unmapped label (e.g. document_index, key/value forms) -- skip for now

        _attach(_content_builder(item, node_type))

    return [n for b in top_sections if (n := b.to_node()) is not None]
