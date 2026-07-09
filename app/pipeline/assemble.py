"""edition.assemble -- emits the final CanonicalEdition (SKILLS.md).

Wires together: extract -> lattice.reconcile -> topology.clauses ->
continuity.stitch/header_path -> classify.section_role -> a single root Node
carrying pipeline_provenance for cross-edition compatibility checks
(ARCHITECTURE.md §1's pipeline-version guardrail).

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

from canonical_schema import CanonicalEdition, Node, Provenance
from app.pipeline import continuity, lattice, topology
from app.pipeline.extract_docling import DOCLING_VERSION, extract
from app.pipeline.section_role_classifier import ClassificationContext, RolePack, classify_document
from app.version import PIPELINE_VERSION, SCHEMA_VERSION

RULEPACK_PATH = Path(__file__).parents[2] / "rulepacks" / "section_roles.yaml"


@lru_cache(maxsize=1)
def _rulepack() -> RolePack:
    return RolePack.load(RULEPACK_PATH)


def _synthetic_root_provenance() -> Provenance:
    # The document root isn't a Docling element -- it's synthesized here to
    # satisfy "no node without provenance" (AGENTS.md §1.9) while making clear
    # this bbox/page carries no positional meaning.
    return Provenance(page=1, bbox=(0.0, 0.0, 0.0, 0.0), parser="assemble",
                       model_version=PIPELINE_VERSION, confidence=1.0)


def assemble(pdf_bytes: bytes, source_sha256: str, ocr_enabled: bool = False) -> CanonicalEdition:
    top_sections = extract(pdf_bytes, ocr_enabled=ocr_enabled)
    top_sections = lattice.reconcile(top_sections)
    top_sections = [topology.assign_clause_ids(n) for n in top_sections]
    top_sections = continuity.stitch(top_sections)
    top_sections = continuity.assign_header_paths(top_sections)
    top_sections = classify_document(top_sections, _rulepack())

    root = Node(
        id=uuid.uuid4().hex[:12],
        type="section",
        text=None,
        children=top_sections,
        provenance=_synthetic_root_provenance(),
    )

    return CanonicalEdition(
        edition_id=f"{source_sha256}+{PIPELINE_VERSION}",
        source_sha256=source_sha256,
        schema_version=SCHEMA_VERSION,
        lang_primary=None,  # lang.detect (fastText) not wired in this iteration
        root=root,
        pipeline_provenance={
            "pipeline_version": PIPELINE_VERSION,
            "docling_version": DOCLING_VERSION,
            "ocr_enabled": ocr_enabled,
        },
    )
