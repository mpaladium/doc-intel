"""edition.assemble -- emits the final CanonicalEdition (SKILLS.md).

Wires together: extract -> triage.classify_page (confidence/review only) ->
lattice.reconcile -> topology.clauses -> continuity.stitch/header_path ->
classify.section_role -> a single root Node carrying pipeline_provenance for
cross-edition compatibility checks (ARCHITECTURE.md §1's pipeline-version
guardrail).

`canon.equation` / `canon.units` (LaTeX/unit canonicalization) are not wired
in yet -- this iteration's synthetic/born-digital test documents don't
exercise equations, and MinerU (the equation extractor) is explicitly out of
scope for this build-order step. Nodes simply carry `latex=None` until that
extractor exists.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path

import fitz  # PyMuPDF

from canonical_schema import CanonicalEdition, Node, Provenance
from app.pipeline import canon_equation, canon_units, continuity, lang, lattice, topology, triage, xref
from app.pipeline.extract_docling import DOCLING_VERSION, extract
from app.pipeline.route import Ownership
from app.pipeline.section_role_classifier import ClassificationContext, RolePack, classify_document
from app.pipeline.triage import PageClass, TriageResult
from app.version import PIPELINE_VERSION, SCHEMA_VERSION

RULEPACK_PATH = Path(__file__).parents[2] / "rulepacks" / "section_roles.yaml"

# Every born-digital node gets the same flat _DIGITAL_TEXT_CONFIDENCE from
# extract_docling.py regardless of how trustworthy the source page's text
# layer actually is -- triage measures that per page (app/pipeline/triage.py)
# but until now nothing consumed the result. This downgrade table replaces
# (not multiplies) that placeholder confidence for non-DIGITAL_CLEAN pages,
# since the placeholder was never a measured value in the first place.
# Revisit against a gold set once real dirty/scanned documents are available
# (same caveat as triage.py's own thresholds).
_CONFIDENCE_BY_PAGE_CLASS: dict[PageClass, float] = {
    "DIGITAL_DIRTY": 0.75,
    "SCANNED": 0.5,
    "UNCERTAIN": 0.6,
}


@lru_cache(maxsize=1)
def _rulepack() -> RolePack:
    return RolePack.load(RULEPACK_PATH)


@lru_cache(maxsize=1)
def _ownership() -> Ownership:
    return Ownership.load()


def _triage_pages(pdf_bytes: bytes) -> list[TriageResult]:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return triage.classify_document(doc)
    finally:
        doc.close()


def _apply_triage(node: Node, page_classes: list[TriageResult]) -> Node:
    """Depth-first, same rebuild-children-first pattern as
    `topology.assign_clause_ids` / `continuity.stitch`: nodes on a
    non-DIGITAL_CLEAN page get a real (downgraded) confidence instead of the
    flat extraction placeholder, plus review_required + a reason -- this is
    what makes the confidence-sorted inspector actually correlate with page
    quality (ARCHITECTURE.md §2.3), instead of every born-digital node
    looking equally trustworthy."""
    children = [_apply_triage(c, page_classes) for c in node.children]
    node = node.model_copy(update={"children": children})

    page_idx = node.provenance.page - 1
    if not (0 <= page_idx < len(page_classes)):
        return node
    page_class = page_classes[page_idx].page_class
    downgraded = _CONFIDENCE_BY_PAGE_CLASS.get(page_class)
    if downgraded is None:  # DIGITAL_CLEAN -- placeholder confidence stands
        return node

    return node.model_copy(update={
        "review_required": True,
        "review_reasons": node.review_reasons + [f"page_class_{page_class.lower()}"],
        "provenance": node.provenance.model_copy(update={"confidence": downgraded}),
    })


def _synthetic_root_provenance() -> Provenance:
    # The document root isn't a Docling element -- it's synthesized here to
    # satisfy "no node without provenance" (AGENTS.md §1.9) while making clear
    # this bbox/page carries no positional meaning.
    return Provenance(page=1, bbox=(0.0, 0.0, 0.0, 0.0), parser="assemble",
                       model_version=PIPELINE_VERSION, confidence=1.0)


def assemble(pdf_bytes: bytes, source_sha256: str, ocr_enabled: bool = False) -> CanonicalEdition:
    page_classes = _triage_pages(pdf_bytes)

    top_sections = extract(pdf_bytes, ocr_enabled=ocr_enabled)
    top_sections = [_apply_triage(n, page_classes) for n in top_sections]
    top_sections = lattice.reconcile(top_sections)
    top_sections = [topology.assign_clause_ids(n) for n in top_sections]
    top_sections = continuity.stitch(top_sections)
    top_sections = continuity.assign_header_paths(top_sections)
    top_sections = [canon_units.annotate_node(n) for n in top_sections]  # {value,unit,condition}
    top_sections = [canon_equation.canonicalize_node(n) for n in top_sections]
    top_sections = [lang.annotate_node(n) for n in top_sections]  # NFC + per-node lang
    top_sections = classify_document(top_sections, _rulepack())
    # Reconstruct the clause hierarchy last, so section-role classification
    # still runs over the flat top-level list (as designed) -- nesting only
    # re-parents the already-classified section nodes by clause number.
    top_sections = topology.nest_by_clause(top_sections)

    root = Node(
        id=uuid.uuid4().hex[:12],
        type="section",
        text=None,
        children=top_sections,
        provenance=_synthetic_root_provenance(),
    )
    # Cross-references need the whole edition's clause set to resolve, so run
    # over the assembled root (after clause_ids + nesting).
    root = xref.annotate_tree(root)
    lang_primary = lang.dominant_lang(root)

    ownership = _ownership()
    engine_by_page = {
        str(i + 1): {
            content_type: ownership.engine_for(content_type, tc.page_class)
            for content_type in ("layout", "table", "equation", "text")
        }
        for i, tc in enumerate(page_classes)
    }

    return CanonicalEdition(
        edition_id=f"{source_sha256}+{PIPELINE_VERSION}",
        source_sha256=source_sha256,
        schema_version=SCHEMA_VERSION,
        lang_primary=lang_primary,
        root=root,
        pipeline_provenance={
            "pipeline_version": PIPELINE_VERSION,
            "docling_version": DOCLING_VERSION,
            "ocr_enabled": ocr_enabled,
            "page_classes": {str(i + 1): tc.page_class for i, tc in enumerate(page_classes)},
            "engine_by_page": engine_by_page,
        },
    )
