"""Gate 1 (spec §"header/footer suppression"): text in the header/footer band,
repeating on >=3 pages, does not appear inside body objects.

Detects running headers injected mid-requirement, splitting a sentence across a
page break. Repairable when the match is EXACT (the header text is byte-
identical to the header on other pages) -- deletion is then uniquely
determined. A "header" that varies per page might be a section title, which is
content, so it never becomes a candidate and is never stripped.

Runs first because it cleans the text field the later text gates read.
"""

from __future__ import annotations

from collections import defaultdict

from canonical_schema import Node
from app.pipeline.gates import GateReport, Outcome, _iter, repair

_MIN_REPEAT_PAGES = 3          # "repeating on >=3 pages"
_MAX_HEADER_WORDS = 12         # a running header is short; a repeated body line isn't


def _running_header_lines(root: Node) -> set[str]:
    """Lines that recur verbatim on >=3 distinct pages -- the running-header/
    footer set. Keyed on the exact stripped line so a per-page-varying header
    (a section title) never qualifies."""
    line_pages: dict[str, set[int]] = defaultdict(set)
    for n in _iter(root):
        text = n.text or ""
        page = n.provenance.page
        for line in text.splitlines():
            s = line.strip()
            if s and len(s.split()) <= _MAX_HEADER_WORDS:
                line_pages[s].add(page)
    return {line for line, pages in line_pages.items() if len(pages) >= _MIN_REPEAT_PAGES}


def check(root: Node) -> GateReport:
    headers = _running_header_lines(root)
    outcomes: list[Outcome] = []
    if not headers:
        return GateReport(root=root, outcomes=outcomes)

    def visit(node: Node) -> Node:
        node = node.model_copy(update={"children": [visit(c) for c in node.children]})
        text = node.text
        if not text or "\n" not in text:
            return node  # a header standing alone as its own node is fine; only
                          # a header EMBEDDED in a multi-line body object is injection
        lines = text.splitlines()
        kept = [ln for ln in lines if ln.strip() not in headers]
        if len(kept) != len(lines) and any(ln.strip() for ln in kept):
            removed = [ln.strip() for ln in lines if ln.strip() in headers]
            node, out = repair(
                node, "header_footer",
                change={"removed_lines": removed, "before": text},
                reason=f"stripped running header(s) injected mid-body: {removed}",
                text="\n".join(kept))
            outcomes.append(out)
        return node

    return GateReport(root=visit(root), outcomes=outcomes)
