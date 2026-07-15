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
import os
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
            spans = line["spans"]
            text = " ".join(s["text"] for s in spans).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            direction = line.get("dir", (1.0, 0.0))
            horizontal = direction[0] > 0.7 and abs(direction[1]) < 0.3
            max_size = max((s.get("size", 0.0) for s in spans), default=0.0)
            lines.append(acc.SourceLine(text=text, y0=y0, y1=y1, x0=x0, x1=x1,
                                        horizontal=horizontal, max_size=max_size))
    return lines


def _cell_rect_pymupdf(bbox: tuple[float, float, float, float], page_h: float):
    """Cell bbox -> PyMuPDF (top-left origin) rect, handling either origin
    convention Docling may emit (BOTTOMLEFT: t > b; TOPLEFT: t < b)."""
    l, t, r, b = bbox
    if t > b:  # bottom-left origin
        return (l, page_h - t, r, page_h - b)
    return (l, t, r, b)


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


def _component_metrics(edition: CanonicalEdition, doc: "fitz.Document",
                       pages_lines: list[list[acc.SourceLine]], repeated: set[str],
                       result: acc.DocAccuracy) -> None:
    """Per-component scorecard fields: heading recall, formula recall probe,
    caption attachment, table region fidelity ('TEDS-lite')."""
    page_heights = [doc[i].rect.height for i in range(doc.page_count)]

    # -- collect extracted structures once --
    all_headings: list[str] = []
    extracted_clause_ids: set[str] = set()
    equations_pages: set[int] = set()
    parent_of: dict[int, Node] = {}
    captions: list[Node] = []
    tables: list[Node] = []
    for n in _iter_nodes(edition.root):
        for c in n.children:
            parent_of[id(c)] = n
        # A source clause number is "matched" if extraction produced ANY node
        # with that clause_id -- section/heading, or a numbered list_item /
        # title paragraph (topology labels those too). Restricting to
        # section/heading would under-count the clause-enrichment fix.
        if n.clause_id:
            extracted_clause_ids.add(n.clause_id)
        if n.type in ("section", "heading"):
            if n.text:
                all_headings.append(n.text)
        elif n.type == "equation":
            equations_pages.add(n.provenance.page)
        elif n.type == "caption":
            captions.append(n)
        elif n.type == "table" and n.cells:
            tables.append(n)

    # -- clause-heading recall (clause-number-pattern source lines vs extraction) --
    for i, lines in enumerate(pages_lines):
        h = page_heights[i]
        for line in lines:
            if acc.is_furniture(line, h, repeated):
                continue
            cid = acc.clause_heading_candidate(line)
            if cid is None:
                continue
            result.heading_candidates += 1
            if acc.heading_matches(cid, line.text, extracted_clause_ids, all_headings):
                result.heading_matched += 1
    result.heading_recall = (round(result.heading_matched / result.heading_candidates, 3)
                             if result.heading_candidates else 1.0)

    # -- formula recall probe (strong math symbols -> expect an equation node) --
    for i, lines in enumerate(pages_lines):
        page_no, h = i + 1, page_heights[i]
        if any(acc.has_strong_math(l.text) for l in lines
               if not acc.is_furniture(l, h, repeated)):
            result.formula_pages += 1
            if page_no in equations_pages:
                result.formula_pages_with_equation += 1
    result.formula_recall = (round(result.formula_pages_with_equation / result.formula_pages, 3)
                             if result.formula_pages else 1.0)

    # -- caption attachment (caption is a child of its table/figure) --
    result.captions_total = len(captions)
    result.captions_attached = sum(
        1 for c in captions
        if (p := parent_of.get(id(c))) is not None and p.type in ("table", "figure"))
    result.caption_attachment = (round(result.captions_attached / result.captions_total, 3)
                                 if result.captions_total else 1.0)

    # -- table region fidelity ("TEDS-lite": source tokens inside the table's
    #    own cell-bbox region must appear in that table's cell texts) --
    for table in tables:
        by_page: dict[int, list] = {}
        for c in table.cells:
            if c.bbox and c.page:
                by_page.setdefault(c.page, []).append(c.bbox)
        cell_tokens = set()
        for c in table.cells:
            cell_tokens |= acc.content_words(c.text) | acc.numeric_tokens(c.text)
        for page_no, bboxes in by_page.items():
            if not (1 <= page_no <= doc.page_count):
                continue
            h = page_heights[page_no - 1]
            rects = [_cell_rect_pymupdf(b, h) for b in bboxes]
            rx0 = min(r[0] for r in rects); ry0 = min(r[1] for r in rects)
            rx1 = max(r[2] for r in rects); ry1 = max(r[3] for r in rects)
            for line in pages_lines[page_no - 1]:
                yc = (line.y0 + line.y1) / 2
                xc = (line.x0 + line.x1) / 2 if line.x1 else line.x0
                if rx0 - 2 <= xc <= rx1 + 2 and ry0 - 2 <= yc <= ry1 + 2:
                    toks = acc.content_words(line.text) | acc.numeric_tokens(line.text)
                    result.table_region_tokens += len(toks)
                    result.table_region_found += len(toks & cell_tokens)
    result.table_region_fidelity = (round(result.table_region_found / result.table_region_tokens, 3)
                                    if result.table_region_tokens else 1.0)


def _load_edition(pdf_bytes: bytes, store: ArtifactStore) -> CanonicalEdition:
    key = compute_key(pdf_bytes, PIPELINE_VERSION)
    cached = store.get_edition(key)
    if cached is not None:
        return cached
    edition = assemble(pdf_bytes, source_sha256=hashlib.sha256(pdf_bytes).hexdigest())
    store.put_edition(key, edition)  # content-addressed; reused by later runs
    return edition


def check_document(path: Path, store: ArtifactStore, gold_source: Path | None = None) -> acc.DocAccuracy:
    """Runs/loads the edition for `path` and scores it against a source text
    layer. Normally that's `path`'s own text layer; `gold_source` overrides
    this to score a text-layer-free scanned copy against the ORIGINAL
    born-digital PDF it was rasterized from (see
    tests/fixtures/make_scanned_pdf.py) -- free, exact OCR ground truth
    instead of hand labels."""
    pdf_bytes = path.read_bytes()
    edition = _load_edition(pdf_bytes, store)
    text_source_bytes = gold_source.read_bytes() if gold_source else pdf_bytes
    doc = fitz.open(stream=text_source_bytes, filetype="pdf")
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

        _component_metrics(edition, doc, pages_lines, repeated, result)
        return result
    finally:
        doc.close()


def _print_summary(results: list[acc.DocAccuracy]) -> None:
    for r in results:
        print(f"\n=== {r.filename} ({r.pages}p) ===")
        print(f"  coverage mean={r.mean_coverage} min={r.min_coverage}  "
              f"numeric_fidelity={r.numeric_fidelity}  reading_order_tau={r.mean_reading_order_tau}")
        print(f"  headings {r.heading_matched}/{r.heading_candidates} ({r.heading_recall})  "
              f"formulas {r.formula_pages_with_equation}/{r.formula_pages} ({r.formula_recall})  "
              f"captions attached {r.captions_attached}/{r.captions_total} ({r.caption_attachment})  "
              f"table_region {r.table_region_found}/{r.table_region_tokens} ({r.table_region_fidelity})")
        print(f"  genuine content misses: {r.total_genuine_misses}")
        for p in r.worst_pages:
            print(f"    p{p['page']} cov={p['coverage']} tau={p['reading_order_tau']} "
                  f"misses={p['genuine_misses'][:4]}")


def _print_scorecard(results: list[acc.DocAccuracy]) -> None:
    """Corpus aggregate: pooled numerators/denominators per component -- the
    '98% per component' scorecard."""
    def pool(num_attr: str, den_attr: str) -> tuple[int, int, float]:
        num = sum(getattr(r, num_attr) for r in results)
        den = sum(getattr(r, den_attr) for r in results)
        return num, den, round(num / den, 4) if den else 1.0

    n_pages = sum(r.pages for r in results)
    mean_cov = round(sum(r.mean_coverage * r.pages for r in results) / n_pages, 4) if n_pages else 1.0
    hd = pool("heading_matched", "heading_candidates")
    fm = pool("formula_pages_with_equation", "formula_pages")
    cp = pool("captions_attached", "captions_total")
    tb = pool("table_region_found", "table_region_tokens")
    misses = sum(r.total_genuine_misses for r in results)
    print(f"\n========== CORPUS SCORECARD ({len(results)} docs, {n_pages} pages) ==========")
    print(f"  paragraph/text coverage (page-weighted mean): {mean_cov}")
    print(f"  headings recall:        {hd[0]}/{hd[1]} = {hd[2]}")
    print(f"  formula page recall:    {fm[0]}/{fm[1]} = {fm[2]}")
    print(f"  caption attachment:     {cp[0]}/{cp[1]} = {cp[2]}")
    print(f"  table region fidelity:  {tb[0]}/{tb[1]} = {tb[2]}")
    print(f"  genuine content misses: {misses} across corpus")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--num", type=int, default=3)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--docs-dir", type=Path, default=None)
    parser.add_argument("--all", action="store_true",
                         help="check every PDF in the sample dir (corpus scorecard)")
    parser.add_argument("--doc", type=Path, default=None,
                         help="check a single PDF (pairs with --gold-source for OCR validation)")
    parser.add_argument("--gold-source", type=Path, default=None,
                         help="score --doc's extraction against THIS PDF's text layer instead of "
                              "--doc's own (e.g. the original PDF a scanned copy was rasterized "
                              "from -- free OCR ground truth, see tests/fixtures/make_scanned_pdf.py)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = ArtifactStore(os.environ.get("ARTIFACT_STORE_PATH", "./data/artifacts"))

    if args.doc is not None:
        log.info("accuracy-checking single doc: %s%s", args.doc,
                 f"  (scored against {args.gold_source})" if args.gold_source else "")
        result = check_document(args.doc, store, gold_source=args.gold_source)
        _print_summary([result])
        _print_scorecard([result])
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = REPORT_DIR / f"accuracy-single-{ts}.json"
        out.write_text(json.dumps({"doc": str(args.doc), "gold_source": str(args.gold_source)
                                   if args.gold_source else None, "documents": [result.to_dict()]},
                                  indent=2))
        log.info("wrote %s", out)
        return 0

    docs_dir = _resolve_docs_dir(args.docs_dir)
    if docs_dir is None:
        log.error("no accessible sample PDFs (see app/cli/evaluate_samples.py resolution order)")
        return 2

    seed = args.seed if args.seed is not None else random.randrange(2**31)
    all_pdfs = discover_pdf_paths(docs_dir)
    if args.all:
        sampled = all_pdfs
        log.info("accuracy-checking ALL %d docs in %s", len(sampled), docs_dir)
    else:
        sampled = random.Random(seed).sample(all_pdfs, min(args.num, len(all_pdfs)))
        log.info("accuracy-checking %d docs (seed=%d): %s", len(sampled), seed,
                 [p.name for p in sampled])

    results = []
    for i, path in enumerate(sampled, 1):
        log.info("[%d/%d] %s", i, len(sampled), path.name)
        results.append(check_document(path, store))

    _print_summary(results)
    _print_scorecard(results)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = REPORT_DIR / f"accuracy-{ts}.json"
    out.write_text(json.dumps({"seed": seed, "docs_dir": str(docs_dir),
                               "documents": [r.to_dict() for r in results]}, indent=2))
    log.info("wrote %s (replay with --seed %d)", out, seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
