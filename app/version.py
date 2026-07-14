"""PIPELINE_VERSION -- aggregate identity of everything that can change extraction
output (AGENTS.md §6). Bump this whenever a pipeline stage, model, or rulepack
changes in a way that could produce different results for the same PDF -- it's
half of the artifact-store content-address key (sha256(pdf) + pipeline_version),
so bumping it is what forces re-processing instead of silently serving stale
output from an old pipeline version.
"""

PIPELINE_VERSION = "0.5.1"  # proximity-based caption attachment (caption_attach.py)
SCHEMA_VERSION = "1.0"
