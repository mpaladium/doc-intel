"""Extraction-quality metrics computed from a CanonicalEdition, used by the
random-sampling evaluation harness (`app/cli/evaluate_samples.py`) to surface
where extraction breaks on real documents across the four target problem
areas: multilingual coverage, table fidelity, deep nesting, and formula/math
extraction. Pure functions of a CanonicalEdition -- no I/O, no models -- so
they're cheap to run per-document and easy to unit-test.
"""

from __future__ import annotations

import unicodedata
from dataclasses import asdict, dataclass, field

from canonical_schema import CanonicalEdition, Node


def _iter_nodes(node: Node, depth: int = 0):
    yield node, depth
    for child in node.children:
        yield from _iter_nodes(child, depth + 1)


def _is_nfc(text: str) -> bool:
    return unicodedata.is_normalized("NFC", text)


@dataclass
class DocMetrics:
    filename: str
    status: str
    pages: int = 0
    # triage
    page_class_counts: dict[str, int] = field(default_factory=dict)
    uncertain_rate: float = 0.0
    # structure
    node_type_counts: dict[str, int] = field(default_factory=dict)
    max_depth: int = 0
    # nesting
    list_items: int = 0
    nested_list_items: int = 0  # list_item whose parent is also a list_item
    # tables
    tables: int = 0
    total_cells: int = 0
    data_cells: int = 0
    data_cells_with_header_path: int = 0
    max_rowspan: int = 1
    max_colspan: int = 1
    # multilingual
    text_nodes: int = 0
    lang_populated: int = 0
    distinct_langs: list[str] = field(default_factory=list)
    lang_primary: str | None = None
    non_nfc_text_nodes: int = 0
    # formulas
    equation_nodes: int = 0
    equation_nodes_with_latex: int = 0
    # review
    review_required: int = 0
    mean_confidence: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def compute_metrics(filename: str, edition: CanonicalEdition) -> DocMetrics:
    m = DocMetrics(filename=filename, status="processed")

    parent_type: dict[int, str] = {}  # id(node) -> parent node type
    confidences: list[float] = []
    langs: set[str] = set()

    for node, depth in _iter_nodes(edition.root):
        m.max_depth = max(m.max_depth, depth)
        m.node_type_counts[node.type] = m.node_type_counts.get(node.type, 0) + 1
        for child in node.children:
            parent_type[id(child)] = node.type

        if node.provenance is not None:
            confidences.append(node.provenance.confidence)
        if node.review_required:
            m.review_required += 1

        if node.type == "list_item":
            m.list_items += 1
            if parent_type.get(id(node)) == "list_item":
                m.nested_list_items += 1

        if node.type == "table" and node.cells:
            m.tables += 1
            m.total_cells += len(node.cells)
            # Header rows via the extractor's own flag (matches continuity.py),
            # falling back to row 0 only if nothing is flagged -- so multi-row
            # headers aren't miscounted as uncovered data cells.
            flagged = {c.row for c in node.cells if c.is_column_header}
            header_rows = flagged or ({0} if node.cells else set())
            for c in node.cells:
                m.max_rowspan = max(m.max_rowspan, c.rowspan)
                m.max_colspan = max(m.max_colspan, c.colspan)
                if c.row not in header_rows:
                    m.data_cells += 1
                    if c.header_path:
                        m.data_cells_with_header_path += 1

        if node.type == "equation":
            m.equation_nodes += 1
            if node.latex:
                m.equation_nodes_with_latex += 1

        if node.text:
            m.text_nodes += 1
            if node.lang:
                m.lang_populated += 1
                langs.add(node.lang)
            if not _is_nfc(node.text):
                m.non_nfc_text_nodes += 1

    m.distinct_langs = sorted(langs)
    m.lang_primary = edition.lang_primary
    m.mean_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0

    prov = edition.pipeline_provenance or {}
    page_classes = prov.get("page_classes", {})
    m.pages = len(page_classes)
    for cls in page_classes.values():
        m.page_class_counts[cls] = m.page_class_counts.get(cls, 0) + 1
    if m.pages:
        m.uncertain_rate = round(m.page_class_counts.get("UNCERTAIN", 0) / m.pages, 4)

    return m
