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

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Shared primitives
# --------------------------------------------------------------------------- #
class Provenance(BaseModel):
    page: int
    bbox: tuple[float, float, float, float]
    parser: str                      # "docling" | "mineru" | "surya" | "digital_layer"
    model_version: str
    confidence: float = Field(ge=0.0, le=1.0)


class Cell(BaseModel):
    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    header_path: list[str] = Field(default_factory=list)  # diff identity, not grid position
    text: str


NodeType = Literal[
    "section", "heading", "paragraph", "table",
    "equation", "figure", "note", "list_item",
]

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


class Node(BaseModel):
    id: str                          # stable WITHIN an edition
    type: NodeType
    clause_id: Optional[str] = None  # normalized "4.2.3.1" / "Annex ZA"
    lang: Optional[str] = None       # BCP-47
    text: Optional[str] = None       # NFC-normalized
    latex: Optional[str] = None      # canonicalized
    cells: Optional[list[Cell]] = None
    children: list["Node"] = Field(default_factory=list)
    provenance: Provenance
    review_required: bool = False    # true if ANY reason below applies
    review_reasons: list[str] = Field(default_factory=list)  # e.g. "low_extraction_confidence",
                                      # "ambiguous_section_role"

    # Section-role classification (see section_role_classifier.py). Defaults are the
    # SAFE defaults: every node is normative and compliance-relevant until a classifier
    # actively, confidently says otherwise. Nothing is ever deleted — only flagged.
    section_role: SectionRole = "normative"
    compliance_relevant: bool = True
    role_confidence: float = Field(default=1.0, ge=0.0, le=1.0)


Node.model_rebuild()


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
