"""caption_attach -- proximity-based re-parenting for captions Docling didn't
link via `.captions` (see extract_docling.py's claimed/unclaimed distinction;
empirically the common case -- Docling often fails to populate that
back-reference even for an adjacent caption). An "unclaimed" caption still
becomes a correctly-typed node (never lost), but lands as a sibling of its
table/figure rather than a child of it.

This is a deterministic post-pass over the assembled tree: an unclaimed
caption immediately adjacent (previous OR next sibling, same page) to exactly
one table/figure becomes that table/figure's child. If the caption sits
between two table/figure siblings, or has no adjacent table/figure at all, it
stays where it is -- fail-safe, never guesses (AGENTS.md §1.6).
"""

from __future__ import annotations

from canonical_schema import Node

_ATTACHABLE = ("table", "figure")


def _same_page(a: Node, b: Node) -> bool:
    return a.provenance.page == b.provenance.page


def attach_captions_by_proximity(nodes: list[Node]) -> list[Node]:
    """Depth-first, single pass per sibling list."""
    processed = [n.model_copy(update={"children": attach_captions_by_proximity(n.children)})
                 for n in nodes]

    result: list[Node] = []
    i = 0
    while i < len(processed):
        node = processed[i]
        if node.type != "caption":
            result.append(node)
            i += 1
            continue

        prev = result[-1] if result and result[-1].type in _ATTACHABLE else None
        nxt = processed[i + 1] if i + 1 < len(processed) and processed[i + 1].type in _ATTACHABLE else None
        prev_ok = prev is not None and _same_page(prev, node)
        next_ok = nxt is not None and _same_page(nxt, node)

        if prev_ok and not next_ok:
            result[-1] = prev.model_copy(update={"children": list(prev.children) + [node]})
            i += 1
            continue
        if next_ok and not prev_ok:
            result.append(nxt.model_copy(update={"children": [node] + list(nxt.children)}))
            i += 2
            continue

        # ambiguous (both/neither adjacent) -- leave as a sibling, unresolved.
        result.append(node)
        i += 1

    return result
