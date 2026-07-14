"""extract.layout / extract.table — Docling as sole geometry owner (AGENTS.md §1.10).

Runs Docling's converter over the PDF and maps its item tree onto
`canonical_schema.Node`. Every node gets a `Provenance` -- no exceptions
(AGENTS.md §1.9). OCR is disabled by default: this iteration primarily serves
the `DIGITAL_CLEAN`/`DIGITAL_DIRTY` born-digital path (ARCHITECTURE.md §7
build order step 1), and per §4 "skip work aggressively", pages that already
have a usable text layer should never touch an OCR model.

Tree construction (`_build_tree`): walks Docling's real `body` tree,
combining two nesting mechanisms -- section hierarchy from
`SectionHeaderItem.level` (Docling keeps headers as flat body siblings) and
group/list hierarchy from the tree structure itself (`ListGroup` > `ListItem`
> nested `ListGroup`). `topology.clauses` fills in the actual `clause_id`
from heading text afterward -- this stage only builds the shape of the tree.

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
import re
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

# CAPTION-labeled items are deliberately NOT in _LABEL_TO_NODETYPE: they are
# never appended as flat paragraph siblings in reading order. Instead they're
# captured only via TableItem/PictureItem.captions ref resolution (see
# _caption_children), which keeps a caption attached to the table/figure it
# describes instead of floating loose in the tree next to it.
_CAPTION_LABELS = {DocItemLabel.CAPTION}

# Docling's own layout model can mislabel caption-like text ("Table 1 ...",
# "Figure 2 ...") as a SectionHeaderItem. Unguarded, that opens a spurious
# new "section" node for it. This is a language-limited heuristic (English
# only) -- acceptable here because it only ever *prevents* a false section
# promotion; on a miss, the text still becomes a normal caption/paragraph
# node under the open section, never lost.
_CAPTION_LIKE = re.compile(r"^(table|figure|fig\.)\s*\d+", re.IGNORECASE)

# Docling doesn't expose one per-element confidence score for born-digital
# text (it's not an ML prediction on this path, it's the PDF's own text
# layer). Fixed high-confidence default, documented so it's the first thing
# replaced with a real signal (e.g. table-cell/TEDS-derived, OCR confidence)
# once the OCR/dirty path is built.
_DIGITAL_TEXT_CONFIDENCE = 0.95
_TABLE_CONFIDENCE = 0.9
_FIGURE_CONFIDENCE = 0.9  # layout-model detection, not text-layer -- no .text field to read
_EQUATION_CONFIDENCE = 0.85  # formula enrichment is a VLM prediction, less certain than text


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


def _formulas_enabled() -> bool:
    """`INGESTION_FORMULAS` (default on). Docling's formula enrichment
    (CodeFormulaV2) is the Goal-1 `extract.equation` path -- it re-reads each
    detected FORMULA region and emits LaTeX into `FormulaItem.text`. It's a
    per-formula VLM pass, so it can be turned off for docs known to have no
    maths, or where the extra latency isn't worth it."""
    return os.environ.get("INGESTION_FORMULAS", "1").lower() not in ("0", "false", "no")


def build_converter(ocr_enabled: bool = False, formulas: bool | None = None) -> DocumentConverter:
    if formulas is None:
        formulas = _formulas_enabled()
    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = ocr_enabled
    pipeline_options.do_table_structure = True
    pipeline_options.do_formula_enrichment = formulas
    pipeline_options.accelerator_options = AcceleratorOptions(
        device=_select_device(), num_threads=_select_num_threads(),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


@lru_cache(maxsize=4)
def get_converter(ocr_enabled: bool, formulas: bool | None = None) -> DocumentConverter:
    """Cached per (ocr_enabled, formulas) for the life of the process --
    Docling's layout/table/formula model weights get loaded once and reused
    across requests instead of being rebuilt (and reloaded from disk) on every
    `extract()` call."""
    return build_converter(ocr_enabled=ocr_enabled, formulas=formulas)


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


def _cell_bbox(tc) -> tuple[float, float, float, float] | None:
    bb = getattr(tc, "bbox", None)
    if bb is None:
        return None
    return (bb.l, bb.t, bb.r, bb.b)


def _table_cells(item) -> list[Cell] | None:
    data = getattr(item, "data", None)
    if data is None or not getattr(data, "table_cells", None):
        return None
    # The cell's source page is the table's page. This matters after
    # continuity.stitch merges a continuation table onto the previous page's
    # node -- each cell keeps the page it was actually extracted from, so
    # downstream (and the accuracy checker) can attribute cell text to the
    # right page rather than the merged node's first-page provenance.
    page = item.prov[0].page_no if getattr(item, "prov", None) else None
    cells: list[Cell] = []
    for tc in data.table_cells:
        cells.append(
            Cell(
                row=tc.start_row_offset_idx,
                col=tc.start_col_offset_idx,
                rowspan=max(tc.end_row_offset_idx - tc.start_row_offset_idx, 1),
                colspan=max(tc.end_col_offset_idx - tc.start_col_offset_idx, 1),
                header_path=[],  # populated by continuity.header_path
                is_column_header=bool(getattr(tc, "column_header", False)),
                text=tc.text or "",
                page=page,
                bbox=_cell_bbox(tc),
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


def _resolve_caption(item, ref, dldoc):
    """Resolves one `RefItem` from a table/figure's `.captions` list. A bad
    ref shouldn't kill extraction of an otherwise-good table/figure, so
    resolution failures return `None` rather than raising -- the same
    fail-soft philosophy as the `getattr` fallback below for missing
    `.text`."""
    try:
        return ref.resolve(dldoc)
    except Exception:
        return None


def _is_group(item) -> bool:
    """A Docling GroupItem/ListGroup/InlineGroup is a structural container,
    not content -- its `.label` is a GroupLabel, whereas a real content
    DocItem's `.label` is a DocItemLabel. Detecting groups this way (rather
    than `isinstance(..., GroupItem)`) also lets tests stand in a group with
    a non-DocItemLabel marker without importing Docling's class."""
    return not isinstance(getattr(item, "label", None), DocItemLabel)


def _child_items(dldoc, item) -> list:
    """Resolve an item's `.children` refs to their target items, skipping any
    that don't resolve (fail-soft, same as caption resolution)."""
    out = []
    for ref in getattr(item, "children", None) or []:
        try:
            resolved = ref.resolve(dldoc)
        except Exception:
            continue
        if resolved is not None:
            out.append(resolved)
    return out


def _iter_doc_items(dldoc):
    """Depth-first over the body tree, yielding every item (groups included).
    The single traversal source for the whole module so tree-shape stays
    consistent between the caption-claim scan and `_build_tree`."""
    root = getattr(dldoc, "body", None)
    if root is None:
        return
    stack = list(reversed(_child_items(dldoc, root)))
    while stack:
        item = stack.pop()
        yield item
        stack.extend(reversed(_child_items(dldoc, item)))


def _claimed_caption_ids(dldoc) -> set[int]:
    """Docling doesn't reliably populate `.captions` on every table/figure it
    labels a nearby text block CAPTION for (confirmed empirically: a caption
    directly adjacent to a table in a synthetic PDF still came back with
    `TableItem.captions == []`). So caption attachment can't just trust that
    every CAPTION-labeled item will be reachable via some table/figure's
    `.captions` -- that would silently drop unclaimed ones. This does one
    pass up front to record which caption item objects (`id()`, stable for
    the lifetime of one `dldoc`) *are* claimed via a resolvable `.captions`
    ref, so `_build_tree`'s main loop can nest those under their table/figure
    and still preserve any unclaimed caption as its own node in reading-order
    position -- never silently lost, per AGENTS.md §1.9 ("no exceptions for
    obvious content")."""
    claimed: set[int] = set()
    for item in _iter_doc_items(dldoc):
        for ref in getattr(item, "captions", None) or []:
            cap_item = _resolve_caption(item, ref, dldoc)
            if cap_item is not None:
                claimed.add(id(cap_item))
    return claimed


def _caption_children(item, dldoc) -> list[_Builder]:
    """Resolves a table/figure item's `.captions` into "caption"-typed
    `_Builder` children."""
    children: list[_Builder] = []
    for ref in getattr(item, "captions", None) or []:
        cap_item = _resolve_caption(item, ref, dldoc)
        if cap_item is None:
            continue
        cap_text = getattr(cap_item, "text", None)
        if cap_text is None:
            continue
        children.append(_Builder("caption", cap_text, _provenance(cap_item, _DIGITAL_TEXT_CONFIDENCE)))
    return children


def _content_builder(item, node_type: str, dldoc=None) -> _Builder:
    """Builds a non-heading, non-skipped item into a `_Builder`. Split out
    from `_build_tree()`'s loop so the "not every item type has `.text`"
    handling is independently testable without a full Docling conversion
    (see `tests/test_extract_docling.py`). `dldoc` is optional (default
    `None`, meaning "skip caption resolution") purely so those stub-based
    tests don't need a real `DoclingDocument` when they aren't exercising
    captions."""
    if node_type == "table":
        builder = _Builder("table", None, _provenance(item, _TABLE_CONFIDENCE), cells=_table_cells(item))
    elif node_type == "figure":
        builder = _Builder("figure", None, _provenance(item, _FIGURE_CONFIDENCE))
    elif node_type == "equation":
        # After formula enrichment, a FormulaItem's `.text` IS its LaTeX. We
        # keep it in both `text` (so it renders/searches) and `latex` (the
        # canonicalization target -- canon.equation normalizes it later). If
        # enrichment was off/failed, `.text` may be the raw glyph string;
        # still stored, just not guaranteed to be valid LaTeX.
        latex = getattr(item, "text", None) or None
        return _Builder("equation", latex, _provenance(item, _EQUATION_CONFIDENCE), latex=latex)
    else:
        return _Builder(node_type, getattr(item, "text", None), _provenance(item, _DIGITAL_TEXT_CONFIDENCE))

    if dldoc is not None and getattr(item, "captions", None):
        builder.children.extend(_caption_children(item, dldoc))
    return builder


def _build_tree(dldoc) -> list[Node]:
    """`DoclingDocument` -> top-level list of `Node` (type="section"), each
    carrying its full descendant subtree.

    Two independent nesting mechanisms are combined by walking Docling's
    actual `body` tree (not the flattened `iterate_items()`):
      * **Section nesting** from heading levels -- Docling keeps section
        headers as flat siblings under `body`, so a heading-level stack turns
        them back into a clause hierarchy (h1 > h2 > h2.3 ...).
      * **Group/list nesting** from the body tree itself -- a `ListGroup`
        contains `ListItem`s, and a `ListItem` can contain a nested
        `ListGroup`; recursing the tree preserves that depth instead of
        collapsing nested lists into flat siblings (the deep-nesting gap).

    Content before the first heading attaches at top level, so nothing is
    dropped. Split out from `extract()` so it's testable against a fake
    `dldoc` exposing a `body` with resolvable `children` refs."""
    claimed_captions = _claimed_caption_ids(dldoc)

    top_sections: list[_Builder] = []
    stack: list[tuple[int, _Builder]] = []  # (heading_level, builder), section nesting

    def _section_attach(builder: _Builder) -> None:
        if stack:
            stack[-1][1].children.append(builder)
        else:
            top_sections.append(builder)

    def _walk(item, attach) -> None:
        """`attach(builder)` places a node at the current nesting point:
        section level at the top, or under a list_item/group when recursing."""
        if _is_group(item):
            # Structural container (ListGroup / inline group): no node of its
            # own; its children nest at the same point it appears.
            for child in _child_items(dldoc, item):
                _walk(child, attach)
            return

        label = item.label
        if label in _SKIP_LABELS:
            return

        if label in _CAPTION_LABELS:
            if id(item) in claimed_captions:
                return  # attached under its table/figure via .captions
            attach(_Builder("caption", getattr(item, "text", None),
                            _provenance(item, _DIGITAL_TEXT_CONFIDENCE)))
            return

        item_text = getattr(item, "text", None)

        if label in _HEADING_LABELS:
            # Guard: Docling can mislabel caption-like text as a heading; never
            # open a section for it.
            if item_text and _CAPTION_LIKE.match(item_text.strip()):
                attach(_Builder("caption", item_text, _provenance(item, _DIGITAL_TEXT_CONFIDENCE)))
                return
            heading_level = getattr(item, "level", 1) or 1
            while stack and stack[-1][0] >= heading_level:
                stack.pop()
            section = _Builder("section", item_text, _provenance(item, _DIGITAL_TEXT_CONFIDENCE))
            _section_attach(section)  # sections always nest by heading level, not locally
            stack.append((heading_level, section))
            for child in _child_items(dldoc, item):
                _walk(child, section.children.append)
            return

        node_type = _LABEL_TO_NODETYPE.get(label)
        if node_type is None:
            return  # unmapped label (document_index, key/value forms) -- skip

        builder = _content_builder(item, node_type, dldoc)
        attach(builder)
        # nest this item's own descendants (e.g. a list_item holding a sub-list)
        for child in _child_items(dldoc, item):
            _walk(child, builder.children.append)

    body = getattr(dldoc, "body", None)
    if body is not None:
        for item in _child_items(dldoc, body):
            _walk(item, _section_attach)

    return [n for b in top_sections if (n := b.to_node()) is not None]


def extract(pdf_bytes: bytes, ocr_enabled: bool = False) -> list[Node]:
    """PDF bytes -> top-level list of `Node` (type="section"). I/O layer:
    runs the Docling conversion, then delegates tree-building to
    `_build_tree()`."""
    converter = get_converter(ocr_enabled)
    stream = DocumentStream(name="input.pdf", stream=io.BytesIO(pdf_bytes))
    result = converter.convert(stream)
    return _build_tree(result.document)
