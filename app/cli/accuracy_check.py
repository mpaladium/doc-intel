"""Per-page factual-accuracy checker: samples N random PDFs, compares each
extracted CanonicalEdition against the source PDF text layer, and reports
faithfulness per page + per component (see app/cli/accuracy.py for the
measurement design). Reuses the eval sample-dir resolution and the artifact
store (falls back to assemble() if a doc isn't cached).

Usage:
    uv run python -m app.cli.accuracy_check            # 3 random docs
    uv run python -m app.cli.accuracy_check -n 5 --seed 42
    uv run python -m app.cli.accuracy_check --docs-dir /some/dir
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF

from app.cli import accuracy as acc
from app.cli.evaluate_samples import REPORT_DIR, _resolve_docs_dir
from app.pipeline.assemble import assemble
from app.store.artifact_store import ArtifactStore, compute_key
from app.store.documents import discover_pdf_paths
from canonical_schema import CanonicalEdition, Node
from app.version import PIPELINE_VERSION

log = logging.getLogger("accuracy_check")

REPEAT_MIN_PAGES = 3          # a line on >= this many pages is a running header/footer
GENUINE_MISS_COVERAGE = 0.5   # a content line below this page-coverage is a real miss


def _source_lines(page: "fitz.Page") -> list[acc.SourceLine]:
    lines: list[acc.SourceLine] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = " ".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            direction = line.get("dir", (1.0, 0.0))
            horizontal = direction[0] > 0.7 and abs(direction[1]) < 0.3
            lines.append(acc.SourceLine(text=text, y0=y0, y1=y1, x0=x0, horizontal=horizontal))
    return lines


def _iter_nodes(node: Node):
    yield node
    for c in node.children:
        yield from _iter_nodes(c)


def _extracted_by_page(edition: CanonicalEdition) -> dict[int, list[str]]:
    by_page: dict[int, list[str]] = {}
    for n in _iter_nodes(edition.root):
        page = n.provenance.page
        if n.text:
            by_page.setdefault(page, []).append(n.text)
        if n.latex:
            by_page.setdefault(page, []).append(n.latex)
        # Attribute each cell to its OWN source page, not the (possibly
        # stitched) table node's first-page provenance -- a continuation
        # table's later-page cells would otherwise count as misses on those
        # pages. Falls back to the node page for cells with no provenance.
        for c in (n.cells or []):
            if c.text:
                by_page.setdefault(c.page or page, []).append(c.text)
    return by_page


def _extracted_node_y_order(edition: CanonicalEdition, page: int) -> list[float]:
    """y0 of each extracted node on the page, in tree (reading) order -- for
    the reading-order tau vs source top-to-bottom."""
    ys = []
    for n in _iter_nodes(edition.root):
        if n.provenance.page == page and (n.text or n.cells):
            ys.append(n.provenance.bbox[1])
    return ys


def _load_edition(pdf_bytes: bytes, store: ArtifactStore) -> CanonicalEdition:
    key = compute_key(pdf_bytes, PIPELINE_VERSION)
    cached = store.get_edition(key)
    if cached is not None:
        return cached
    return assemble(pdf_bytes, source_sha256=hashlib.sha256(pdf_bytes).hexdigest())


def check_document(path: Path, store: ArtifactStore) -> acc.DocAccuracy:
    pdf_bytes = path.read_bytes()
    edition = _load_edition(pdf_bytes, store)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        pages_lines = [_source_lines(doc[i]) for i in range(doc.page_count)]
        repeated = acc.find_repeated_lines(pages_lines, REPEAT_MIN_PAGES)
        extracted = _extracted_by_page(edition)

        result = acc.DocAccuracy(filename=path.name, pages=doc.page_count)
        coverages, taus, num_found, num_total = [], [], 0, 0

        for i in range(doc.page_count):
            page_no = i + 1
            h = doc[i].rect.height
            lines = pages_lines[i]

            ext_text = " ".join(extracted.get(page_no, []))
            ext_words = acc.content_words(ext_text)
            ext_nums = acc.numeric_tokens(ext_text)

            src_words: set[str] = set()
            genuine_misses: list[str] = []
            furniture = 0
            src_nums: set[str] = set()
            for line in lines:
                if acc.is_furniture(line, h, repeated):
                    furniture += 1
                    continue
                lw = acc.content_words(line.text)
                src_words |= lw
                src_nums |= acc.numeric_tokens(line.text)
                # per-line genuine-miss: most of this body line's words absent
                if lw:
                    found = len(lw & ext_words) / len(lw)
                    if found < GENUINE_MISS_COVERAGE:
                        genuine_misses.append(line.text.strip()[:80])

            cov, _ = acc.token_coverage(src_words, ext_words)
            nfound = len(src_nums & ext_nums)
            tau = acc.reading_order_tau(_extracted_node_y_order(edition, page_no))

            pa = acc.PageAccuracy(
                page=page_no, source_content_words=len(src_words), coverage=round(cov, 3),
                genuine_misses=genuine_misses, numeric_total=len(src_nums),
                numeric_found=nfound, reading_order_tau=round(tau, 3), furniture_lines=furniture,
            )
            result.per_page.append(vars(pa))
            coverages.append(cov)
            taus.append(tau)
            num_found += nfound
            num_total += len(src_nums)

        result.mean_coverage = round(sum(coverages) / len(coverages), 3) if coverages else 1.0
        result.min_coverage = round(min(coverages), 3) if coverages else 1.0
        result.numeric_fidelity = round(num_found / num_total, 3) if num_total else 1.0
        result.mean_reading_order_tau = round(sum(taus) / len(taus), 3) if taus else 1.0
        result.total_genuine_misses = sum(len(p["genuine_misses"]) for p in result.per_page)
        result.worst_pages = sorted(
            (p for p in result.per_page if p["genuine_misses"]),
            key=lambda p: p["coverage"])[:5]
        return result
    finally:
        doc.close()


def _print_summary(results: list[acc.DocAccuracy]) -> None:
    for r in results:
        print(f"\n=== {r.filename} ({r.pages}p) ===")
        print(f"  coverage mean={r.mean_coverage} min={r.min_coverage}  "
              f"numeric_fidelity={r.numeric_fidelity}  reading_order_tau={r.mean_reading_order_tau}")
        print(f"  genuine content misses: {r.total_genuine_misses}")
        for p in r.worst_pages:
            print(f"    p{p['page']} cov={p['coverage']} tau={p['reading_order_tau']} "
                  f"misses={p['genuine_misses'][:4]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--num", type=int, default=3)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--docs-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    docs_dir = _resolve_docs_dir(args.docs_dir)
    if docs_dir is None:
        log.error("no accessible sample PDFs (see app/cli/evaluate_samples.py resolution order)")
        return 2

    seed = args.seed if args.seed is not None else random.randrange(2**31)
    all_pdfs = discover_pdf_paths(docs_dir)
    sampled = random.Random(seed).sample(all_pdfs, min(args.num, len(all_pdfs)))
    log.info("accuracy-checking %d docs (seed=%d): %s", len(sampled), seed, [p.name for p in sampled])

    import os
    store = ArtifactStore(os.environ.get("ARTIFACT_STORE_PATH", "./data/artifacts"))
    results = []
    for i, path in enumerate(sampled, 1):
        log.info("[%d/%d] %s", i, len(sampled), path.name)
        results.append(check_document(path, store))

    _print_summary(results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = REPORT_DIR / f"accuracy-{ts}.json"
    out.write_text(json.dumps({"seed": seed, "docs_dir": str(docs_dir),
                               "documents": [r.to_dict() for r in results]}, indent=2))
    log.info("wrote %s (replay with --seed %d)", out, seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
