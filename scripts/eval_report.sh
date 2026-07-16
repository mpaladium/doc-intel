#!/usr/bin/env bash
# Combined benchmark + accuracy + verify_extraction report over N random real
# PDFs: draws one sample, runs the structural benchmark, the factual-accuracy
# scorecard, and the verify_extraction.py admission-gate CLI against the SAME
# cached editions, and writes a timestamped JSON+Markdown report to
# data/eval-reports/ plus a committed docs/EVAL_REPORT.md. See
# app/cli/eval_report.py.
#
# Usage:
#   ./scripts/eval_report.sh                 # 3 random docs
#   EVAL_N=5 ./scripts/eval_report.sh
#   ./scripts/eval_report.sh --seed 7 --docs-dir data/eval-samples
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${EVAL_N:=3}"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
fi

CMD=(uv run python -m app.cli.eval_report -n "${EVAL_N}")
if [[ $# -gt 0 ]]; then
  CMD+=("$@")
fi
exec "${CMD[@]}"
