"""eval_report -- one combined benchmark + accuracy + verify_extraction report
over N random PDFs from the sample directory.

Draws the sample ONCE (unlike running `evaluate_samples.py` and
`accuracy_check.py` separately, which each do their own random draw and only
land on the same files if given the same seed by coincidence), then for each
document:

  1. runs the structural benchmark (`eval_metrics.compute_metrics` -- tables,
     nesting, multilingual, formulas, plus the Phase 1-6 signal: gate
     quarantine/repair counts, CDM type distribution, Parameter counts, runs
     coverage);
  2. runs the factual-accuracy scorecard against the PDF's own text layer
     (`accuracy_check.check_document` -- paragraph coverage, heading recall,
     caption attachment, table-region fidelity);
  3. runs `scripts/verify_extraction.py` as a subprocess against the cached
     edition.json (the actual CI admission-gate CLI, not a re-implementation
     of it) and records its exit code + findings.

All three read the SAME cached `CanonicalEdition` (the `ArtifactStore`, keyed
by sha256(pdf)+PIPELINE_VERSION) -- `assemble()` runs exactly once per
document, not three times.

Writes a timestamped JSON + Markdown report to `data/eval-reports/`, and
overwrites the committed `docs/EVAL_REPORT.md` with the latest run so the
benchmark is visible in the repo rather than only in a git-ignored directory.

Usage:
    uv run python -m app.cli.eval_report                     # 3 random docs
    uv run python -m app.cli.eval_report -n 5 --seed 42
    uv run python -m app.cli.eval_report --docs-dir data/eval-samples
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.cli.accuracy_check import check_document
from app.cli.eval_metrics import DocMetrics, compute_metrics
from app.cli.evaluate_samples import REPORT_DIR, _resolve_docs_dir
from app.pipeline import quarantine
from app.store.artifact_store import ArtifactStore, compute_key
from app.store.documents import discover_pdf_paths
from app.version import PIPELINE_VERSION

log = logging.getLogger("eval_report")

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_extraction.py"
DOC_REPORT_PATH = REPO_ROOT / "docs" / "EVAL_REPORT.md"
_EXIT_LABELS = {0: "clean", 1: "quarantined objects", 2: "document-level alarm"}


def _run_verify_extraction(edition_path: Path) -> dict:
    """Shells out to the real CLI (not a re-implementation) so the report
    reflects exactly what a CI runner invoking this script would see."""
    proc = subprocess.run(
        [sys.executable, str(VERIFY_SCRIPT), str(edition_path), "--json"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    result = {"exit_code": proc.returncode}
    try:
        result.update(json.loads(proc.stdout))
    except json.JSONDecodeError:
        result["stdout"] = proc.stdout
        result["stderr"] = proc.stderr
    return result


def evaluate_one(path: Path, store: ArtifactStore) -> dict:
    pdf_bytes = path.read_bytes()
    q = quarantine.check(pdf_bytes)
    if not q.ok:
        return {"filename": path.name, "status": f"quarantined:{q.cause}"}

    accuracy = check_document(path, store)  # the only assemble() call for this doc
    key = compute_key(pdf_bytes, PIPELINE_VERSION)
    edition = store.get_edition(key)
    benchmark = compute_metrics(path.name, edition)
    edition_path = store.edition_path(key)
    verify = _run_verify_extraction(edition_path) if edition_path else {"exit_code": None}

    return {
        "filename": path.name,
        "status": "processed",
        "benchmark": benchmark.to_dict(),
        "accuracy": accuracy.to_dict(),
        "verify_extraction": verify,
    }


def run(docs_dir: Path, n: int, seed: int, store: ArtifactStore) -> list[dict]:
    all_pdfs = discover_pdf_paths(docs_dir)
    sampled = random.Random(seed).sample(all_pdfs, min(n, len(all_pdfs)))
    log.info("sampling %d of %d PDFs from %s (seed=%d): %s",
              len(sampled), len(all_pdfs), docs_dir, seed, [p.name for p in sampled])

    results = []
    for i, path in enumerate(sampled, start=1):
        t0 = time.time()
        try:
            r = evaluate_one(path, store)
        except Exception:
            log.exception("[%d/%d] FAILED: %s", i, len(sampled), path.name)
            r = {"filename": path.name, "status": "failed"}
        log.info("[%d/%d] %s -> %s (%.1fs)", i, len(sampled), path.name, r["status"], time.time() - t0)
        results.append(r)
    return results


def _rollup(results: list[dict]) -> dict:
    """Corpus-level pooled numerators/denominators, mirroring
    accuracy_check._print_scorecard's pooling but returned as data."""
    processed = [r for r in results if r["status"] == "processed"]
    if not processed:
        return {}

    def pool(section: str, num_attr: str, den_attr: str) -> tuple[int, int, float]:
        num = sum(r[section][num_attr] for r in processed)
        den = sum(r[section][den_attr] for r in processed)
        return num, den, round(num / den, 4) if den else 1.0

    n_pages = sum(r["accuracy"]["pages"] for r in processed)
    mean_cov = (round(sum(r["accuracy"]["mean_coverage"] * r["accuracy"]["pages"]
                          for r in processed) / n_pages, 4) if n_pages else 1.0)

    return {
        "documents": len(processed),
        "pages": n_pages,
        "paragraph_coverage_mean": mean_cov,
        "heading_recall": pool("accuracy", "heading_matched", "heading_candidates"),
        "formula_recall": pool("accuracy", "formula_pages_with_equation", "formula_pages"),
        "caption_attachment": pool("accuracy", "captions_attached", "captions_total"),
        "table_region_fidelity": pool("accuracy", "table_region_found", "table_region_tokens"),
        "gates_quarantined_total": sum(r["benchmark"]["gates_quarantined"] for r in processed),
        "gates_repaired_total": sum(r["benchmark"]["gates_repaired"] for r in processed),
        "verify_extraction_alarms": sum(1 for r in processed if r["verify_extraction"].get("alarm")),
        "verify_extraction_clean": sum(1 for r in processed
                                       if r["verify_extraction"].get("exit_code") == 0),
    }


def _fmt_pool(p: tuple[int, int, float]) -> str:
    return f"{p[0]}/{p[1]} ({p[2]})"


_MAX_REASON_LEN = 200


def _truncate_reason(reason: str) -> str:
    """A node carrying many Parameters (e.g. a dense limit table row) can
    concatenate one clause per parameter into a single gate reason -- readable
    in the full JSON report, but a wall of repeated text in the Markdown
    summary. Cap the DISPLAYED string only; the JSON report keeps the full
    reason untruncated."""
    if len(reason) <= _MAX_REASON_LEN:
        return reason
    return reason[:_MAX_REASON_LEN] + f"... [{len(reason) - _MAX_REASON_LEN} more chars]"


def render_markdown(docs_dir: Path, seed: int, results: list[dict], rollup: dict) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# ingestion-engine eval report",
        "",
        f"Generated {ts} · pipeline_version `{PIPELINE_VERSION}` · "
        f"sample dir `{docs_dir}` · seed `{seed}`",
        "",
        "Combines the structural benchmark (`app/cli/evaluate_samples.py`), "
        "the factual-accuracy scorecard (`app/cli/accuracy_check.py`), and "
        "the verification-gate CI check (`scripts/verify_extraction.py`) over "
        "the same randomly sampled documents. Regenerate with "
        "`uv run python -m app.cli.eval_report`.",
        "",
    ]

    if rollup:
        lines += [
            "## Corpus rollup",
            "",
            f"- Documents: {rollup['documents']}  ·  Pages: {rollup['pages']}",
            f"- Paragraph coverage (page-weighted mean): {rollup['paragraph_coverage_mean']}",
            f"- Heading recall: {_fmt_pool(rollup['heading_recall'])}",
            f"- Formula page recall: {_fmt_pool(rollup['formula_recall'])}",
            f"- Caption attachment: {_fmt_pool(rollup['caption_attachment'])}",
            f"- Table-region fidelity: {_fmt_pool(rollup['table_region_fidelity'])}",
            f"- Gate quarantines / repairs (total): "
            f"{rollup['gates_quarantined_total']} / {rollup['gates_repaired_total']}",
            f"- `verify_extraction.py`: {rollup['verify_extraction_clean']}/"
            f"{rollup['documents']} clean (exit 0), "
            f"{rollup['verify_extraction_alarms']} document-level alarm(s) (exit 2)",
            "",
        ]

    for r in results:
        lines.append(f"## {r['filename']}")
        lines.append("")
        if r["status"] != "processed":
            lines.append(f"status: `{r['status']}`")
            lines.append("")
            continue

        b, a, v = r["benchmark"], r["accuracy"], r["verify_extraction"]
        lines += [
            f"**Status:** processed · {b['pages']} pages · "
            f"page classes: {b['page_class_counts']}",
            "",
            "**Benchmark (structure)**",
            f"- node types: {b['node_type_counts']} (max depth {b['max_depth']})",
            f"- tables: {b['tables']}, cells: {b['total_cells']} "
            f"({b['data_cells_with_header_path']}/{b['data_cells']} data cells with header_path)",
            f"- lists: {b['list_items']} (nested: {b['nested_list_items']})",
            f"- languages: {b['distinct_langs']} (primary: {b['lang_primary']}), "
            f"{b['lang_populated']}/{b['text_nodes']} text nodes tagged",
            f"- equations: {b['equation_nodes']} ({b['equation_nodes_with_latex']} with LaTeX)",
            f"- runs coverage: {b['runs_coverage']}  ·  CDM types: {b['cdm_type_counts']}  ·  "
            f"parameters extracted: {b['parameters_total']}",
            f"- consensus: {b['consensus_quarantined']} quarantined, "
            f"{b['consensus_majority']} majority (incl. table-geometry disagreements)",
            f"- review_required: {b['review_required']}  ·  mean confidence: {b['mean_confidence']}",
            "",
            "**Accuracy (vs. source text layer)**",
            f"- coverage: mean {a['mean_coverage']}, min {a['min_coverage']}  ·  "
            f"numeric fidelity: {a['numeric_fidelity']}  ·  "
            f"reading-order tau: {a['mean_reading_order_tau']}",
            f"- headings: {a['heading_matched']}/{a['heading_candidates']} "
            f"({a['heading_recall']})  ·  "
            f"captions attached: {a['captions_attached']}/{a['captions_total']} "
            f"({a['caption_attachment']})  ·  "
            f"table region: {a['table_region_found']}/{a['table_region_tokens']} "
            f"({a['table_region_fidelity']})",
            f"- genuine content misses: {a['total_genuine_misses']}",
            "",
            "**Verification gates (`verify_extraction.py`)**",
            f"- exit code: {v.get('exit_code')} ({_EXIT_LABELS.get(v.get('exit_code'), 'unknown')})",
            f"- objects checked: {v.get('objects_checked', '?')}  ·  "
            f"quarantined: {v.get('quarantined', '?')}  ·  repaired: {v.get('repaired', '?')}",
        ]
        outcomes = v.get("outcomes") or []
        if outcomes:
            lines.append("- example findings:")
            for o in outcomes[:5]:
                lines.append(f"  - `{o['gate']}` [{o['verdict']}] {o['object_id']}: "
                            f"{_truncate_reason(o['reason'])}")
            if len(outcomes) > 5:
                lines.append(f"  - ... {len(outcomes) - 5} more")
        lines.append("")

    return "\n".join(lines)


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
    store = ArtifactStore(os.environ.get("ARTIFACT_STORE_PATH", "./data/artifacts"))
    results = run(docs_dir, args.num, seed, store)
    rollup = _rollup(results)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    json_path = REPORT_DIR / f"eval-report-{ts}.json"
    json_path.write_text(json.dumps({
        "timestamp": ts, "pipeline_version": PIPELINE_VERSION, "docs_dir": str(docs_dir),
        "seed": seed, "rollup": rollup, "documents": results,
    }, indent=2, ensure_ascii=False))

    markdown = render_markdown(docs_dir, seed, results, rollup)
    md_path = REPORT_DIR / f"eval-report-{ts}.md"
    md_path.write_text(markdown)

    DOC_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_REPORT_PATH.write_text(markdown)

    log.info("wrote %s, %s, and %s (replay with --seed %d)",
             json_path, md_path, DOC_REPORT_PATH, seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
