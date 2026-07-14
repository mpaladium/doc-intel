#!/usr/bin/env bash
# Runs the random-sampling evaluation harness over N random real PDFs and
# writes a quality report to data/eval-reports/. Foreground by default (you
# usually want to read the summary); pass --background to detach it.
#
# Sample dir resolution: --docs-dir / $EVAL_DOCS_DIR / the QuickSamples path /
# ./data/eval-samples (see app/cli/evaluate_samples.py). If ~/Documents is
# blocked by macOS TCC, grant the terminal Full Disk Access or copy PDFs into
# ./data/eval-samples/.
#
# Usage:
#   ./scripts/eval_samples.sh                  # 3 random docs, foreground
#   EVAL_N=5 ./scripts/eval_samples.sh
#   ./scripts/eval_samples.sh --seed 42        # reproducible sample
#   ./scripts/eval_samples.sh --background
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

: "${EVAL_N:=3}"

background=false
passthru=()
for arg in "$@"; do
  if [[ "${arg}" == "--background" ]]; then
    background=true
  else
    passthru+=("${arg}")
  fi
done

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is not installed -- see https://docs.astral.sh/uv/" >&2
  exit 1
fi

CMD=(uv run python -m app.cli.evaluate_samples -n "${EVAL_N}")
if [[ ${#passthru[@]} -gt 0 ]]; then
  CMD+=("${passthru[@]}")
fi

if [[ "${background}" == "true" ]]; then
  log_dir="./data/eval-logs"
  mkdir -p "${log_dir}"
  ts=$(date +"%Y%m%d-%H%M%S")
  log_file="${log_dir}/eval-samples-${ts}.log"
  nohup "${CMD[@]}" > "${log_file}" 2>&1 &
  bg_pid=$!
  disown
  echo "[eval_samples] started in background, pid=${bg_pid}"
  echo "[eval_samples] tail:  tail -f ${log_file}"
else
  exec "${CMD[@]}"
fi
