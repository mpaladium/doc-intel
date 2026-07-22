"""edition.assemble -- emits the final CanonicalEdition (SKILLS.md).

Wires together: extract(Docling, OCR auto-routed by triage) ->
caption_attach (proximity re-parenting) -> triage.classify_page
(confidence/review) -> lattice.reconcile -> topology.clauses ->
continuity.stitch/header_path -> canon.units -> canon.equation -> lang ->
classify.section_role -> nest_by_clause -> xref -> a single root Node
carrying pipeline_provenance for cross-edition compatibility checks
(ARCHITECTURE.md §1's pipeline-version guardrail).

OCR routing (build-order step 2, ARCHITECTURE.md §7): triage runs first, so
by the time `extract()` is called this function already knows whether any
page is SCANNED/UNCERTAIN. If so (and `INGESTION_OCR` permits, default on),
the whole document is re-extracted with Docling's OCR enabled -- Docling
decides internally which pages actually need it, so DIGITAL_CLEAN pages in
the same document still skip OCR (ARCHITECTURE.md §4 "skip work
aggressively"). An explicit `ocr_enabled=True` caller override always wins.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
import uuid
from functools import lru_cache
from pathlib import Path

import fitz  # PyMuPDF

from canonical_schema import CanonicalEdition, Node, Provenance, same_text_content
from app.pipeline import (
    canon_equation, canon_units, caption_attach, classify_type, continuity, gates, identity,
    lang, lattice, nested_table, parameters, topology, triage, xref,
)
from app.pipeline.extract_docling import (
    DOCLING_VERSION, extract_with_confidence, resolved_ocr_engine,
)
from app.pipeline.route import Ownership
from app.pipeline.section_role_classifier import ClassificationContext, RolePack, classify_document
from app.pipeline.triage import PageClass, TriageResult
from app.version import PIPELINE_VERSION, SCHEMA_VERSION

log = logging.getLogger("pipeline.assemble")

_OCR_TRIGGER_CLASSES = {"SCANNED", "UNCERTAIN"}


def _ocr_permitted() -> bool:
    return os.environ.get("INGESTION_OCR", "1").lower() not in ("0", "false", "no")


def _ocr_needed(page_classes: list[TriageResult]) -> bool:
    return any(tc.page_class in _OCR_TRIGGER_CLASSES for tc in page_classes)

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


_RUN_TEXT_TYPES = {"paragraph", "heading", "list_item", "caption", "note"}


def _backfill_runs(top_sections: list[Node], pdf_bytes: bytes) -> list[Node]:
    """Back-fill every text node with PyMuPDF `runs` intersected by its bbox
    (parser-consensus.md authority split: Docling gives region geometry,
    PyMuPDF gives the runs inside it -- the only layer that catches
    superscript/± loss). Node bboxes are Docling bottom-left origin; convert to
    PyMuPDF top-left via the page height before intersecting. Skips scanned
    pages with no text layer -- there are simply no runs to place there."""
    from canonical_schema import reconstruct_raw_text
    from app.pipeline import runs as runs_mod

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_cache: dict[int, tuple[list, float]] = {}

        def placed_for(page_no: int):
            if page_no not in page_cache:
                if 1 <= page_no <= doc.page_count:
                    page = doc[page_no - 1]
                    page_cache[page_no] = (runs_mod.page_runs(page), page.rect.height)
                else:
                    page_cache[page_no] = ([], 0.0)
            return page_cache[page_no]

        def backfill_cells(node: Node) -> Node:
            """Same authority split as prose, applied per table cell -- a cell is
            where a flattened exponent costs the most (it IS the limit value).

            Conservative by measurement: over 1172 real cells, the runs in a
            cell's region reconstruct identically for 66%, differ ONLY by
            super/subscript for ~1%, and differ STRUCTURALLY for ~20% -- the last
            group dominated by imprecise cell-region geometry (runs bleeding in
            from a neighbouring column, or a cell bbox not covering its own
            leading value), not by real transcription loss. So the runs are always
            recorded as evidence + a `parsers` candidate, `raw_text` is set only
            when the content provably matches, `text` is never overwritten, and a
            structural mismatch raises nothing (it would be a false-alarm
            generator, not a finding)."""
            out = []
            for cell in node.cells or []:
                if not cell.bbox or not cell.page or cell.runs:
                    out.append(cell)
                    continue
                placed, page_h = placed_for(cell.page)
                if not placed:
                    out.append(cell)
                    continue
                region = runs_mod.docling_bbox_to_topleft(cell.bbox, page_h)
                cell_runs = runs_mod.runs_in_region(placed, region)
                if not cell_runs:
                    out.append(cell)
                    continue
                raw = reconstruct_raw_text(cell_runs)
                update = {
                    "runs": cell_runs,
                    "parsers": {**cell.parsers, "pymupdf": raw, "docling": cell.text},
                }
                # Enrich ONLY when the runs actually carry vertical-alignment
                # information. Without this guard, any cell differing merely in
                # whitespace also qualifies (measured: 360 cells vs 14 real ones)
                # -- and the runs concatenation drops the space at an internal
                # line break ("industries. We" -> "industries.We"), so raw_text
                # would be strictly WORSE than text while now being what
                # parameters.py reads. No super/subscript => nothing to preserve.
                if (raw != cell.text
                        and any(r.vertical_align != "normal" for r in cell_runs)
                        and same_text_content(raw, cell.text)):
                    update["raw_text"] = raw
                out.append(cell.model_copy(update=update))
            return node.model_copy(update={"cells": out})

        def visit(node: Node) -> Node:
            children = [visit(c) for c in node.children]
            node = node.model_copy(update={"children": children})
            if node.type == "table" and node.cells:
                return backfill_cells(node)
            if node.type in _RUN_TEXT_TYPES and not node.runs:
                placed, page_h = placed_for(node.provenance.page)
                if placed:
                    region = runs_mod.docling_bbox_to_topleft(node.provenance.bbox, page_h)
                    region_runs = runs_mod.runs_in_region(placed, region)
                    if region_runs:
                        # PyMuPDF is the raw-text/runs authority (parser-consensus.md):
                        # raw_text is reconstructed FROM the runs (byte-exact, keeps
                        # the en-dash/±/superscript the content stream actually has),
                        # so run-integrity is consistent by construction. Docling's
                        # transcription is kept as a corroborator candidate in `parsers`
                        # (it flattens en-dash->hyphen etc.), not as raw_text.
                        raw = reconstruct_raw_text(region_runs)
                        node = node.model_copy(update={
                            "runs": region_runs,
                            "raw_text": raw,
                            "parsers": {**node.parsers, "pymupdf": raw, "docling": node.text},
                        })
            return node

        return [visit(n) for n in top_sections]
    finally:
        doc.close()


def _apply_table_geometry_consensus(root: Node, pdf_bytes: bytes) -> Node:
    """Three-parser table-grid consensus (parser-consensus.md): compare the
    Docling and pdfplumber geometries (the two genuine independent methods, with
    PyMuPDF word-grid as an approximate corroborator, `table_geometry.reconcile`)
    and quarantine a table whose grid the parsers don't agree on -- the
    merged-cell-collapse guard. Runs before the gates so a geometry quarantine is
    an admission finding. Records each parser's opinion in `quarantine_reason`
    for the audit trail; never rewrites the cells."""
    from app.pipeline import extract_pdfplumber, table_geometry

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        # pdfplumber grids are page-scoped; cache per page (0-based).
        pp_cache: dict[int, list] = {}

        def pp_for(page_no: int):
            if page_no not in pp_cache:
                pp_cache[page_no] = extract_pdfplumber.page_table_grids(pdf_bytes, page_no - 1)
            return pp_cache[page_no]

        def visit(node: Node) -> Node:
            node = node.model_copy(update={"children": [visit(c) for c in node.children]})
            if node.type != "table" or not node.cells or node.consensus == "quarantined":
                return node
            page_no = next((c.page for c in node.cells if c.page), node.provenance.page)
            if not (1 <= page_no <= doc.page_count):
                return node
            d = table_geometry.docling_grid(node)
            pp = pp_for(page_no)
            # pdfplumber's best-matching grid for this table (a page can hold
            # several); pick the one closest to Docling's row/col count.
            pp_best = min(pp, key=lambda g: abs(g.n_rows - d.n_rows) + abs(g.n_cols - d.n_cols),
                          default=None) if pp else None
            pm = table_geometry.pymupdf_grid_for_node(doc[page_no - 1], node)
            result = table_geometry.reconcile(d, pp_best, pm)
            if result.state == "unanimous":
                return node
            if result.state == "quarantined":
                reason = f"table geometry: {result.reason} candidates={result.candidates}"
                return node.model_copy(update={
                    "consensus": "quarantined",
                    "quarantine_reason": (reason if not node.quarantine_reason
                                          else f"{node.quarantine_reason}; {reason}"),
                    "review_required": True,
                    "review_reasons": node.review_reasons + ["table_geometry_disagreement"],
                })
            return node.model_copy(update={  # majority: admitted, corroborator noted
                "consensus": "majority",
                "review_reasons": node.review_reasons + ["table_geometry_corroborator_differs"],
            })

        return visit(root)
    finally:
        doc.close()


def _crop_region(doc: "fitz.Document", page_no: int,
                 bbox_bottomleft: tuple[float, float, float, float],
                 zoom: float = 3.0, pad: float = 4.0):
    """Rasterize one node's region to a PIL image for a vision engine. Docling
    bbox (bottom-left) -> top-left clip; None when the page is out of range."""
    import io as _io

    from PIL import Image

    from app.pipeline.runs import docling_bbox_to_topleft

    if not (1 <= page_no <= doc.page_count):
        return None
    page = doc[page_no - 1]
    x0, y0, x1, y1 = docling_bbox_to_topleft(bbox_bottomleft, page.rect.height)
    clip = fitz.Rect(x0 - pad, y0 - pad, x1 + pad, y1 + pad) & page.rect
    if clip.is_empty:
        return None
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
    return Image.open(_io.BytesIO(pix.tobytes("png")))


# The equation-lane corroborators, in deterministic priority order (SKILLS.md
# rule 5: disagreement resolution extends a priority table, never an ad-hoc
# call-site heuristic). Each is a module exposing available()/recognize_formula;
# each contributes an INDEPENDENT LaTeX candidate next to the Docling CodeFormula
# authority. Order is the tie-broken reporting order, not an authority claim --
# the authority is always Docling's stored `node.latex`.
def _equation_engines():
    from app.pipeline.engines import glm_ocr, mineru
    return [glm_ocr, mineru]


def _apply_equation_corroboration(root: Node, pdf_bytes: bytes) -> Node:
    """Equation-lane consensus (parser-consensus.md equation authority): every
    available formula engine (GLM-OCR, MinerU/UniMERNet) produces an independent
    LaTeX candidate next to Docling CodeFormula's. Comparison uses
    `canon_equation.eq_compare_form`, which folds transcription variants
    (\\text vs \\mathrm, $$ wrappers, \\tag numbers) but preserves genuine glyph
    differences (\\mathfrak vs \\mathrm) -- if ANY candidate disagrees with the
    authority the equation quarantines with ALL candidates kept
    (verification-rules.md: "flag the difference and let a human resolve it").
    No-op when no engine is available; never rewrites the stored LaTeX."""
    engines = [e for e in _equation_engines() if e.available()]
    if not engines:
        return root

    def has_equations(n: Node) -> bool:
        return n.type == "equation" or any(has_equations(c) for c in n.children)

    if not has_equations(root):
        return root

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        def visit(node: Node) -> Node:
            node = node.model_copy(update={"children": [visit(c) for c in node.children]})
            if node.type != "equation" or not node.latex:
                return node
            img = None
            new_candidates: dict[str, str] = {}
            for engine in engines:
                if engine.ENGINE_NAME in node.parsers:
                    continue  # already corroborated (idempotent re-run)
                if img is None:
                    img = _crop_region(doc, node.provenance.page, node.provenance.bbox)
                if img is None:
                    break
                candidate = engine.recognize_formula(img)
                if candidate is not None:
                    new_candidates[engine.ENGINE_NAME] = candidate
            if not new_candidates:
                return node
            node = node.model_copy(update={"parsers": {**node.parsers, **new_candidates}})
            # Disagreement iff any engine candidate differs from the authority
            # (Docling's stored LaTeX) after variant folding.
            auth_form = canon_equation.eq_compare_form(node.latex)
            dissenters = {name: cand for name, cand in new_candidates.items()
                          if canon_equation.eq_compare_form(cand) != auth_form}
            if not dissenters:
                return node  # corroborated -- stays unanimous
            detail = ", ".join(f"{name}={cand!r}" for name, cand in dissenters.items())
            return node.model_copy(update={
                "consensus": "quarantined",
                "quarantine_reason": (
                    f"equation engines disagree: docling_formula={node.latex!r} vs {detail}"),
                "review_required": True,
                "review_reasons": node.review_reasons + ["equation_engine_disagreement"],
            })

        return visit(root)
    finally:
        doc.close()


_OCR_PAGE_CLASSES = {"SCANNED", "UNCERTAIN"}


# The scanned-OCR text corroborators, in deterministic order. Each exposes
# available()/recognize_text and is registered in consensus.GENUINE_TEXT_PARSERS,
# so `_apply_text_consensus` votes over whatever they populate without a
# call-site change (SKILLS.md rule 5). GLM-OCR is default-on; Surya is opt-in.
def _ocr_text_engines(primary_ocr: str = ""):
    """Corroborators that are INDEPENDENT of the engine that produced the primary
    text. If Docling's own OCR engine is Surya (docling-surya), the Surya sidecar
    is dropped: the same model on both sides of a comparison is not a second
    opinion, it is one opinion counted twice. It would agree with itself, and
    N-version consensus would report false unanimity on exactly the scanned
    pages where the risk of silent transcription error is highest."""
    from app.pipeline.engines import glm_ocr, surya
    engines = [glm_ocr, surya]
    return [e for e in engines if e.ENGINE_NAME != primary_ocr]


def _backfill_ocr_candidates(root: Node, pdf_bytes: bytes,
                             page_classes: list[TriageResult]) -> Node:
    """OCR-lane candidates on SCANNED/UNCERTAIN pages (parser-consensus.md OCR
    authority + confidence ceiling). There is no digital text layer there, so
    the node text IS Docling's OCR transcription -- recorded under the engine
    that actually produced it as a genuine voter -- and the corroborator engines
    contribute independent second opinions; `_apply_text_consensus` then votes.
    Also enforces the spec's "any OCR-derived Parameter is quarantined by
    default": a scanned limit is exactly where digit confusables live.
    Born-digital pages are untouched ("skip work aggressively"); no-op when no
    engine is available."""
    primary_ocr = resolved_ocr_engine()
    engines = [e for e in _ocr_text_engines(primary_ocr) if e.available()]
    if primary_ocr != "rapidocr":
        log.info("OCR lane: primary=%s, corroborators=%s",
                 primary_ocr, [e.ENGINE_NAME for e in engines] or "none")

    scanned_pages = {i + 1 for i, tc in enumerate(page_classes)
                     if tc.page_class in _OCR_PAGE_CLASSES}
    if not scanned_pages:
        return root

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        def visit(node: Node) -> Node:
            node = node.model_copy(update={"children": [visit(c) for c in node.children]})
            if node.provenance.page not in scanned_pages or not (node.text or "").strip():
                return node
            if node.type in ("table", "figure"):
                return node  # cell text is handled by the table lanes
            parsers = dict(node.parsers)
            # Label the primary transcription with the engine Docling ACTUALLY
            # ran, not an assumed one -- see extract_docling._OCR_ENGINES.
            parsers.setdefault(primary_ocr, node.text)
            img = None
            for engine in engines:
                if engine.ENGINE_NAME in parsers:
                    continue
                if img is None:
                    img = _crop_region(doc, node.provenance.page, node.provenance.bbox)
                if img is None:
                    break
                candidate = engine.recognize_text(img)
                if candidate is not None:
                    parsers[engine.ENGINE_NAME] = candidate
            update: dict = {"parsers": parsers}
            if node.parameters and node.consensus != "quarantined":
                update.update({
                    "consensus": "quarantined",
                    "quarantine_reason": "OCR-derived parameter -- digit confusables live "
                                         "here; needs human confirmation",
                    "review_required": True,
                    "review_reasons": node.review_reasons + ["ocr_derived_parameter"],
                })
            return node.model_copy(update=update)

        return visit(root)
    finally:
        doc.close()


def _apply_text_consensus(root: Node) -> Node:
    """Set `consensus`/`quarantine_reason` from the genuine text transcribers in
    each node's `parsers` (consensus.reconcile_text). Only parsers in
    `GENUINE_TEXT_PARSERS` vote -- Docling's reflow-derived text is a recorded
    corroborator, not a voter (see consensus.GENUINE_TEXT_PARSERS), so with the
    current PyMuPDF-only transcriber set every born-digital node is trivially
    unanimous; it activates to real quarantine on scanned pages once the OCR
    engines (GLM-OCR, Surya) populate `parsers`. It never averages, resolves, or
    discards -- disagreement becomes a quarantine with all candidates kept."""
    from app.pipeline import consensus as _consensus
    from canonical_schema import is_normative

    def visit(node: Node) -> Node:
        node = node.model_copy(update={"children": [visit(c) for c in node.children]})
        voters = {p: t for p, t in node.parsers.items()
                  if p in _consensus.GENUINE_TEXT_PARSERS}
        # Need at least two genuine opinions for a vote; one (PyMuPDF) is
        # trivially unanimous and already the schema default.
        if len(voters) < 2 or node.consensus == "quarantined":
            return node
        # authority = highest-ranked genuine transcriber present (PyMuPDF on
        # born-digital regions; the OCR engines on scanned ones)
        authority = next(p for p in _consensus.TEXT_AUTHORITY_ORDER if p in voters)
        result = _consensus.reconcile_text(voters, authority=authority,
                                           normative=is_normative(node))
        if result.state == "unanimous":
            return node
        if result.state == "quarantined":
            return node.model_copy(update={
                "consensus": "quarantined",
                "quarantine_reason": (f"consensus: {result.reason}"
                                      if not node.quarantine_reason
                                      else f"{node.quarantine_reason}; consensus: {result.reason}"),
                "review_required": True,
                "review_reasons": node.review_reasons + ["consensus_dissent"],
            })
        # majority: admit at recorded reduced confidence, keep the dissent
        return node.model_copy(update={
            "consensus": "majority",
            "review_reasons": node.review_reasons + ["consensus_minority_dissent"],
        })

    return visit(root)


def _outcomes_by_gate(report) -> dict[str, dict[str, int]]:
    from collections import defaultdict
    out: dict[str, dict[str, int]] = defaultdict(lambda: {"quarantine": 0, "repair": 0})
    for o in report.outcomes:
        out[o.gate][o.verdict] = out[o.gate].get(o.verdict, 0) + 1
    return {g: dict(v) for g, v in out.items()}


def _synthetic_root_provenance() -> Provenance:
    # The document root isn't a Docling element -- it's synthesized here to
    # satisfy "no node without provenance" (AGENTS.md §1.9) while making clear
    # this bbox/page carries no positional meaning.
    return Provenance(page=1, bbox=(0.0, 0.0, 0.0, 0.0), parser="assemble",
                       model_version=PIPELINE_VERSION, confidence=1.0)


def assemble(pdf_bytes: bytes, source_sha256: str, ocr_enabled: bool = False) -> CanonicalEdition:
    page_classes = _triage_pages(pdf_bytes)
    # Auto-route to OCR if triage found a page that needs it -- an explicit
    # ocr_enabled=True from the caller always wins even if OCR is gated off.
    ocr_enabled = ocr_enabled or (_ocr_needed(page_classes) and _ocr_permitted())

    top_sections, page_confidence = extract_with_confidence(pdf_bytes, ocr_enabled=ocr_enabled)
    top_sections = caption_attach.attach_captions_by_proximity(top_sections)
    top_sections = [_apply_triage(n, page_classes) for n in top_sections]
    top_sections = lattice.reconcile(top_sections)
    top_sections = topology.merge_split_clause_numbers(top_sections)  # reunite two-column clauses
    top_sections = [topology.assign_clause_ids(n) for n in top_sections]
    top_sections = continuity.stitch(top_sections)
    top_sections = continuity.assign_header_paths(top_sections)
    top_sections = [nested_table.flag_nested_tables(n) for n in top_sections]  # fail-toward-review
    top_sections = _backfill_runs(top_sections, pdf_bytes)  # PyMuPDF runs (super/± authority)
    top_sections = [canon_units.annotate_node(n) for n in top_sections]  # {value,unit,condition}
    top_sections = [canon_equation.canonicalize_node(n) for n in top_sections]
    top_sections = [lang.annotate_node(n) for n in top_sections]  # NFC + per-node lang
    top_sections = [classify_type.annotate_node(n) for n in top_sections]  # closed CDM type
    top_sections = [parameters.annotate_node(n) for n in top_sections]  # Decimal Parameters
    top_sections = classify_document(top_sections, _rulepack())
    # Reconstruct the clause hierarchy last, so section-role classification
    # still runs over the flat top-level list (as designed) -- nesting only
    # re-parents the already-classified section nodes by clause number.
    # hoist first: clause nodes Docling buried under the WRONG clause (3.24
    # inside 3.23) come back to the flat list so nesting places them by number.
    top_sections = topology.hoist_misnested_clauses(top_sections)
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

    # computes_limit (verification-rules.md): an equation whose defined symbol a
    # normative sibling depends on -- conservative, same-subtree only, runs after
    # nesting so "same section" is the real clause subtree.
    root = canon_equation.annotate_computes_limit(root)

    # translation_group_id (canonical-model.md §Multilingual): link -- never
    # merge -- language instances of the same clause. No-op on monolingual docs.
    root = lang.link_translation_groups(root)

    # Model-backed corroboration (parser-consensus.md engines, now live):
    # independent LaTeX candidates per equation (GLM-OCR + MinerU/UniMERNet) and
    # OCR text candidates on scanned pages (GLM-OCR + Surya). All no-op cleanly
    # when an engine is unavailable/gated (GLM-OCR default-on; MinerU/Surya opt-in).
    root = _apply_equation_corroboration(root, pdf_bytes)
    root = _backfill_ocr_candidates(root, pdf_bytes, page_classes)

    # Three-parser table-grid consensus (parser-consensus.md): Docling +
    # pdfplumber geometry must agree or the table quarantines (merged-cell
    # collapse is the single most expensive silent error).
    root = _apply_table_geometry_consensus(root, pdf_bytes)

    # N-version text consensus (parser-consensus.md): compare the genuine
    # per-region transcribers recorded in Node.parsers and set consensus /
    # quarantine per the disagreement branch. With only PyMuPDF today this is
    # trivially unanimous; it activates to real quarantine when a genuine
    # alternate transcriber (MinerU/Surya) is registered. Runs before the gates
    # so a consensus quarantine is visible to the admission check.
    root = _apply_text_consensus(root)

    # Verification gates: the admission checks (verification-rules.md). run_all
    # threads the repaired tree through all 8 gates in order and marks any
    # object that fails an invariant `consensus="quarantined"` with a reason --
    # extraction never discards, it quarantines into a review queue.
    gate_report = gates.run_all(root)
    root = gate_report.root

    # Content-addressed identity (canonical-model.md §Identity): the FINAL pass,
    # so every clause_id is assigned and every id-reference is remapped through
    # the same old->new map. Replaces the random uuid ids with
    # standard_id#section_path / doc_id#hash so ids are stable across runs (and,
    # when numbering is stable, across editions -- the premise of ID-first
    # alignment). doc_id is the source hash; standard_id is derived best-effort
    # from a title-page designation (None on a title-page-less fragment).
    standard_id = identity.derive_standard_id(root)
    root = identity.restamp_ids(root, doc_id=source_sha256[:16], standard_id=standard_id)

    lang_primary = lang.dominant_lang(root)

    ownership = _ownership()

    primary_ocr = resolved_ocr_engine()

    def _engine_for(content_type: str, page_class: str) -> str:
        engine = ownership.engine_for(content_type, page_class)
        # OWNERSHIP names the OCR lane's engine generically ("rapidocr") whether
        # or not OCR ran, and whichever engine is configured; report what
        # actually happened. If the triage-driven route didn't enable OCR (gated
        # off, or the caller didn't ask), text came from Docling's best-effort
        # digital-layer read, not OCR. If it did run, name the real engine.
        if engine == "rapidocr":
            return primary_ocr if ocr_enabled else "docling"
        return engine

    engine_by_page = {
        str(i + 1): {
            content_type: _engine_for(content_type, tc.page_class)
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
            # Docling's own per-page confidence (layout/parse/table/ocr + grade),
            # DIAGNOSTIC ONLY -- nothing branches on it. Measured against
            # accuracy_check ground truth and found unusable as a gate
            # (precision 0.26-0.31; r=+0.009 with page coverage) -- see the
            # rationale on `_DIGITAL_TEXT_CONFIDENCE` in extract_docling.py. Kept
            # because it's free (Docling already computes it) and useful in
            # aggregate for spotting a model-upgrade regression across the corpus.
            "page_confidence": page_confidence,
            # The admission review queue: how many objects each gate quarantined
            # or repaired. A hard document with zero quarantines is itself a
            # symptom (verification-rules.md "Quarantine is not failure").
            "gates": {
                "quarantined": len(gate_report.quarantined),
                "repaired": len(gate_report.repaired),
                "by_gate": _outcomes_by_gate(gate_report),
            },
        },
    )
