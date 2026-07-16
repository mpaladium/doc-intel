"""Random-sampling evaluation harness: pick N random PDFs from a directory,
run the full pipeline on each, and emit an extraction-quality report across
the four target problem areas (multilingual, tables, deep nesting, formulas).

Fresh random selection every run by default (the RNG seed is logged so a bad
run can be replayed with `--seed`). Reports are written to
`data/eval-reports/` as JSON plus a readable stdout summary. No server
required -- calls `assemble()` directly.

Sample directory resolution (`--docs-dir` overrides all):
    1. $EVAL_DOCS_DIR
    2. /Users/navm/Documents/ECTest/Evaluation_sample/QuickSamples (the
       user's real sample set; note macOS may require granting the terminal
       Full Disk Access to read ~/Documents)
    3. ./data/eval-samples  (git-ignored fallback -- copy PDFs here if the
       Documents path isn't accessible)

Usage:
    uv run python -m app.cli.evaluate_samples            # 3 random docs
    uv run python -m app.cli.evaluate_samples -n 5 --seed 42
    uv run python -m app.cli.evaluate_samples --docs-dir /some/dir
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from app.cli.eval_metrics import DocMetrics, compute_metrics
from app.pipeline import quarantine
from app.pipeline.assemble import assemble
from app.store.documents import discover_pdf_paths
from app.version import PIPELINE_VERSION

log = logging.getLogger("evaluate_samples")

_DEFAULT_SAMPLE_DIRS = [
    "/Users/navm/Documents/ECTest/Evaluation_sample/QuickSamples",
    "./data/eval-samples",
]
REPORT_DIR = Path("./data/eval-reports")


def _resolve_docs_dir(explicit: Path | None) -> Path | None:
    """First accessible, non-empty candidate wins. `Path.is_dir()` alone is
    misleading under macOS TCC (it can return True while listing raises
    PermissionError), so each candidate is probed by actually listing it."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("EVAL_DOCS_DIR"):
        candidates.append(Path(os.environ["EVAL_DOCS_DIR"]))
    candidates.extend(Path(p) for p in _DEFAULT_SAMPLE_DIRS)

    for cand in candidates:
        try:
            pdfs = discover_pdf_paths(cand)
        except (PermissionError, OSError) as exc:
            log.warning("cannot read %s (%s)", cand, exc)
            continue
        if pdfs:
            return cand
        log.info("no PDFs under %s", cand)
    return None


def _evaluate_one(path: Path) -> DocMetrics:
    pdf_bytes = path.read_bytes()
    q = quarantine.check(pdf_bytes)
    if not q.ok:
        return DocMetrics(filename=path.name, status=f"quarantined:{q.cause}")
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    edition = assemble(pdf_bytes, source_sha256=sha, ocr_enabled=False)
    return compute_metrics(path.name, edition)


def _print_summary(metrics: list[DocMetrics]) -> None:
    for m in metrics:
        print(f"\n=== {m.filename}  [{m.status}] ===")
        if not m.status.startswith("processed"):
            continue
        print(f"  pages={m.pages} page_classes={m.page_class_counts} "
              f"uncertain_rate={m.uncertain_rate}")
        print(f"  node_types={m.node_type_counts} max_depth={m.max_depth}")
        print(f"  tables={m.tables} cells={m.total_cells} "
              f"data_cells_w_header_path={m.data_cells_with_header_path}/{m.data_cells} "
              f"max_span=({m.max_rowspan}x{m.max_colspan})")
        print(f"  lists={m.list_items} (nested={m.nested_list_items})")
        print(f"  lang: {m.lang_populated}/{m.text_nodes} text nodes tagged, "
              f"langs={m.distinct_langs} primary={m.lang_primary} "
              f"non_nfc={m.non_nfc_text_nodes}")
        print(f"  equations={m.equation_nodes} (with_latex={m.equation_nodes_with_latex})")
        print(f"  review_required={m.review_required} mean_conf={m.mean_confidence}")
        print(f"  runs_coverage={m.runs_coverage} cdm_types={m.cdm_type_counts} "
              f"parameters={m.parameters_total}")
        print(f"  gates: quarantined={m.gates_quarantined} repaired={m.gates_repaired} "
              f"by_gate={m.gates_by_gate}")
        print(f"  consensus: quarantined={m.consensus_quarantined} majority={m.consensus_majority}")


def run(docs_dir: Path, n: int, seed: int) -> list[DocMetrics]:
    all_pdfs = discover_pdf_paths(docs_dir)
    rng = random.Random(seed)
    sampled = rng.sample(all_pdfs, min(n, len(all_pdfs)))
    log.info("sampling %d of %d PDFs from %s (seed=%d): %s",
              len(sampled), len(all_pdfs), docs_dir, seed, [p.name for p in sampled])

    metrics: list[DocMetrics] = []
    for i, path in enumerate(sampled, start=1):
        t0 = time.time()
        try:
            m = _evaluate_one(path)
        except Exception:
            log.exception("[%d/%d] FAILED: %s", i, len(sampled), path.name)
            m = DocMetrics(filename=path.name, status="failed")
        log.info("[%d/%d] %s -> %s (%.1fs)", i, len(sampled), path.name, m.status, time.time() - t0)
        metrics.append(m)
    return metrics


def _write_report(docs_dir: Path, seed: int, metrics: list[DocMetrics]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = REPORT_DIR / f"eval-{ts}.json"
    path.write_text(json.dumps({
        "timestamp": ts,
        "pipeline_version": PIPELINE_VERSION,
        "docs_dir": str(docs_dir),
        "seed": seed,
        "documents": [m.to_dict() for m in metrics],
    }, indent=2))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--num", type=int, default=3, help="number of random PDFs (default 3)")
    parser.add_argument("--seed", type=int, default=None,
                         help="RNG seed for reproducible sampling (default: fresh random each run)")
    parser.add_argument("--docs-dir", type=Path, default=None, help="override sample directory")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    docs_dir = _resolve_docs_dir(args.docs_dir)
    if docs_dir is None:
        log.error(
            "no accessible sample PDFs found. Grant the terminal Full Disk Access to read "
            "~/Documents, set EVAL_DOCS_DIR, or copy PDFs into ./data/eval-samples/")
        return 2

    seed = args.seed if args.seed is not None else random.randrange(2**31)
    metrics = run(docs_dir, args.num, seed)
    _print_summary(metrics)
    report_path = _write_report(docs_dir, seed, metrics)
    log.info("wrote report: %s  (replay this sample with --seed %d)", report_path, seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
