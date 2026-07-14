"""Section-role gold-set runner -- the Goal-1 merge gate (ARCHITECTURE.md §2.3
/ §7 step 3): the classifier's **false-exclusion rate on real normative
sections must be ≈ 0**. A missed TOC/foreword is acceptable noise; a wrongly
excluded normative clause silently drops compliance evidence -- the worst
failure mode this system has.

Gold labels live in `tests/goldsets/section_roles/*.yaml`, one file per real
sample document (EN + DE -- this is the multilingual gold set), hand-labeled
against the source PDF:

    doc: DIN_EN_60068-2-64-2009_p001-012.pdf
    sections:
      - match: "deutsche norm"          # normalized substring of the heading
        compliance_relevant: false       # what a compliance reviewer would say
        role: title_page                 # optional: expected SectionRole

Matching is normalized-substring against the extracted top-level sections
(role classification operates on top-level sections, so labels do too). A gold
entry no extracted section matches is reported as `unmatched` -- an extraction
gap, never silently dropped.

Exit codes: 0 = gate passes (zero false exclusions), 1 = gate fails,
2 = setup problem (missing docs/goldsets).

Usage:
    uv run python -m app.cli.section_role_gold
    uv run python -m app.cli.section_role_gold --goldset-dir tests/goldsets/section_roles
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.cli.evaluate_samples import _resolve_docs_dir
from app.pipeline.assemble import assemble
from app.store.artifact_store import ArtifactStore, compute_key
from app.version import PIPELINE_VERSION
from canonical_schema import CanonicalEdition, Node

log = logging.getLogger("section_role_gold")

GOLDSET_DIR = Path(__file__).parents[2] / "tests" / "goldsets" / "section_roles"


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.strip().casefold())
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).split())


@dataclass
class GoldResult:
    doc: str
    entries: int = 0
    false_exclusions: list[str] = field(default_factory=list)   # THE gate
    false_inclusions: list[str] = field(default_factory=list)   # acceptable noise
    role_mismatches: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)


def _load_edition(pdf_path: Path, store: ArtifactStore) -> CanonicalEdition:
    pdf_bytes = pdf_path.read_bytes()
    key = compute_key(pdf_bytes, PIPELINE_VERSION)
    cached = store.get_edition(key)
    if cached is not None:
        return cached
    edition = assemble(pdf_bytes, source_sha256=hashlib.sha256(pdf_bytes).hexdigest())
    store.put_edition(key, edition)
    return edition


def _top_sections(edition: CanonicalEdition) -> list[Node]:
    return [n for n in edition.root.children if n.type == "section"]


def check_gold_doc(gold: dict, docs_dir: Path, store: ArtifactStore) -> GoldResult:
    result = GoldResult(doc=gold["doc"])
    edition = _load_edition(docs_dir / gold["doc"], store)
    sections = _top_sections(edition)

    for entry in gold["sections"]:
        result.entries += 1
        want_match = _norm(entry["match"])
        matched = [s for s in sections if s.text and want_match in _norm(s.text)]
        if not matched:
            result.unmatched.append(entry["match"])
            continue
        # If several sections match the string, all must satisfy the label
        # (labels are written to be unambiguous; over-matching shows up here).
        for sec in matched:
            want_relevant = bool(entry["compliance_relevant"])
            got_relevant = sec.compliance_relevant
            desc = f"{entry['match']!r} -> {sec.text[:50]!r} (role={sec.section_role})"
            if want_relevant and not got_relevant:
                result.false_exclusions.append(desc)
            elif not want_relevant and got_relevant:
                result.false_inclusions.append(desc)
            want_role = entry.get("role")
            if want_role and sec.section_role != want_role:
                result.role_mismatches.append(f"{desc} expected role={want_role}")
    return result


def run(goldset_dir: Path, docs_dir: Path, store: ArtifactStore) -> tuple[list[GoldResult], int]:
    gold_files = sorted(goldset_dir.glob("*.yaml"))
    if not gold_files:
        log.error("no gold files under %s", goldset_dir)
        return [], 2

    results = []
    for gf in gold_files:
        gold = yaml.safe_load(gf.read_text(encoding="utf-8"))
        if not (docs_dir / gold["doc"]).exists():
            log.warning("gold doc missing from sample dir, skipping: %s", gold["doc"])
            continue
        log.info("checking %s (%d labeled sections)", gold["doc"], len(gold["sections"]))
        results.append(check_gold_doc(gold, docs_dir, store))

    total_fx = sum(len(r.false_exclusions) for r in results)
    total_entries = sum(r.entries for r in results)
    print(f"\n===== SECTION-ROLE GOLD SET ({len(results)} docs, {total_entries} labeled sections) =====")
    for r in results:
        status = "FAIL" if r.false_exclusions else "ok"
        print(f"  [{status}] {r.doc}: entries={r.entries} "
              f"false_excl={len(r.false_exclusions)} false_incl={len(r.false_inclusions)} "
              f"role_mism={len(r.role_mismatches)} unmatched={len(r.unmatched)}")
        for d in r.false_exclusions:
            print(f"      FALSE EXCLUSION: {d}")
        for d in r.role_mismatches:
            print(f"      role mismatch:  {d}")
        for d in r.unmatched:
            print(f"      unmatched gold entry: {d!r}")
    fx_rate = total_fx / total_entries if total_entries else 0.0
    print(f"  GATE  false-exclusion rate: {total_fx}/{total_entries} = {fx_rate:.4f} "
          f"({'PASS' if total_fx == 0 else 'FAIL — normative content would be dropped'})")
    return results, (0 if total_fx == 0 else 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--goldset-dir", type=Path, default=GOLDSET_DIR)
    parser.add_argument("--docs-dir", type=Path, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    docs_dir = _resolve_docs_dir(args.docs_dir)
    if docs_dir is None:
        log.error("no accessible sample PDFs")
        return 2
    store = ArtifactStore(os.environ.get("ARTIFACT_STORE_PATH", "./data/artifacts"))
    _, code = run(args.goldset_dir, docs_dir, store)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
