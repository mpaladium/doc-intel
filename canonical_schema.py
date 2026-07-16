"""
canonical_schema.py
====================
Shared contract between `ingestion-engine` (Goal 1) and `comparison-engine` (Goal 2).

ingestion-engine produces CanonicalEdition (+ page images, stored separately).
comparison-engine reads CanonicalEdition, derives Chunks + GraphEdges, and produces
ChangeSet. Neither service reaches across this boundary: comparison-engine never
touches a PdfPage or an extraction engine; ingestion-engine never imports Chunk,
GraphEdge, Change, or ChangeSet.

Design notes:
  - `Node` carries `Provenance` — no node without it (see ARCHITECTURE.md §1.1).
  - `Chunk` is a re-cut of the same tree at two resolutions (leaf, rollup); it does not
    duplicate extraction, only re-groups already-extracted nodes.
  - `discrepancy_score` on `Change` is the ONLY "human review" signal in the system.
    It drives UI sort order. Nothing in this schema represents a review workflow state
    (no "pending", "approved", etc.) — see ARCHITECTURE.md §0 on why that was dropped.
  - Everything embeddable (Chunk) carries enough context (header_path, breadcrumb,
    clause_id) to be meaningfully compared without re-fetching the parent edition.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Shared primitives
# --------------------------------------------------------------------------- #
class Provenance(BaseModel):
    page: int
    bbox: tuple[float, float, float, float]
    parser: str                      # "docling" | "mineru" | "surya" | "digital_layer" | "pymupdf"
    model_version: str
    confidence: float = Field(ge=0.0, le=1.0)


VerticalAlign = Literal["normal", "superscript", "subscript"]


class Run(BaseModel):
    """A single formatting run (docs/references/canonical-model.md §Text runs).
    `raw_text` is a lossy projection that cannot audit itself: '10⁻³ V/m'
    flattens to '10-3 V/m' *before the string exists*, so any check written
    against raw_text interrogates a witness that already forgot. Font/baseline
    metadata is the only layer that can catch it, and PyMuPDF exposes it per
    character where nothing else in the stack does. Never merge adjacent runs
    with different `vertical_align`, `size`, or `font` -- that merge is exactly
    the operation that destroys the signal."""
    text: str
    font: str
    size: float
    baseline_offset: float = 0.0             # negative=subscript, positive=superscript
    bold: bool = False
    italic: bool = False
    vertical_align: VerticalAlign = "normal"
    bbox: Optional[tuple[float, float, float, float]] = None


# Unicode super/subscript maps for reconstructing raw_text from runs (the
# reconstruction the run-integrity gate checks). Only digits + a few operators
# have codepoints; anything without one falls back to the baseline glyph.
_SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")


def reconstruct_raw_text(runs: list[Run]) -> str:
    """Concatenate run text, emitting the Unicode super/subscript codepoint
    wherever a run is not on the baseline. This is what makes the units gate
    meaningful instead of decorative -- if this doesn't equal the stored
    raw_text, one of the two is lying and the object is quarantined."""
    out: list[str] = []
    for r in runs:
        if r.vertical_align == "superscript":
            out.append(r.text.translate(_SUPERSCRIPT))
        elif r.vertical_align == "subscript":
            out.append(r.text.translate(_SUBSCRIPT))
        else:
            out.append(r.text)
    return "".join(out)


class Quantity(BaseModel):
    """DEPRECATED in favor of `Parameter` (CDM v2). Retained so existing
    canon.units output stays valid during migration. A cell's surface value;
    `Parameter` is the richer, comparison-grade replacement (Decimal value,
    comparator, tolerance)."""
    value: str
    unit: Optional[str] = None
    condition: Optional[str] = None


# --------------------------------------------------------------------------- #
# Parameter -- the highest-value extraction (canonical-model.md §Parameter).
# Most consequential compliance changes are a number moving.
# --------------------------------------------------------------------------- #
Comparator = Literal["gte", "lte", "eq", "range"]
ToleranceType = Literal["symmetric", "asymmetric", "relative"]


class Tolerance(BaseModel):
    type: ToleranceType
    value: Decimal
    value_upper: Optional[Decimal] = None    # for asymmetric tolerances
    unit: Optional[str] = None


class Parameter(BaseModel):
    """A compliance value: `Decimal` never float (a limit is not a place for
    binary rounding), a required comparator (missing → quarantine, never
    default `eq`: "shall be 10 V/m" and "at least 10 V/m" differ), and its
    condition (a limit is meaningless without its frequency band)."""
    name: str
    quantity_kind: Optional[str] = None      # controlled vocab, e.g. "electric_field"
    value: Optional[Decimal] = None          # None only for a pure-range parameter
    unit: Optional[str] = None               # canonical; raw string kept on parent object
    raw_unit: Optional[str] = None
    tolerance: Optional[Tolerance] = None
    comparator: Optional[Comparator] = None  # missing → the parameter is quarantined
    range: Optional[tuple[Decimal, Decimal]] = None
    condition: Optional[str] = None          # e.g. "80 MHz - 1 GHz"
    source_object_id: Optional[str] = None
    bbox: Optional[tuple[float, float, float, float]] = None


class Cell(BaseModel):
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    header_path: list[str] = Field(default_factory=list)  # diff identity, not grid position
    is_column_header: bool = False   # from the extractor's own header detection; drives
                                     # multi-row header_path lineage (continuity.header_path)
    text: str
    # Every parser's candidate for this cell's text (parser-consensus.md: a
    # merged-cell collapse in a limit table is the single most expensive silent
    # error, so table geometry requires all three parsers to agree). Empty for
    # single-parser cells.
    parsers: dict[str, Optional[str]] = Field(default_factory=dict)
    # Cell-level provenance (ARCHITECTURE.md §1.9). A table is the one element
    # whose sub-parts can span pages: continuity.stitch merges a continuation
    # table onto the previous page's node, so the node's single provenance.page
    # is NOT the page of every cell. Each cell therefore carries its own source
    # page/bbox (Docling bottom-left origin, same convention as Provenance.bbox),
    # populated at extraction and preserved through the stitch merge. Optional so
    # hand-constructed cells / older data stay valid.
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None
    # Structured {value, unit, condition} for a cell that holds a measurement,
    # populated by canon.units. None for label/prose cells.
    quantity: Optional[Quantity] = None


NodeType = Literal[
    "section", "heading", "paragraph", "table",
    "equation", "figure", "note", "list_item", "caption",
]
# A "caption" node is always a child of the table/figure it describes --
# parent-of-caption is expressed by tree position alone, not a back-link
# field, consistent with how this schema avoids redundant cross-references
# elsewhere (e.g. Cell.header_path names columns rather than pointing at a
# header Cell by id).
#
# `NodeType` above is the STRUCTURAL type (what the extraction tree is made of).
# `CDMType` below is the closed normative-role type set from
# canonical-model.md -- assigned by classify_type on top of the structural
# type (a "paragraph" carrying a `shall` becomes cdm_type "Requirement"). The
# set is closed on purpose: content that doesn't fit is a Paragraph with a role
# annotation, never a new ad-hoc type.
CDMType = Literal[
    "Document", "Section", "Paragraph", "Requirement", "Recommendation",
    "Permission", "Procedure", "Step", "AcceptanceCriteria", "Table", "Figure",
    "Equation", "Note", "Warning", "Reference", "NormativeReference",
    "Definition", "Scope", "Exception",
]

# Which CDM types are normative (parser-consensus.md: normative objects require
# UNANIMITY -- a wording dissent on a limit is stop-the-line, on a note is
# tolerable). An object also counts as normative if it carries any Parameter.
_NORMATIVE_CDM_TYPES = frozenset({
    "Requirement", "Warning", "AcceptanceCriteria", "Exception", "Scope",
    "Procedure", "Step", "NormativeReference",
})

ConsensusState = Literal["unanimous", "majority", "quarantined"]

SectionRole = Literal[
    "normative",          # body content: requirements, tables, definitions, normative annexes
    "title_page",
    "toc",
    "list_of_figures",
    "list_of_tables",
    "foreword",
    "preface",             # includes "Introduction"-style informative preambles — see
                           # section_role_classifier.py docstring on why this role is
                           # held to a stricter confidence bar than the others.
    "index",
    "other_frontmatter",   # Reserved for REVIEWER-assigned classification via
                           # ui.correction_capture. The automatic classifier
                           # (section_role_classifier.py) never assigns this role
                           # itself -- an ambiguous automatic result stays "normative"
                           # with review_required=True instead (fail-toward-include).
                           # A human confirming "yes, this really is unidentified
                           # front matter" is what promotes a node to this role.
]


XRefKind = Literal["clause", "annex", "table", "figure", "section", "external"]


class XRef(BaseModel):
    """A cross-reference found in a node's text ("see 4.2.3", "Table 22",
    "Anhang ZA"). Within-edition annotation produced by `topology`/`xref`, not a
    comparison graph edge (that's comparison-engine's REFERENCES, ARCHITECTURE.md
    §3.2). `target_clause_id` is set when the reference resolves to a clause/annex
    that actually exists in this edition; unresolved/table/figure refs keep it
    None but still record the reference so nothing is silently dropped."""
    kind: XRefKind
    text: str                              # matched surface, e.g. "4.2.3", "Table 22"
    target_clause_id: Optional[str] = None  # resolved within-edition, if present


class Node(BaseModel):
    id: str                          # stable WITHIN an edition
    type: NodeType                   # STRUCTURAL type
    cdm_type: Optional[CDMType] = None  # normative-role type (classify_type); None until assigned
    clause_id: Optional[str] = None  # normalized "4.2.3.1" / "Annex ZA"
    lang: Optional[str] = None       # BCP-47, detected per object not per document
    translation_group_id: Optional[str] = None  # links DE/EN instances of the same object
    # `text` is what the pipeline reads (NFC-normalized). `raw_text` is the
    # IMMUTABLE byte-exact string from the authoritative parser (== text on the
    # single-parser path); `normalized_text` is the additive comparison form.
    # `runs` is REQUIRED for a real audit -- raw_text alone is lossy (a check
    # against it interrogates a witness that already forgot the superscript).
    text: Optional[str] = None
    raw_text: Optional[str] = None
    normalized_text: Optional[str] = None
    runs: list[Run] = Field(default_factory=list)
    latex: Optional[str] = None      # canonicalized (Equation nodes)
    cells: Optional[list[Cell]] = None
    parameters: list[Parameter] = Field(default_factory=list)  # compliance values
    xrefs: list[XRef] = Field(default_factory=list)  # cross-references in this node's text
    children: list["Node"] = Field(default_factory=list)
    provenance: Provenance

    # -- N-version consensus (parser-consensus.md). Single-parser output is
    # trivially "unanimous"; the consensus engine sets these when >1 parser
    # produced a candidate. `parsers` keeps EVERY candidate incl. the losers so
    # "why does the report say 10 V/m" is answerable three years later.
    parsers: dict[str, Optional[str]] = Field(default_factory=dict)
    consensus: ConsensusState = "unanimous"
    quarantine_reason: Optional[str] = None

    # -- Table-specific (only meaningful when type == "table") --
    header_rows: Optional[int] = None
    continues_from: Optional[str] = None   # multi-page stitching, prev fragment id
    continues_to: Optional[str] = None
    row_scopes: list[str] = Field(default_factory=list)  # one embedding target per row-scope

    # -- Equation-specific (only meaningful when type == "equation") --
    mathml: Optional[str] = None
    rendered_text: Optional[str] = None    # what a flattening parser produced -- diagnosis only
    defines: Optional[str] = None          # LHS symbol, e.g. "E"
    symbol_table: dict = Field(default_factory=dict)  # {sym: {quantity_kind, unit}}
    computes_limit: bool = False           # does a Requirement's parameter depend on this?

    review_required: bool = False    # true if ANY reason below applies
    review_reasons: list[str] = Field(default_factory=list)  # e.g. "low_extraction_confidence"
    repairs: list[dict] = Field(default_factory=list)  # gate repairs (auditable or it's corruption)

    # Section-role classification (see section_role_classifier.py). Defaults are the
    # SAFE defaults: every node is normative and compliance-relevant until a classifier
    # actively, confidently says otherwise. Nothing is ever deleted — only flagged.
    section_role: SectionRole = "normative"
    compliance_relevant: bool = True
    role_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


Node.model_rebuild()


def is_normative(node: Node) -> bool:
    """A node is normative if its CDM type is in the normative set OR it carries
    any Parameter (parser-consensus.md: any object carrying a Parameter requires
    unanimity). Normative objects that aren't unanimous are quarantined."""
    return (node.cdm_type in _NORMATIVE_CDM_TYPES) or bool(node.parameters)


def make_object_id(doc_id: str, section_path: Optional[list[str]], raw_text: Optional[str],
                   standard_id: Optional[str] = None) -> str:
    """Identity scheme (canonical-model.md §Identity): section-path IDs are
    stable across editions when numbering is stable (what makes ID-first
    alignment cheap); unnumbered content falls back to a content hash."""
    import hashlib

    if section_path:
        prefix = standard_id or doc_id
        return f"{prefix}#{'.'.join(section_path)}"
    digest = hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()[:12]
    return f"{doc_id}#{digest}"


def iter_chunkable_nodes(node: Node, _ancestor_relevant: bool = True):
    """Depth-first traversal yielding only nodes NOT under a compliance_relevant=False
    ancestor (inclusive: a node itself flagged compliance_relevant=False is also
    skipped, along with everything beneath it). THIS is the concrete enforcement point
    for front/back-matter exclusion described in ARCHITECTURE.md §3.1 and §Ingestion
    pipeline -- `chunk.leaf` / `chunk.rollup` in comparison-engine must traverse a
    CanonicalEdition via this function, not a raw tree walk, or the exclusion flags set
    by `classify.section_role` have no effect.

    Excluded content is skipped for chunking only -- it is never removed from the
    Node tree itself, so it remains visible (with its role/confidence/review_reasons)
    to the verification UI and to full-text search over the CanonicalEdition."""
    relevant = _ancestor_relevant and node.compliance_relevant
    if relevant:
        yield node
    for child in node.children:
        yield from iter_chunkable_nodes(child, _ancestor_relevant=relevant)


class CanonicalEdition(BaseModel):
    edition_id: str
    source_sha256: str
    schema_version: str
    lang_primary: Optional[str] = None
    root: Node
    pipeline_provenance: dict = Field(default_factory=dict)  # parser/model versions used


# --------------------------------------------------------------------------- #
# Goal 2 additions — chunking
# --------------------------------------------------------------------------- #
ChunkResolution = Literal["leaf", "rollup"]


class Chunk(BaseModel):
    """A re-cut of the CanonicalEdition tree at embeddable granularity.
    Does not re-extract; carries enough context to be compared standalone."""
    id: str                                  # stable within an edition
    edition_id: str
    resolution: ChunkResolution
    node_ids: list[str]                      # source Node.id(s) this chunk aggregates
    clause_id: Optional[str] = None
    breadcrumb: list[str] = Field(default_factory=list)   # e.g. ["4", "4.2", "4.2.3"]
    header_path: Optional[list[str]] = None  # populated only for table-cell-group chunks
    text: str                                # flattened, embeddable text (incl. rendered LaTeX)
    lang: Optional[str] = None
    confidence: float = Field(ge=0.0, le=1.0)  # min/avg of constituent node confidences
    embedding: Optional[list[float]] = None    # populated by vector.embed; stored in Qdrant


# --------------------------------------------------------------------------- #
# Goal 2 additions — knowledge graph
# --------------------------------------------------------------------------- #
EdgeType = Literal["parent_of", "references", "contains_cell", "aligned_to"]


class GraphEdge(BaseModel):
    """Typed edge between chunks (or nodes, for contains_cell). Stored as Neo4j
    relationships, traversed via Cypher. `aligned_to` is the only cross-edition edge
    type and is what comparison-engine writes as its comparison output — see
    `neo4j_aligned_to_props` below, which writes it directly from a `Change`."""
    id: str
    type: EdgeType
    src_id: str
    dst_id: str
    src_edition_id: str
    dst_edition_id: str              # == src_edition_id for parent_of/references/contains_cell
    weight: Optional[float] = None   # e.g. vector similarity, for aligned_to edges
    method: Optional[str] = None     # "structural" | "vector" | "graph_rerank"


def to_cypher_rel_type(edge_type: EdgeType) -> str:
    """Neo4j convention is UPPER_SNAKE relationship types; the Python-side EdgeType
    stays lower_snake for normal Python ergonomics. This is the single place that
    translates between the two -- never hardcode the uppercase form elsewhere."""
    return edge_type.upper()


# --------------------------------------------------------------------------- #
# Goal 2 additions — comparison output
# --------------------------------------------------------------------------- #
ChangeKind = Literal["added", "removed", "modified", "moved", "unchanged"]
ChangeScope = Literal["clause", "cell", "equation", "text"]


class DiscrepancyFactors(BaseModel):
    """The components of the discrepancy score, kept explicit (not collapsed into a
    single opaque float) so the UI can explain *why* something was flagged."""
    match_confidence: float = Field(ge=0.0, le=1.0)
    vector_similarity_margin: Optional[float] = None   # best vs. second-best candidate
    extraction_confidence_gap: float = Field(ge=0.0, le=1.0)  # |conf_left - conf_right|
    structural_ambiguity: bool = False                 # e.g. renumbering detected nearby


class Change(BaseModel):
    kind: ChangeKind
    scope: ChangeScope
    left_chunk_id: Optional[str] = None
    right_chunk_id: Optional[str] = None
    left_ref: Optional[str] = None    # clause_id / header_path string, for display
    right_ref: Optional[str] = None
    factors: DiscrepancyFactors
    discrepancy_score: float = Field(ge=0.0, le=1.0)   # THE flag; drives UI sort order
    explanation: Optional[str] = None                  # advisory, filled later if at all


class ChangeSet(BaseModel):
    left_edition_id: str
    right_edition_id: str
    changes: list[Change]

    def sorted_by_discrepancy(self, descending: bool = True) -> list[Change]:
        """Convenience for the UI: the entire 'review' mechanism is this sort."""
        return sorted(self.changes, key=lambda c: c.discrepancy_score, reverse=descending)


# --------------------------------------------------------------------------- #
# Storage adapters — comparison-engine only. ingestion-engine never imports this
# section (it has no Neo4j/Qdrant dependency; see ARCHITECTURE.md section 0 on statelessness).
#
# Source-of-truth rule (TECHSTACK.md): Neo4j holds Chunk content + all edges.
# Qdrant holds ONLY the vector + a thin payload sufficient to filter candidates -
# never Chunk.text or anything else Neo4j already has. If Qdrant's index is lost,
# `qdrant_point` can regenerate every point by re-reading Chunk nodes from Neo4j
# and re-embedding; nothing is lost that isn't independently recoverable.
# --------------------------------------------------------------------------- #
def neo4j_chunk_props(chunk: "Chunk") -> dict:
    """Chunk -> Neo4j (:Chunk) node properties. Everything a reader needs, in one place."""
    return {
        "id": chunk.id,
        "edition_id": chunk.edition_id,
        "resolution": chunk.resolution,
        "clause_id": chunk.clause_id,
        "breadcrumb": chunk.breadcrumb,
        "header_path": chunk.header_path,
        "text": chunk.text,
        "lang": chunk.lang,
        "confidence": chunk.confidence,
        "node_ids": chunk.node_ids,
    }
    # Deliberately excludes `embedding` -- that lives only in Qdrant.


def neo4j_edge_props(edge: GraphEdge) -> dict:
    """GraphEdge -> Neo4j relationship properties, for the structural edge types
    (PARENT_OF / REFERENCES / CONTAINS_CELL). Use with `to_cypher_rel_type(edge.type)`
    for the relationship label. ALIGNED_TO is written via `neo4j_aligned_to_props`
    directly from a `Change` instead -- it carries comparison-specific fields that
    don't belong on a generic edge."""
    return {"id": edge.id, "weight": edge.weight, "method": edge.method}


def neo4j_aligned_to_props(change: "Change") -> dict:
    """Change -> Neo4j ALIGNED_TO relationship properties. Writing this relationship
    IS emitting the ChangeSet (see ARCHITECTURE.md section 3.2) -- no separate table exists."""
    return {
        "kind": change.kind,
        "scope": change.scope,
        "left_ref": change.left_ref,
        "right_ref": change.right_ref,
        "discrepancy_score": change.discrepancy_score,
        "match_confidence": change.factors.match_confidence,
        "vector_similarity_margin": change.factors.vector_similarity_margin,
        "extraction_confidence_gap": change.factors.extraction_confidence_gap,
        "structural_ambiguity": change.factors.structural_ambiguity,
        "explanation": change.explanation,   # advisory field; never read to compute anything
    }


def qdrant_point(chunk: "Chunk") -> dict:
    """Chunk -> Qdrant point. Payload is intentionally thin (filter-only fields) --
    see the source-of-truth rule above. Raises if embedding hasn't been computed yet."""
    if chunk.embedding is None:
        raise ValueError(f"Chunk {chunk.id} has no embedding; run vector.embed first")
    return {
        "id": chunk.id,
        "vector": chunk.embedding,
        "payload": {
            "chunk_id": chunk.id,
            "edition_id": chunk.edition_id,
            "resolution": chunk.resolution,
            "clause_id": chunk.clause_id,
        },
    }
