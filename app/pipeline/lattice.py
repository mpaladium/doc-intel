"""lattice.reconcile — the layout owner alone defines region boundaries.

Docling is the sole geometry owner in this iteration (AGENTS.md §1.10): every
region boundary in the extracted tree already comes from Docling, and no other
engine is wired in yet to fill content inside those boundaries. So reconciliation
is a pass-through today — this function exists as the single seam where a second
engine's fill-content would be merged in later, per OWNERSHIP, rather than a
call-site special case.
"""

from __future__ import annotations

from canonical_schema import Node


def reconcile(nodes: list[Node]) -> list[Node]:
    """Identity today. Once a second content-filling engine is wired in (e.g. MinerU
    for equations), this is where its output gets merged into Docling's region
    boundaries -- never by renegotiating boxes via IoU (AGENTS.md §1.10)."""
    return nodes
