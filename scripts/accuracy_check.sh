#!/usr/bin/env bash
# Per-page factual-accuracy check over N random real PDFs: compares each
# extracted CanonicalEdition against the source PDF's own text layer and
# reports faithfulness per page + per component (coverage, numeric fidelity,
# reading order, genuine content misses). See app/cli/accuracy_check.py.
#
# Reuses cached artifacts under ARTIFACT_STORE_PATH when present, else runs
# the pipeline on the fly. Sample dir resolves like eval_samples.sh.
#
# Usage:
#   ./scripts/accuracy_check.sh                 # 3 random docs
#   EVAL_N=5 ./scripts/accuracy_check.sh
#   ./scripts/accuracy_check.sh --seed 7        # reproducible sample
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${EVAL_N:=3}"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
fi

CMD=(uv run python -m app.cli.accuracy_check -n "${EVAL_N}")
if [[ $# -gt 0 ]]; then
  CMD+=("$@")
fi
exec "${CMD[@]}"
