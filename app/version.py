"""PIPELINE_VERSION -- aggregate identity of everything that can change extraction
output (AGENTS.md §6). Bump this whenever a pipeline stage, model, or rulepack
changes in a way that could produce different results for the same PDF -- it's
half of the artifact-store content-address key (sha256(pdf) + pipeline_version),
so bumping it is what forces re-processing instead of silently serving stale
output from an old pipeline version.

That rule applies to *runtime* engine selection too, not just code edits: an
env var that swaps a model changes extraction output for the same PDF, so it
has to be part of the version identity. Otherwise flipping it silently serves
editions built by the other model straight from the cache -- the exact
stale-output failure this key exists to prevent (observed with
INGESTION_TABLEFORMER before it was folded in here: a v1 run returned the
cached v2 editions and reported byte-identical metrics).

Kept dependency-free (stdlib only): this module is imported by nearly every
other one, including the artifact store, so it must not pull in docling/torch.
`app/pipeline/extract_docling.py` imports `tableformer_variant()` from here
rather than re-reading the env var, so the model actually built and the key it
is stored under can never disagree.
"""

import os

_BASE_PIPELINE_VERSION = "0.13.0"  # TableFormer V2 as the default table-structure model
SCHEMA_VERSION = "2.0"             # CDM v2 (docs/references/canonical-model.md)

_TABLEFORMER_V1_ALIASES = frozenset({"v1", "1", "tableformer", "legacy"})


def tableformer_variant() -> str:
    """`INGESTION_TABLEFORMER` (v2|v1, default v2) -> "v1" | "v2". Anything
    unrecognized falls forward to the default rather than failing extraction."""
    choice = os.environ.get("INGESTION_TABLEFORMER", "v2").strip().lower()
    return "v1" if choice in _TABLEFORMER_V1_ALIASES else "v2"


def _pipeline_version() -> str:
    """The base version, suffixed for any non-default engine selection so the
    two variants occupy separate content-address namespaces. Uses "-" (not "+")
    to keep exactly one "+" in `sha256(pdf)+pipeline_version` keys."""
    version = _BASE_PIPELINE_VERSION
    if tableformer_variant() == "v1":
        version += "-tfv1"
    return version


PIPELINE_VERSION = _pipeline_version()
