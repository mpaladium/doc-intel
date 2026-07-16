#!/usr/bin/env python3
"""Run the verification gates (app/pipeline/gates) over a CanonicalEdition JSON.

This is the CI admission gate, not just a scorecard: it exits non-zero when any
object is quarantined, so a build that ingests a document producing a
±/superscript/merged-cell corruption fails loudly instead of shipping a wrong
limit. Gates are deterministic and need no model weights.

Exit codes:
    0  clean -- nothing quarantined
    1  one or more objects quarantined (a review queue exists)
    2  a document-level extraction alarm (unresolved internal cross-reference)

Usage:
    uv run python scripts/verify_extraction.py path/to/edition.json
    uv run python scripts/verify_extraction.py edition.json --json

A suspiciously clean run on a hard document (scanned, multilingual, many pages)
is itself a symptom -- the tail note flags it (verification-rules.md
"Quarantine is not failure").
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

# Make the package importable when run as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from canonical_schema import CanonicalEdition  # noqa: E402
from app.pipeline import gates  # noqa: E402


def _count_objects(node) -> int:
    return 1 + sum(_count_objects(c) for c in node.children)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edition", help="path to a CanonicalEdition JSON file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    edition = CanonicalEdition.model_validate_json(Path(args.edition).read_text("utf-8"))
    report = gates.run_all(edition.root)

    n_objects = _count_objects(report.root)
    quarantines = report.quarantined
    repairs = report.repaired
    # A cross-reference quarantine is the document-level alarm (exit 2).
    alarm = any(o.gate == "cross_reference" for o in quarantines)

    if args.json:
        print(json.dumps({
            "objects_checked": n_objects,
            "quarantined": len(quarantines),
            "repaired": len(repairs),
            "alarm": alarm,
            "outcomes": [
                {"gate": o.gate, "object_id": o.object_id,
                 "verdict": o.verdict, "reason": o.reason}
                for o in report.outcomes],
        }, indent=2, ensure_ascii=False))
    else:
        by_gate: Counter[str] = Counter(o.gate for o in report.outcomes)
        for o in report.outcomes:
            print(f"  [{o.verdict:10}] {o.gate:20} {o.object_id}: {o.reason}")
        print(f"\n{n_objects} objects | {len(quarantines)} quarantined | "
              f"{len(repairs)} repaired")
        if by_gate:
            print("by gate: " + ", ".join(f"{g}={n}" for g, n in by_gate.most_common()))
        if not quarantines and n_objects > 500:
            print("NOTE: zero quarantines on a large document -- verify the pipeline "
                  "is actually checking rather than assuming success.")

    if alarm:
        return 2
    return 1 if quarantines else 0


if __name__ == "__main__":
    sys.exit(main())
